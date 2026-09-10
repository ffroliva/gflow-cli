from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from playwright.async_api import Error as PlaywrightError
from rich.console import Console

from gflow_cli.config import get_settings
from gflow_cli.errors import AuthBrowserRejectedError, AuthLoginTimeoutError, SecurityError
from gflow_cli.profile_lease import ProfileLease

from .base import AuthStrategy
from .verification import SESSION_API_URL, FlowSessionOutcome, evaluate_session_response

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)
_console = Console()

GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"
GOOGLE_REJECTED_BROWSER_ROUTE = "accounts.google.com/v3/signin/rejected"
POLL_INTERVAL_SECONDS = 3


def login_launch_kwargs(
    profile_dir: Path,
    headless: bool,
    *,
    channel: str | None = None,
) -> dict[str, Any]:
    """Launch kwargs for a browser a HUMAN signs into — shared by both strategies.

    Defined once because the stealth set is measured, not chosen: 2026-09-08
    (docs/superpowers/spikes/2026-09-08-g12-blocks-webdriver-not-playwright.md)
    real Chrome WITHOUT these flags reported ``navigator.webdriver == True`` and
    Google routed the sign-in to ``/v3/signin/rejected`` in 17.5 s, while both
    real Chrome and bundled Chromium WITH them signed in normally. Whether
    either flag alone suffices is untested — keep both, on both strategies.

    ``channel="chrome"`` selects the system Chrome binary. It is not what gets
    past the sign-in gate (the bundled arm passed too) — it is what makes the
    resulting profile a chrome-strategy profile, without which
    ``channel_for_profile()`` returns None and generation silently downgrades.
    """
    return {
        "user_data_dir": str(profile_dir),
        "channel": channel,
        "headless": headless,
        # A human signs into this window, so let it be a REAL window: an
        # explicit viewport makes Playwright emulate that size independently of
        # the OS window and pushes Google's sign-in form off-screen on
        # smaller/scaled displays. The #315 "log in at the size you generate at"
        # rationale is preserved by --window-size below, on the real window.
        "no_viewport": True,
        # Playwright defaults chromium_sandbox=False, which injects
        # --no-sandbox: an extra automation signal plus Chrome's "unsupported
        # command-line flag" banner.
        "chromium_sandbox": True,
        "ignore_default_args": ["--enable-automation"],
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
            # Load-bearing beyond auth: keeps the profile off the macOS
            # keychain, which api/client.py also depends on (#222).
            "--password-store=basic",
        ],
    }


async def poll_session_until_authenticated(
    ctx: Any,
    page: Any,
    timeout_seconds: int,
    strategy_name: str,
    *,
    raise_on_close: bool = True,
) -> str | None:
    """Poll the Flow NextAuth session endpoint until the sign-in completes.

    Returns the verified user email, or None if it could not be extracted.
    Raises ``AuthBrowserRejectedError`` if Google rejects the browser.
    Raises ``AuthLoginTimeoutError`` if the timeout elapses, and — when
    ``raise_on_close`` — also when the browser closes before authentication is
    verified. Callers that own a *fallback* oracle (``RealChromeStrategy``
    re-checks the on-disk store with ``verify_flow_profile``) pass
    ``raise_on_close=False`` and get ``None`` instead: three releases told users
    to close the window themselves, so doing so must not turn a successful
    login red.
    """
    timeout_at = asyncio.get_running_loop().time() + timeout_seconds
    success = False
    _email: str | None = None

    while asyncio.get_running_loop().time() < timeout_at:
        try:
            # Liveness FIRST, because the host guard below can `continue` without
            # touching Playwright at all. A user who abandons a 2FA challenge closes
            # the window while still on accounts.google.com, so the guard short-circuits
            # every iteration, nothing raises, and the close is never noticed: measured
            # as a full run to the deadline with the session endpoint touched 0 times.
            # Reactive detection via `except PlaywrightError` only works once some
            # operation actually runs, which on the Google host it never does.
            if page.is_closed():
                break

            if _is_google_rejected_browser_page(page):
                raise AuthBrowserRejectedError

            # Do not touch the session endpoint while a sign-in is in flight.
            # `/fx/api/auth/session` is a NextAuth route that can rotate session
            # cookies, and a poll landing mid-callback can clobber the `state`/PKCE
            # cookies the callback needs — observed live 2026-09-08 as
            # `labs.google/fx/api/auth/signin?error=OAuthCallback`, a sign-in that
            # failed and then timed out at 600 s. The spike that signed in twice
            # never made this request at all: it read the jar locally over CDP.
            #
            # A HOST check alone does not do this. NextAuth's callback runs on the
            # app's own origin, so `/fx/api/auth/callback/google` passes any
            # labs.google test — see `_is_safe_to_probe_session`, which excludes the
            # auth routes as well as the host.
            if not _is_safe_to_probe_session(page):
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            cookies = await ctx.cookies()
            google_session = any(c.get("name") == "SAPISID" for c in cookies)
            resp = await page.request.get(SESSION_API_URL, timeout=15_000)
            status = evaluate_session_response(
                resp.status,
                await resp.text(),
                google_session=google_session,
                source=strategy_name,
            )
            if status.outcome is FlowSessionOutcome.AUTHENTICATED:
                logger.info(
                    "auth_flow_session_verified",
                    strategy=strategy_name,
                    source=status.source,
                    user_email=status.user_email,
                    # Which oracle spoke. RealChromeStrategy runs BOTH — this live probe
                    # decides when to close, then verify_flow_profile re-checks what
                    # actually landed on disk — and a live run on 2026-09-08 emitted two
                    # identical events, leaving "did the on-disk check pass?"
                    # unanswerable from the log.
                    probe="in_context",
                )
                success = True
                _email = status.user_email
                break
        except asyncio.CancelledError:
            raise
        except AuthBrowserRejectedError:
            raise
        except PlaywrightError as exc:
            # NOT every PlaywrightError means the window is gone. `TimeoutError`
            # subclasses `Error`, so a 15 s request timeout, a DNS hiccup or a Wi-Fi
            # reassociation lands here too — and breaking on those made gflow close
            # Chrome out from under a user still on Google's password screen, then
            # report exit 8 "No sign-in detected" for a sign-in that had not failed.
            # Ask the page whether it is actually closed; anything else is transient
            # and retries until the deadline. (HTTP-level failures never reached this
            # arm at all: they come back as VERIFICATION_ERROR and keep polling.)
            if page.is_closed():
                break
            logger.warning(
                "auth_flow_session_poll_error",
                strategy=strategy_name,
                error=type(exc).__name__,
            )
        except Exception as exc:
            logger.warning(
                "auth_flow_session_poll_error",
                strategy=strategy_name,
                error=type(exc).__name__,
            )
            break

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    else:
        msg = f"Flow sign-in not completed within {timeout_seconds}s."
        raise AuthLoginTimeoutError(
            msg,
            remediation_hint=(
                "Run `gflow auth login` again and continue until the Flow "
                "editor loads. Set GFLOW_CLI_AUTH_LOGIN_TIMEOUT higher if "
                f"needed (current: {timeout_seconds}s)."
            ),
        )

    if not success:
        if not raise_on_close:
            logger.info("auth_login_browser_closed_by_user", strategy=strategy_name)
            return None
        msg = "Browser closed before the Flow editor sign-in was verified."
        raise AuthLoginTimeoutError(
            msg,
            remediation_hint=(
                "Complete the Flow sign-in — until the editor loads — "
                "before closing the browser. Run `gflow auth login` to retry."
            ),
        )

    return _email


def _is_google_rejected_browser_page(page: object) -> bool:
    """Return True when Google has already routed login to its rejection page."""
    url = getattr(page, "url", "")
    return isinstance(url, str) and GOOGLE_REJECTED_BROWSER_ROUTE in url


def _is_safe_to_probe_session(page: object) -> bool:
    """Return True when the session endpoint can be read without disturbing a sign-in.

    Two conditions, and the second is the one a host check alone gets wrong.

    **On a Flow host.** ``flow_host_kind`` is the codebase's existing classifier
    (``api/transports/_common.py``) rather than a fourth copy of the host set: it
    requires https, matches the host exactly instead of by substring, and returns
    ``None`` for a non-str or an unparseable URL. That last part is load-bearing —
    ``urlparse("https://[bad").hostname`` raises ``ValueError``, which escaped an
    earlier version of this helper into the loop's catch-all and reported "browser
    closed" for a browser that was open.

    **Not on NextAuth's own auth routes.** NextAuth runs the OAuth callback on the
    *app's* origin, so a host test passes straight through it — verified:
    ``/fx/api/auth/callback/google?state=…&code=…`` and
    ``/fx/api/auth/signin?error=OAuthCallback`` both satisfy a labs.google host check.
    Reading ``/fx/api/auth/session`` while that callback is in flight is precisely the
    cookie-rotation hazard this guard exists to avoid, so excluding only
    ``accounts.google.com`` excluded the one phase where the callback is NOT running.
    """
    # Deferred: a module-level import cycles. `_common` reaches `profile_store`, which
    # imports `gflow_cli.auth` — verified as
    # "cannot import name 'default_profile_root' from partially initialized module".
    from gflow_cli.api.transports._common import flow_host_kind, flow_landing_kind

    url = getattr(page, "url", "")
    if flow_host_kind(url) is None:
        return False
    # The NextAuth prefix used to be a private constant here. It is now
    # `flow_landing_kind`, next to `flow_host_kind`, because the transports need the
    # same knowledge and could not reach it: a signed-out session sits on one of these
    # routes while passing every host check, which is #756 / the 2026-09-10 RED canary.
    return flow_landing_kind(url) != "signin"


class InternalChromiumStrategy(AuthStrategy):
    """Legacy login strategy using bundled Playwright Chromium.

    This strategy is kept as a fallback for cases where Real Chrome is not available
    or desired, although it may be blocked by Google's "browser not secure" check.
    """

    name = "internal"

    def __init__(self, *, timeout_seconds: int = 600) -> None:
        self._timeout_seconds = timeout_seconds

    async def login(self, profile_dir: Path, headless: bool) -> None:
        """Execute the login flow using internal Chromium."""
        settings = get_settings()
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

        # Deferred import to avoid circular dependency and support test patching
        from .strategies import async_playwright

        profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info("auth_login_started", profile_dir=str(profile_dir), strategy=self.name)

        user_email: str | None = None
        # Own the profile for this login context (D3). The lease is the OUTER
        # context so it releases only after the driver stops. Contention raises
        # ProfileLockedError before Chromium launches.
        async with ProfileLease(profile_dir), async_playwright() as pw:
            # We use launch_persistent_context to ensure cookies are saved to profile_dir
            # Bundled Chromium: no channel. Everything else — the stealth set,
            # the real-window geometry — is the shared, measured configuration.
            ctx = await pw.chromium.launch_persistent_context(
                **login_launch_kwargs(profile_dir, headless),
            )
            try:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)

                if not headless:
                    _console.print(
                        "\n  Sign into your Google account in the open window.\n"
                        "  Once you reach the Flow editor, gflow will automatically detect "
                        "success and exit.\n",
                    )

                # Poll until the Flow app sign-in completes; raises on timeout/rejection.
                user_email = await poll_session_until_authenticated(
                    ctx,
                    page,
                    self._timeout_seconds,
                    self.name,
                )
                # Small delay to ensure state is flushed to disk
                await asyncio.sleep(1)

            finally:
                await ctx.close()

        if user_email:
            (profile_dir / ".gflow_account").write_text(user_email, encoding="utf-8")
