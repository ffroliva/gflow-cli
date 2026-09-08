from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import TYPE_CHECKING

import structlog
from rich.console import Console

from gflow_cli.browser_manager import is_playwright_chrome_channel_available
from gflow_cli.config import Settings, get_settings
from gflow_cli.errors import (
    AuthBrowserRejectedError,
    AuthLoginTimeoutError,
    AuthMissingError,
    SecurityError,
)
from gflow_cli.profile_lease import ProfileLease

from .base import AuthStrategy
from .internal_chromium import login_launch_kwargs, poll_session_until_authenticated
from .verification import FlowSessionOutcome, verify_flow_profile

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

logger = structlog.get_logger(__name__)
_console = Console()

GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"

# User-facing guidance per non-authenticated verification outcome (issue #15).
_UNVERIFIED_MESSAGE: dict[FlowSessionOutcome, str] = {
    FlowSessionOutcome.GOOGLE_SESSION_ONLY: (
        "Signed in to your Google account, but the Flow app sign-in wasn't completed."
    ),
    FlowSessionOutcome.NO_SESSION: "No sign-in detected.",
    FlowSessionOutcome.VERIFICATION_ERROR: (
        "Could not verify the Flow session — this is often a network problem."
    ),
}
_UNVERIFIED_HINT: dict[FlowSessionOutcome, str] = {
    FlowSessionOutcome.GOOGLE_SESSION_ONLY: (
        "Re-run `gflow auth login` and continue until the Flow editor "
        "(the prompt box / your projects) loads."
    ),
    FlowSessionOutcome.NO_SESSION: (
        "Re-run `gflow auth login`, sign in to Google, and continue until the Flow editor loads."
    ),
    FlowSessionOutcome.VERIFICATION_ERROR: ("Check your connection and re-run `gflow auth login`."),
}


def _validate_profile_dir(profile_dir: Path, settings: Settings) -> None:
    """Raise SecurityError if profile_dir is outside GFLOW_CLI_HOME."""
    try:
        profile_dir.resolve(strict=False).relative_to(settings.home.resolve())
    except ValueError:
        msg = (
            f"Profile directory {profile_dir} is outside of GFLOW_CLI_HOME "
            f"({settings.home}) boundaries."
        )
        raise SecurityError(
            msg,
        ) from None


def _build_chrome_args(chrome_exe: str, profile_dir: Path, headless: bool) -> list[str]:
    """Build the Chrome command-line argument list for passive-capture login."""
    args = [
        chrome_exe,
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1920,1080",  # match the generation viewport (#315 consistency)
        "--password-store=basic",
        # No --remote-debugging-port: zero automation surface.
    ]
    if headless:
        args.append("--headless=new")
    # Open Flow directly (ONCE) so the user lands on the sign-in / app surface
    # instead of a blank new-tab page. A second GEMINI_URL positional made
    # Chrome open a duplicate Flow tab on every login.
    args.append(GEMINI_URL)
    return args


def _print_login_instructions() -> None:
    """Print the browser sign-in steps to the console."""
    _console.print("\n" + "=" * 60)
    _console.print("[bold cyan]BROWSER SIGN-IN[/bold cyan]")
    _console.print("=" * 60)
    _console.print("1. A Google Chrome window opens at the Flow sign-in page.")
    _console.print("2. Sign in with your Google account.")
    _console.print(
        "3. [bold yellow]Keep going until the Flow editor itself loads[/bold yellow] "
        "— the prompt box and your projects.",
    )
    _console.print(
        "   Signing in to Google is NOT enough; gflow needs a completed Flow app sign-in.",
    )
    _console.print(
        "4. That's it — gflow detects the sign-in and [bold]closes Chrome for you[/bold], "
        "then verifies the session.",
    )
    _console.print(
        "   Closing the window yourself still works; gflow verifies what's on disk either way.",
    )
    _console.print("-" * 60)
    _console.print("Launching Chrome...")


def _login_timeout_error(timeout_seconds: int) -> AuthLoginTimeoutError:
    """The timeout every login path raises when the window stays unauthenticated.

    Worded as a detection failure, not a user failure: the most likely cause is
    a Chrome that advertises automation (see ``_warn_if_webdriver_exposed``),
    and telling that user to "sign in faster" is the wrong advice.
    """
    msg = f"Flow sign-in not detected within {timeout_seconds}s; Chrome was stopped."
    return AuthLoginTimeoutError(
        msg,
        remediation_hint=(
            "Run `gflow auth login` again and complete sign-in before the time limit. "
            f"Set GFLOW_CLI_AUTH_LOGIN_TIMEOUT to raise the limit "
            f"(current: {timeout_seconds}s)."
        ),
    )


async def _warn_if_webdriver_exposed(page: Any, strategy_name: str) -> None:
    """Log loudly if this Chrome still advertises automation despite the flags.

    ``--disable-blink-features=AutomationControlled`` +
    ``ignore_default_args=["--enable-automation"]`` is what keeps
    ``navigator.webdriver`` false today. If a future Chrome ignores them,
    Google's G12 block returns and the only user-visible symptom is a silent
    600 s timeout — so make the real cause observable at launch instead.
    """
    try:
        exposed = bool(await page.evaluate("() => navigator.webdriver"))
    except Exception as exc:
        # Never fail a login over a diagnostic probe.
        logger.warning(
            "auth_login_webdriver_probe_failed",
            strategy=strategy_name,
            error=type(exc).__name__,
        )
        return
    if exposed:
        logger.warning("auth_login_webdriver_exposed", strategy=strategy_name)
        _console.print(
            "[yellow]Warning: this Chrome still reports navigator.webdriver — "
            "Google may reject the sign-in.[/yellow]",
        )


async def _terminate_and_reap(proc: asyncio.subprocess.Process) -> None:
    """Terminate the child, then kill+reap it if it doesn't exit promptly.

    Bounded so a wedged Chrome cannot hang teardown, and the final ``wait()``
    reaps the process so it never lingers as a zombie holding the profile lock.
    """
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (TimeoutError, asyncio.CancelledError):
        proc.kill()
        with contextlib.suppress(BaseException):
            await proc.wait()


async def _await_chrome_close(proc: asyncio.subprocess.Process, timeout_seconds: int) -> None:
    """Wait for Chrome to exit; terminate/kill it on timeout OR cancellation.

    On cancellation (Ctrl-C / task cancel while waiting for the user to close
    Chrome) the child would otherwise be orphaned — keeping the profile's
    SQLite cookie lock (and therefore the ProfileLease) held. Terminate + reap
    it before the cancellation propagates out through the enclosing
    ``async with ProfileLease`` (which releases the lease), so chrome is dead
    BEFORE the profile is freed.
    """
    try:
        await asyncio.wait_for(proc.wait(), timeout=float(timeout_seconds))
    except asyncio.CancelledError:
        await _terminate_and_reap(proc)
        raise
    except TimeoutError:
        await _terminate_and_reap(proc)
        raise _login_timeout_error(timeout_seconds) from None


def find_chrome_executable() -> str | None:
    """Find the system Google Chrome executable path."""
    if sys.platform == "win32":
        paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
    elif sys.platform == "darwin":
        paths = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]

    for p in paths:
        if os.path.exists(p):
            return p
    return None


class RealChromeStrategy(AuthStrategy):
    """Login strategy that drives the system's real Google Chrome.

    Two paths, no user-facing switch:

    * **Owned browser (default).** Playwright launches Chrome with
      ``channel="chrome"``, gflow watches the Flow session endpoint from the
      context it owns, and closes the window itself once sign-in completes.
      There IS an automation surface here, and that is fine: the spike of
      2026-09-08 measured that Google rejects a browser which *advertises*
      automation (``navigator.webdriver == True`` -> ``/v3/signin/rejected`` in
      17.5 s), not one that is merely driven. The stealth flags in
      :func:`login_launch_kwargs` are what keep that flag false, so they are
      load-bearing — see ``_warn_if_webdriver_exposed`` for the alarm.
    * **Bare subprocess (automatic fallback).** When Playwright cannot resolve
      a ``channel="chrome"`` binary, or Google rejects the owned browser
      anyway, Chrome is spawned as a plain child process with no debugging port
      and no automation flags, and the user closes it by hand. Nothing asks the
      user to choose; the fallback is silent apart from a log event.

    Either way the profile is verified afterwards by ``verify_flow_profile``,
    outside the lease, so the on-disk store is proven readable by the reader
    generation shares.
    """

    name = "chrome"

    def __init__(self, *, timeout_seconds: int = 600) -> None:
        # Maximum seconds to wait for the Flow sign-in to be detected.
        self._timeout_seconds = timeout_seconds

    async def login(self, profile_dir: Path, headless: bool) -> None:
        """Sign in to Flow in real Chrome, then verify what landed on disk."""
        settings = get_settings()
        _validate_profile_dir(profile_dir, settings)
        profile_dir.mkdir(parents=True, exist_ok=True)

        logger.info("auth_login_started", profile_dir=str(profile_dir), strategy=self.name)
        if not headless:
            _print_login_instructions()

        # `headless` never reaches the owned-browser path. The 2026-09-08 spike measured
        # three arms and every one was headed, so a headless Playwright sign-in is
        # unmeasured against Google's gate — and ACCOUNT_SAFETY.md records that headless
        # is rejected outright by reCAPTCHA Enterprise. The subprocess path already has a
        # `--headless=new` branch that predates this change, so routing there is both the
        # measured option and the smaller one. Not reachable from the CLI today
        # (`auth login` exposes no --headless); this guards library callers.
        if headless or not is_playwright_chrome_channel_available():
            fallback_reason = "headless" if headless else "channel_unavailable"
        else:
            fallback_reason = await self._login_owned_browser(profile_dir, headless)
        if fallback_reason is not None:
            logger.info(
                "auth_login_subprocess_fallback",
                strategy=self.name,
                reason=fallback_reason,
            )
            await self._login_subprocess(profile_dir, headless)

        await self._verify_and_record(profile_dir)

    async def _login_owned_browser(self, profile_dir: Path, headless: bool) -> str | None:
        """Drive the sign-in in a Chrome gflow owns, and close it when done.

        Returns ``None`` when this path handled the login, or the reason the
        caller must fall back to the subprocess path.
        """
        # Deferred imports: a top-level `from .strategies import ...` recreates
        # the strategies -> real_chrome cycle, and `gflow_cli.api` pulls the
        # whole transport stack (which imports auth.verification) at import time.
        from gflow_cli.api._engine import CONTEXT_TEARDOWN_TIMEOUT_S, close_context_bounded
        from gflow_cli.api._engine import run_teardown_step as _teardown_step

        from .strategies import async_playwright

        # Unwind order is pw -> lease, so the driver is stopped (and Chrome with
        # it) before the profile is freed for the next holder (D3).
        async with ProfileLease(profile_dir), async_playwright() as pw:
            try:
                ctx = await pw.chromium.launch_persistent_context(
                    **login_launch_kwargs(profile_dir, headless, channel="chrome"),
                )
            except Exception as exc:
                logger.warning(
                    "auth_login_launch_failed",
                    strategy=self.name,
                    error=type(exc).__name__,
                )
                return "launch_failed"

            rejected = False
            try:
                await self._await_flow_session(ctx)
            except AuthBrowserRejectedError:
                # Not the user's problem to solve: retry on the path that has
                # no automation surface at all, rather than surfacing exit 14.
                rejected = True
            finally:
                # Bounded + shielded (not a bare `await ctx.close()`): a
                # CancelledError landing inside the close must not skip the
                # driver stop or the lease release below, and a secondary
                # TargetClosedError must not mask the original exception.
                cancelled = await _teardown_step(
                    close_context_bounded(ctx, owner="auth_login"),
                    timeout=CONTEXT_TEARDOWN_TIMEOUT_S,
                    owner="auth_login",
                    step="context_close",
                )
            if cancelled is not None:
                # Re-raised inside the `async with`, so the driver still stops
                # and the lease still releases on the way out.
                raise cancelled
        return "browser_rejected" if rejected else None

    async def _await_flow_session(self, ctx: Any) -> None:
        """Wait for the Flow app sign-in on an owned context.

        Returns normally on success AND when the user closed the window first —
        ``verify_flow_profile`` is the authority in both cases, and three
        releases of docs told users to close the window themselves. Only a
        genuine timeout (window still open, still signed out) raises.
        """
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)
        await _warn_if_webdriver_exposed(page, self.name)

        started = asyncio.get_running_loop().time()
        try:
            # NEVER a cookie-name match: the cookie can be present while the
            # endpoint still rejects. The session endpoint is the oracle.
            email = await poll_session_until_authenticated(
                ctx,
                page,
                self._timeout_seconds,
                self.name,
                raise_on_close=False,
            )
        except AuthLoginTimeoutError:
            raise _login_timeout_error(self._timeout_seconds) from None
        if email is None:
            return
        logger.info(
            "auth_login_session_detected",
            strategy=self.name,
            elapsed_s=round(asyncio.get_running_loop().time() - started, 1),
        )
        _console.print("\n[bold green]Signed in.[/bold green] Closing Chrome...")
        # Let Chrome flush the cookie store to disk before the close — the
        # durability check that follows reads that store, not this context.
        await asyncio.sleep(1)

    async def _login_subprocess(self, profile_dir: Path, headless: bool) -> None:
        """Spawn Chrome as a plain child process and wait for the user to close it."""
        chrome_exe = find_chrome_executable()
        if not chrome_exe:
            msg = (
                "Google Chrome not found on system. "
                "Please install Chrome or use '--browser internal'."
            )
            raise RuntimeError(
                msg,
            )

        chrome_args = _build_chrome_args(chrome_exe, profile_dir, headless)

        # Own the profile while passive-capture Chrome runs (D3). The lease
        # scope is ONLY the running browser: it is released before
        # verify_flow_profile below, which momentarily owns its own probe context
        # on the same profile (a nested lease would be a same-process double-
        # acquire). Contention raises ProfileLockedError before Chrome launches.
        async with ProfileLease(profile_dir):
            # Tests MUST patch asyncio.create_subprocess_exec itself — patching
            # subprocess.Popen instead lets the real asyncio transport run against
            # a mock process, hanging proc.wait() forever on the loop's child
            # watcher.
            proc = await asyncio.create_subprocess_exec(
                *chrome_args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            # Chrome holds an exclusive lock on its SQLite cookie store while
            # running, so we must wait for it to close before probing the session.
            await _await_chrome_close(proc, self._timeout_seconds)
        _console.print("\n[bold green]Browser closed.[/bold green] Verifying Flow session...")

    async def _verify_and_record(self, profile_dir: Path) -> None:
        """Prove the on-disk profile carries a usable Flow session, and record it."""
        # Pre-write the Chrome marker so the verification fallback can use the
        # same channel gate if browser-cookie3 decryption fails. The marker must
        # exist DURING verification (the cookie-decrypt fallback reads it), so we
        # cannot defer the write until success. Instead, only the speculative
        # write is rolled back on failure: a marker that legitimately pre-existed
        # (a previously-verified chrome profile) must survive a transient probe
        # failure, or channel_for_profile would stop returning 'chrome' and
        # FlowApiClient would downgrade to bundled Chromium on a real-Chrome
        # profile (see [[real-browser-auth-mandatory]]).
        marker = profile_dir / ".gflow_browser_strategy"
        marker_preexisted = marker.exists()
        marker.write_text("chrome", encoding="utf-8")
        verified = False
        # Bound before the `try` so the `finally` can log it even when an
        # interrupt cuts the probe short and `status` never gets assigned.
        outcome: str | None = None
        try:
            status = await verify_flow_profile(profile_dir, source=self.name)
            verified = status.authenticated
            outcome = status.outcome.value
        finally:
            # `finally` (not `except`) so an interrupt — KeyboardInterrupt /
            # asyncio.CancelledError, both BaseException — also rolls back a
            # speculative write and never leaves an unverified profile claiming
            # the chrome strategy.
            if not verified and not marker_preexisted:
                marker.unlink(missing_ok=True)
                # #644: this rollback flips channel_for_profile away from
                # 'chrome', silently downgrading generation to bundled
                # Chromium. It used to emit nothing, so the first real
                # occurrence was only ever visible as a user report. `outcome`
                # separates "the probe endpoint was unreachable" (labs.google's
                # session BFF is the sole oracle, and the Flow frontend has
                # already migrated off that host) from "genuinely signed out".
                # It is an enum value — never response content.
                logger.warning(
                    "auth_chrome_marker_rolled_back",
                    strategy=self.name,
                    outcome=outcome,
                )
        if status.authenticated:
            logger.info(
                "auth_flow_session_verified",
                strategy=self.name,
                source=status.source,
                user_email=status.user_email,
            )
            # Marker read by browser_manager.channel_for_profile so FlowApiClient
            # selects the system Chrome channel. Load-bearing — must persist here.
            assert status.user_email, "AUTHENTICATED outcome must carry a non-empty user_email"
            (profile_dir / ".gflow_account").write_text(status.user_email, encoding="utf-8")
            _console.print(f"[green][OK] Flow session verified ({status.user_email}).[/green]")
        else:
            logger.warning(
                "auth_flow_session_unverified",
                strategy=self.name,
                outcome=status.outcome.value,
            )
            raise AuthMissingError(
                _UNVERIFIED_MESSAGE[status.outcome],
                remediation_hint=_UNVERIFIED_HINT[status.outcome],
            )
