"""Auth — capture/refresh Google session via Playwright persistent context.

Sessions live under `Settings.home / profile_<name>/`. Default location is
the OS-native user-data-dir (via `platformdirs`):

  * Windows: `%LOCALAPPDATA%\\gflow-cli\\profile_<name>`
  * macOS:   `~/Library/Application Support/gflow-cli/profile_<name>`
  * Linux:   `~/.local/share/gflow-cli/profile_<name>` (XDG)

Override the root with `GFLOW_CLI_HOME`. See `docs/AUTHENTICATION.md`.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from playwright.async_api import async_playwright
from rich.console import Console

from gflow_cli.config import get_settings

logger = structlog.get_logger(__name__)
_console = Console()

GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"


def default_profile_root() -> Path:
    """Root dir under which `profile_<name>/` subdirectories live.

    Returns `Settings.home`. Reads env via `get_settings()` so changes to
    `GFLOW_CLI_HOME` after import are honoured (provided the cache is reset).
    """
    return get_settings().home


def profile_dir(name: str = "default") -> Path:
    return get_settings().profile_subdir(name)


async def login(name: str = "default") -> Path:
    """Open a HEADED Chromium window, let user sign into Google, persist session.

    Returns the profile directory path. On subsequent runs the saved cookies
    are reused; if Google's session expires, calling this again re-captures it.
    """
    pdir = profile_dir(name)
    pdir.mkdir(parents=True, exist_ok=True)
    logger.info("auth_login_started", profile_dir=str(pdir))
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(pdir),
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)
            # User-facing instructions — Rich console, not raw print() (CLAUDE.md
            # invariant: no `print()` under src/).
            _console.print(
                "\n  Sign into your Google account in the open window.\n"
                "  Once you reach the Flow editor, close the window to save the session.\n"
            )
            try:
                await ctx.wait_for_event("close", timeout=600_000)  # pyright: ignore[reportUnknownMemberType]
            except Exception:
                pass
        finally:
            try:
                await ctx.close()
            except Exception:
                pass
    return pdir


def status(name: str = "default") -> dict[str, object]:
    """Lightweight check — does the profile dir exist and have cookies file?"""
    pdir = profile_dir(name)
    cookies_file: Path | None = None
    for candidate in (pdir / "Default" / "Cookies", pdir / "Cookies"):
        if candidate.exists():
            cookies_file = candidate
            break
    return {
        "profile": str(pdir),
        "exists": pdir.exists(),
        "cookies_present": cookies_file is not None,
        "cookies_path": str(cookies_file) if cookies_file else None,
    }
