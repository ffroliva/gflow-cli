from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from rich.console import Console

from .base import AuthStrategy

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)
_console = Console()

GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"


class InternalChromiumStrategy(AuthStrategy):
    """Legacy login strategy using bundled Playwright Chromium.

    This strategy is kept as a fallback for cases where Real Chrome is not available
    or desired, although it may be blocked by Google's "browser not secure" check.
    """

    name = "internal"

    async def login(self, profile_dir: Path, headless: bool) -> None:
        """Execute the login flow using internal Chromium."""
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

                # Polling for success (SAPISID cookie + UI signal)
                # This matches AC-3 in the design spec.
                while True:
                    try:
                        cookies = await ctx.cookies()
                        has_sapisid = any(c.get("name") == "SAPISID" for c in cookies)

                        if has_sapisid:
                            # Final confirmation via UI signal
                            if (
                                await page.get_by_text("New project").is_visible()
                                or await page.get_by_text("Your projects").is_visible()
                            ):
                                logger.info("auth_login_success_detected", strategy=self.name)
                                break
                    except Exception:
                        # If browser is closed or context is gone, exit loop
                        break

                    await asyncio.sleep(1)

                # Small delay to ensure state is flushed to disk
                await asyncio.sleep(1)

            finally:
                await ctx.close()
