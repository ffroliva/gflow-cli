from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import structlog
from rich.console import Console

from gflow_cli.config import get_settings
from gflow_cli.errors import SecurityError

from .base import AuthStrategy
from .internal_chromium import poll_session_until_authenticated

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)
_console = Console()

GEMINI_URL = "https://labs.google/fx/tools/flow?hl=en"


class CamoufoxStrategy(AuthStrategy):
    """Login strategy using Camoufox stealth browser.

    This bypasses Google's browser restrictions by spoofing fingerprints and
    avoiding Playwright automation leaks.
    """

    name = "camoufox"

    def __init__(self, *, timeout_seconds: int = 600) -> None:
        self._timeout_seconds = timeout_seconds

    async def login(self, profile_dir: Path, headless: bool) -> None:
        """Execute the login flow using Camoufox."""
        settings = get_settings()
        try:
            profile_dir.resolve(strict=False).relative_to(settings.home.resolve())
        except ValueError:
            msg = (
                f"Profile directory {profile_dir} is outside of GFLOW_CLI_HOME "
                f"({settings.home}) boundaries."
            )
            raise SecurityError(msg) from None

        profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info("auth_login_started", profile_dir=str(profile_dir), strategy=self.name)

        user_email: str | None = None

        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError as e:
            from gflow_cli.errors import ConfigurationError

            raise ConfigurationError(
                "Camoufox not installed. Run `uv pip install gflow-cli[camoufox]`"
                " or `pip install camoufox`."
            ) from e

        async with AsyncCamoufox(
            persistent_context=True,
            user_data_dir=str(profile_dir),
            headless=headless,
        ) as raw_ctx:
            ctx = cast("Any", raw_ctx)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)

            if not headless:
                _console.print(
                    "\n  Sign into your Google account in the open Camoufox window.\n"
                    "  Once you reach the Flow editor, gflow will automatically detect "
                    "success and exit.\n",
                )

            user_email = await poll_session_until_authenticated(
                ctx,
                page,
                self._timeout_seconds,
                self.name,
            )
            await asyncio.sleep(1)

        if user_email:
            (profile_dir / ".gflow_account").write_text(user_email, encoding="utf-8")
            (profile_dir / ".gflow_browser_strategy").write_text("camoufox", encoding="utf-8")
