from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import structlog
from rich.console import Console

from gflow_cli.config import get_settings
from gflow_cli.errors import AuthLoginTimeoutError, AuthMissingError, SecurityError

from .base import AuthStrategy
from .verification import FlowSessionOutcome, verify_flow_session

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
            GEMINI_URL,  # open straight on the Flow sign-in page
        ]

        if headless:
            chrome_args.append("--headless=new")

        # Open Flow directly so the user lands on the sign-in / app surface
        # instead of a blank new-tab page.
        chrome_args.append(GEMINI_URL)

        logger.info(
            "auth_passive_capture_started",
            profile_dir=str(profile_dir),
            strategy=self.name,
        )

        if not headless:
            _console.print("\n" + "=" * 60)
            _console.print("[bold cyan]PASSIVE AUTHENTICATION[/bold cyan]")
            _console.print("=" * 60)
            _console.print("1. A Google Chrome window opens at the Flow sign-in page.")
            _console.print("2. Sign in with your Google account.")
            _console.print(
                "3. [bold yellow]Keep going until the Flow editor itself loads[/bold yellow] "
                "— the prompt box and your projects."
            )
            _console.print(
                "   Signing in to Google is NOT enough; gflow needs a completed Flow app sign-in."
            )
            _console.print(
                "4. Then [bold yellow]CLOSE THE BROWSER[/bold yellow] — gflow verifies "
                "the Flow session automatically."
            )
            _console.print("-" * 60)
            _console.print("Launching Chrome...")

        # Tests MUST patch asyncio.create_subprocess_exec itself — patching
        # subprocess.Popen instead lets the real asyncio transport run against a
        # mock process, hanging proc.wait() forever on the loop's child watcher.
        proc = await asyncio.create_subprocess_exec(
            *chrome_args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Wait for the user to close Chrome.  Chrome holds an exclusive lock on
        # its SQLite cookie store while running, so we must wait before probing.
        try:
            await asyncio.wait_for(proc.wait(), timeout=float(self._timeout_seconds))
        except TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                proc.kill()
            raise AuthLoginTimeoutError(
                f"Sign-in timed out after {self._timeout_seconds}s; Chrome was stopped.",
                remediation_hint=(
                    "Run `gflow auth login` again and complete sign-in before the time limit. "
                    f"Set GFLOW_CLI_AUTH_LOGIN_TIMEOUT to raise the limit "
                    f"(current: {self._timeout_seconds}s)."
                ),
            ) from None

        _console.print("\n[bold green]Browser closed.[/bold green] Verifying Flow session...")

        # Verify the real Flow app session — not just the Google SSO cookie.
        status = await verify_flow_session(profile_dir, channel="chrome", source=self.name)

        if status.authenticated:
            logger.info(
                "auth_flow_session_verified",
                strategy=self.name,
                source=status.source,
                user_email=status.user_email,
            )
            # Marker read by browser_manager.channel_for_profile so FlowApiClient
            # selects the system Chrome channel. Load-bearing — must persist here.
            (profile_dir / ".gflow_browser_strategy").write_text("chrome", encoding="utf-8")
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
