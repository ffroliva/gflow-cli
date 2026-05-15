from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
from playwright.async_api import async_playwright
from rich.console import Console

from gflow_cli.config import get_settings
from gflow_cli.errors import SecurityError

from .base import AuthStrategy

logger = structlog.get_logger(__name__)
_console = Console()

GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"

# Stealth init script runs before any page JS on every navigation.
# Belt-and-suspenders for JS-level automation checks that survive the
# --disable-blink-features=AutomationControlled C++ flag.
_STEALTH_INIT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
"""


class RealChromeStrategy(AuthStrategy):
    """Bypass strategy using system Chrome with stealth and privacy protections.

    This strategy launches the real system Chrome (via Playwright channel="chrome")
    and applies several stealth techniques to bypass bot detection.

    Stealth approach (empirically validated against Google's G12 block):
      1. ``ignore_default_args=["--enable-automation"]`` — prevents the
         "Chrome is being controlled" automation bar.
      2. ``args=["--disable-blink-features=AutomationControlled"]`` — disables
         the Blink feature that sets ``navigator.webdriver = true`` as a
         non-configurable C++ property.  Without this flag, the JS
         ``Object.defineProperty`` override arrives too late (after the native
         property is already locked) and silently fails.  A cosmetic
         "unsupported flag" notice may appear in the browser — this is an
         accepted trade-off vs. the functional sign-in rejection.
      3. ``add_init_script`` — runs before any page JS on every navigation;
         provides JS-level masking as belt-and-suspenders.
      4. Privacy Guard — profile_dir must be inside GFLOW_CLI_HOME to prevent
         accidental use of the user's primary system Chrome profile.
    """

    name = "chrome"

    async def login(self, profile_dir: Path, headless: bool) -> None:
        """Execute the login flow using real system Chrome."""
        settings = get_settings()
        try:
            profile_dir.relative_to(settings.home)
        except ValueError:
            raise SecurityError(
                f"Profile directory {profile_dir} is outside of GFLOW_CLI_HOME "
                f"({settings.home}) boundaries."
            ) from None

        profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info("auth_login_started", profile_dir=str(profile_dir), strategy=self.name)

        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=headless,
                viewport={"width": 1280, "height": 800},
                ignore_default_args=["--enable-automation", "--no-sandbox"],
                # Required to prevent Blink from setting navigator.webdriver as a
                # non-configurable native property before our JS override can run.
                # See class docstring for the full timing rationale.
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                # Register BEFORE accessing any page — ensures even the first
                # navigation (goto below) runs the stealth script.
                await ctx.add_init_script(_STEALTH_INIT_SCRIPT)

                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)

                if not headless:
                    _console.print(
                        "\n  [bold]Action Required:[/bold] Complete the sign-in "
                        "process in the Chrome window.\n"
                        "  I will automatically click through the landing page "
                        "and account selection.\n"
                        "  [dim]Chrome may briefly show an 'unsupported flag' notice"
                        " — this is expected and harmless.[/dim]\n"
                    )

                # Polling for success (SAPISID cookie + UI signal)
                # T2.5: Optimistic Orchestration - 1s intervals, 10 min timeout
                timeout_at = asyncio.get_running_loop().time() + 600
                clicked_landing = False
                clicked_account = False

                while asyncio.get_running_loop().time() < timeout_at:
                    try:
                        # 1. Check for Landing Page and click through
                        if not clicked_landing:
                            # Try multiple selectors for the landing page button
                            create_btn = page.locator('text="Create with Flow"').first
                            if await create_btn.is_visible():
                                logger.debug("auth_clicking_landing_page")
                                await create_btn.click(no_wait_after=True, timeout=3000)
                                clicked_landing = True

                        # 2. Check for Account Chooser and click first account
                        if not clicked_account:
                            account_btn = page.locator("[data-email]").first
                            if await account_btn.is_visible():
                                email = await account_btn.get_attribute("data-email")
                                logger.info("auth_selecting_account", email=email)
                                await account_btn.click(no_wait_after=True, timeout=3000)
                                clicked_account = True

                        # 3. Success Detection: SAPISID cookie + Editor UI
                        cookies = await ctx.cookies()
                        has_sapisid = any(c.get("name") == "SAPISID" for c in cookies)

                        if has_sapisid:
                            # Final confirmation via UI signal in the editor
                            if (
                                await page.get_by_text("New project").is_visible()
                                or await page.get_by_text("Your projects").is_visible()
                            ):
                                logger.info("auth_login_success_detected", strategy=self.name)
                                break
                    except Exception as e:
                        logger.debug("auth_polling_tick_error", error=str(e))
                        # If browser is closed or context is gone, exit loop
                        if "Target closed" in str(e) or "context closed" in str(e):
                            break

                    await asyncio.sleep(1)
                else:
                    logger.warning("auth_login_timed_out", strategy=self.name)

                # Small delay to ensure state is flushed to disk
                await asyncio.sleep(1)

            finally:
                await ctx.close()
