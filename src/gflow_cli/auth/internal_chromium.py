from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from playwright.async_api import Error as PlaywrightError
from rich.console import Console

from gflow_cli.config import get_settings
from gflow_cli.errors import AuthBrowserRejectedError, AuthLoginTimeoutError, SecurityError

from .base import AuthStrategy
from .verification import SESSION_API_URL, FlowSessionOutcome, evaluate_session_response

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)
_console = Console()

GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"
GOOGLE_REJECTED_BROWSER_ROUTE = "accounts.google.com/v3/signin/rejected"


async def _poll_session_until_authenticated(
    ctx: Any,
    page: Any,
    timeout_seconds: int,
    strategy_name: str,
) -> str | None:
    """Poll the Flow NextAuth session endpoint until the sign-in completes.

    Returns the verified user email, or None if it could not be extracted.
    Raises ``AuthBrowserRejectedError`` if Google rejects the browser.
    Raises ``AuthLoginTimeoutError`` if the timeout elapses or the browser
    closes before authentication is verified.
    """
    timeout_at = asyncio.get_running_loop().time() + timeout_seconds
    success = False
    _email: str | None = None

    while asyncio.get_running_loop().time() < timeout_at:
        try:
            if _is_google_rejected_browser_page(page):
                raise AuthBrowserRejectedError

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
                )
                success = True
                _email = status.user_email
                break
        except asyncio.CancelledError:
            raise
        except AuthBrowserRejectedError:
            raise
        except PlaywrightError:
            # Browser / page / context closed — stop polling.
            break
        except Exception as exc:
            logger.warning(
                "auth_flow_session_poll_error",
                strategy=strategy_name,
                error=type(exc).__name__,
            )
            break

        await asyncio.sleep(3)
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
        async with async_playwright() as pw:
            # We use launch_persistent_context to ensure cookies are saved to profile_dir
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=headless,
                viewport={"width": 1280, "height": 800},
                args=["--password-store=basic"],
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
                user_email = await _poll_session_until_authenticated(
                    ctx, page, self._timeout_seconds, self.name,
                )
                # Small delay to ensure state is flushed to disk
                await asyncio.sleep(1)

            finally:
                await ctx.close()

        if user_email:
            (profile_dir / ".gflow_account").write_text(user_email, encoding="utf-8")
