from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import TYPE_CHECKING

import structlog
from rich.console import Console

from gflow_cli.config import Settings, get_settings
from gflow_cli.errors import AuthLoginTimeoutError, AuthMissingError, SecurityError
from gflow_cli.profile_lease import ProfileLease

from .base import AuthStrategy
from .verification import FlowSessionOutcome, verify_flow_profile

if TYPE_CHECKING:
    from pathlib import Path

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
        "(the prompt box / your projects) loads before closing Chrome."
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
    """Print the passive-capture login steps to the console."""
    _console.print("\n" + "=" * 60)
    _console.print("[bold cyan]PASSIVE AUTHENTICATION[/bold cyan]")
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
        "4. Then [bold yellow]CLOSE THE BROWSER[/bold yellow] — gflow verifies "
        "the Flow session automatically.",
    )
    _console.print("-" * 60)
    _console.print("Launching Chrome...")


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
        msg = f"Sign-in timed out after {timeout_seconds}s; Chrome was stopped."
        raise AuthLoginTimeoutError(
            msg,
            remediation_hint=(
                "Run `gflow auth login` again and complete sign-in before the time limit. "
                f"Set GFLOW_CLI_AUTH_LOGIN_TIMEOUT to raise the limit "
                f"(current: {timeout_seconds}s)."
            ),
        ) from None


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
    """Bypass strategy using system Chrome with 'Passive Capture' pattern.

    Launches real Chrome WITHOUT any automation-triggering flags or debugging
    ports — a 100% standard browser process that Google's G12 block cannot
    detect.  The user signs in manually, then closes Chrome.  gflow then runs
    a fast headless Playwright probe to verify the persisted cookies.

    Stealth properties:
      - No --remote-debugging-port, no --enable-automation.
      - navigator.webdriver is naturally absent (no Playwright injection).
      - Chrome launches exactly as a normal user process.
    """

    name = "chrome"

    def __init__(self, *, timeout_seconds: int = 600) -> None:
        # Maximum seconds to wait for the user to close Chrome.
        self._timeout_seconds = timeout_seconds

    async def login(self, profile_dir: Path, headless: bool) -> None:
        """Execute the login flow using Passive Capture on Real Chrome."""
        settings = get_settings()
        _validate_profile_dir(profile_dir, settings)
        profile_dir.mkdir(parents=True, exist_ok=True)

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

        logger.info(
            "auth_passive_capture_started",
            profile_dir=str(profile_dir),
            strategy=self.name,
        )
        if not headless:
            _print_login_instructions()

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
        try:
            status = await verify_flow_profile(profile_dir, source=self.name)
            verified = status.authenticated
        finally:
            # `finally` (not `except`) so an interrupt — KeyboardInterrupt /
            # asyncio.CancelledError, both BaseException — also rolls back a
            # speculative write and never leaves an unverified profile claiming
            # the chrome strategy.
            if not verified and not marker_preexisted:
                marker.unlink(missing_ok=True)
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
