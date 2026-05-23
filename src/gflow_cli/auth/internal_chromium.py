from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
from playwright.async_api import Error as PlaywrightError
from rich.console import Console

from gflow_cli.config import get_settings
from gflow_cli.errors import AuthBrowserRejectedError, AuthLoginTimeoutError, SecurityError

from .base import AuthStrategy
from .verification import SESSION_API_URL, FlowSessionOutcome, evaluate_session_response

logger = structlog.get_logger(__name__)
_console = Console()

GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"
GOOGLE_REJECTED_BROWSER_ROUTE = "accounts.google.com/v3/signin/rejected"


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
            raise SecurityError(
                f"Profile directory {profile_dir} is outside of GFLOW_CLI_HOME "
                f"({settings.home}) boundaries."
            ) from None

        # Deferred import to avoid circular dependency and support test patching
        from .strategies import async_playwright

        profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info("auth_login_started", profile_dir=str(profile_dir), strategy=self.name)

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
                        "success and exit.\n"
                    )

                # Poll the NextAuth session endpoint live until the Flow app
                # sign-in completes. Non-AUTHENTICATED outcomes (including a
                # transient VERIFICATION_ERROR) just mean "keep waiting" — the
                # user may still be signing in.
                timeout_at = asyncio.get_running_loop().time() + self._timeout_seconds
                success = False

                while asyncio.get_running_loop().time() < timeout_at:
                    try:
                        if _is_google_rejected_browser_page(page):
                            raise AuthBrowserRejectedError()

                        cookies = await ctx.cookies()
                        google_session = any(c.get("name") == "SAPISID" for c in cookies)
                        resp = await page.request.get(SESSION_API_URL, timeout=15_000)
                        status = evaluate_session_response(
                            resp.status,
                            await resp.text(),
                            google_session=google_session,
                            source=self.name,
                        )
                        if status.outcome is FlowSessionOutcome.AUTHENTICATED:
                            logger.info(
                                "auth_flow_session_verified",
                                strategy=self.name,
                                source=status.source,
                                user_email=status.user_email,
                            )
                            success = True
                            break
                    except asyncio.CancelledError:
                        raise
                    except AuthBrowserRejectedError:
                        raise
                    except PlaywrightError:
                        # Browser / page / context closed — stop polling.
                        break
                    # Unexpected error — log it and stop polling.
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "auth_flow_session_poll_error",
                            strategy=self.name,
                            error=type(exc).__name__,
                        )
                        break

                    await asyncio.sleep(3)
                else:
                    raise AuthLoginTimeoutError(
                        f"Flow sign-in not completed within {self._timeout_seconds}s.",
                        remediation_hint=(
                            "Run `gflow auth login` again and continue until the Flow "
                            "editor loads. Set GFLOW_CLI_AUTH_LOGIN_TIMEOUT higher if "
                            f"needed (current: {self._timeout_seconds}s)."
                        ),
                    )

                if not success:
                    raise AuthLoginTimeoutError(
                        "Browser closed before the Flow editor sign-in was verified.",
                        remediation_hint=(
                            "Complete the Flow sign-in — until the editor loads — "
                            "before closing the browser. Run `gflow auth login` to retry."
                        ),
                    )

                # Small delay to ensure state is flushed to disk
                await asyncio.sleep(1)

            finally:
                await ctx.close()
