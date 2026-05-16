from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
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
# Now focused on masking other browser fingerprints, as navigator.webdriver
# is handled natively by the 'Attach' strategy.
_STEALTH_INIT_SCRIPT = """
    window.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
"""


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


def get_free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class RealChromeStrategy(AuthStrategy):
    """Bypass strategy using system Chrome with 'Attach' pattern for maximum stealth.

    This strategy launches the real system Chrome via subprocess and connects
    via CDP. This avoids Playwright's default automation-triggering flags
    and provides a 'clean' browser state that naturally reports
    navigator.webdriver = false, bypassing Google's G12 block without
    requiring flags that trigger security banners.

    Stealth approach:
      1. Launch via subprocess: Prevents Playwright from injecting
         --enable-automation.
      2. No problematic flags: Does NOT use --disable-blink-features=AutomationControlled,
         so no "unsupported flag" banner appears.
      3. Native State: navigator.webdriver is naturally false and is NOT an
         'own' property, matching real user browsers perfectly.
      4. add_init_script: Provides additional fingerprint masking.
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

        chrome_exe = find_chrome_executable()
        if not chrome_exe:
            raise RuntimeError(
                "Google Chrome not found on system. Please install Chrome or use another strategy."
            )

        port = get_free_port()

        # Launch Chrome via subprocess to avoid Playwright's automation mode
        chrome_args = [
            chrome_exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1280,800",
            # We skip --enable-automation to keep navigator.webdriver = false
        ]
        if headless:
            # Use 'new' headless mode which is more like real Chrome
            chrome_args.append("--headless=new")

        logger.debug("auth_launching_chrome", args=chrome_args)

        # Use a process group or similar to ensure cleanup?
        # On Windows, we'll just rely on proc.terminate()
        proc = subprocess.Popen(chrome_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        try:
            async with async_playwright() as pw:
                # Give Chrome a moment to start the CDP server
                browser = None
                for _i in range(20):  # 10 seconds total
                    try:
                        browser = await pw.chromium.connect_over_cdp(f"http://localhost:{port}")
                        break
                    except Exception:
                        await asyncio.sleep(0.5)

                if not browser:
                    raise RuntimeError("Failed to connect to Chrome via CDP.")

                ctx = browser.contexts[0]

                # Register BEFORE accessing any page
                await ctx.add_init_script(_STEALTH_INIT_SCRIPT)

                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)

                if not headless:
                    _console.print(
                        "\n  [bold]Action Required:[/bold] Complete the sign-in "
                        "process in the Chrome window.\n"
                        "  I will automatically click through the landing page "
                        "and account selection.\n"
                    )

                # Polling for success (SAPISID cookie + UI signal)
                timeout_at = asyncio.get_running_loop().time() + 600
                clicked_landing = False
                clicked_account = False

                while asyncio.get_running_loop().time() < timeout_at:
                    try:
                        # 1. Check for Landing Page and click through
                        if not clicked_landing:
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
                        if "Target closed" in str(e) or "context closed" in str(e):
                            break

                    await asyncio.sleep(1)
                else:
                    logger.warning("auth_login_timed_out", strategy=self.name)

                # Small delay to ensure state is flushed to disk
                await asyncio.sleep(1)
                await browser.close()

        finally:
            # Ensure the Chrome process is killed
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
