from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import structlog
from rich.console import Console

from gflow_cli.config import get_settings
from gflow_cli.errors import AuthLoginTimeoutError, AuthMissingError, SecurityError

from .base import AuthStrategy

logger = structlog.get_logger(__name__)
_console = Console()

GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"


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
        try:
            profile_dir.resolve(strict=False).relative_to(settings.home.resolve())
        except ValueError:
            raise SecurityError(
                f"Profile directory {profile_dir} is outside of GFLOW_CLI_HOME "
                f"({settings.home}) boundaries."
            ) from None

        profile_dir.mkdir(parents=True, exist_ok=True)

        chrome_exe = find_chrome_executable()
        if not chrome_exe:
            raise RuntimeError(
                "Google Chrome not found on system. "
                "Please install Chrome or use '--browser internal'."
            )

        chrome_args = [
            chrome_exe,
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1280,800",
            # No --remote-debugging-port: zero automation surface.
        ]

        if headless:
            chrome_args.append("--headless=new")

        logger.info(
            "auth_passive_capture_started",
            profile_dir=str(profile_dir),
            strategy=self.name,
        )

        if not headless:
            _console.print("\n" + "=" * 60)
            _console.print("[bold cyan]PASSIVE AUTHENTICATION[/bold cyan]")
            _console.print("=" * 60)
            _console.print("1. A Google Chrome window will open.")
            _console.print(f"2. Sign in at: [bold]{GEMINI_URL}[/bold]")
            _console.print("3. Complete sign-in until you reach the Flow editor.")
            _console.print(
                "4. [bold yellow]CLOSE THE BROWSER[/bold yellow] when done — "
                "gflow will verify your session automatically."
            )
            _console.print("-" * 60)
            _console.print("Launching Chrome...")

        proc = subprocess.Popen(chrome_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Wait for the user to close Chrome.  Chrome holds an exclusive lock on
        # its SQLite cookie store while running, so we must wait before probing.
        # Run in a thread to avoid blocking the event loop.
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, proc.wait),
                timeout=float(self._timeout_seconds),
            )
        except TimeoutError:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise AuthLoginTimeoutError(
                f"Sign-in timed out after {self._timeout_seconds}s; Chrome was stopped.",
                remediation_hint=(
                    "Run `gflow auth login` again and complete sign-in before the time limit. "
                    f"Set GFLOW_CLI_AUTH_LOGIN_TIMEOUT to raise the limit "
                    f"(current: {self._timeout_seconds}s)."
                ),
            ) from None

        _console.print("\n[bold green]Browser closed.[/bold green] Verifying session...")

        # Headless probe: read persisted cookies from the isolated profile dir.
        # channel="chrome" uses the system Chrome binary so verification avoids
        # Playwright's own automation flags (belt-and-suspenders stealth).
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=True,
            )
            try:
                cookies = await ctx.cookies()
                has_sapisid = any(c.get("name") == "SAPISID" for c in cookies)

                if has_sapisid:
                    logger.info("auth_login_success_verified", strategy=self.name)
                    # Write strategy marker before any output that might fail on
                    # narrow Windows codepages — FlowApiClient reads this to select
                    # the matching Chrome channel for launch_persistent_context.
                    (profile_dir / ".gflow_browser_strategy").write_text("chrome", encoding="utf-8")
                    _console.print("[green][OK] Session captured and verified.[/green]")
                else:
                    logger.warning("auth_login_no_cookies", strategy=self.name)
                    raise AuthMissingError(
                        "No session cookies found after sign-in. "
                        "Did you complete the sign-in before closing Chrome?"
                    )
            finally:
                await ctx.close()
