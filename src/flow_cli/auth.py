"""Auth — capture/refresh Google session via Playwright persistent context."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"


def default_profile_root() -> Path:
    """~/.flow-cli/ on POSIX, %USERPROFILE%/.flow-cli/ on Windows."""
    home = Path(os.environ.get("FLOW_CLI_HOME") or Path.home() / ".flow-cli")
    return home


def profile_dir(name: str = "default") -> Path:
    return default_profile_root() / f"profile_{name}"


async def login(name: str = "default") -> Path:
    """Open a HEADED Chromium window, let user sign into Google, persist session.

    Returns the profile directory path. On subsequent runs the saved cookies
    are reused; if Google's session expires, calling this again re-captures it.
    """
    pdir = profile_dir(name)
    pdir.mkdir(parents=True, exist_ok=True)
    logger.info("login: launching browser, profile=%s", pdir)
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(pdir),
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)
            print(
                "\n  Sign into your Google account in the open window.\n"
                "  Once you reach the Flow editor, close the window to save the session.\n"
            )
            # Wait until the user closes the context
            try:
                await ctx.wait_for_event("close", timeout=600_000)
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
    cookies_file: Optional[Path] = None
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
