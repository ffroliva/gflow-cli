"""Unit tests for ``UiAutomationTransport._switch_to_image_mode``.

Mirror of the video-side ``_switch_to_video_mode`` tests. The image
transport must select Image mode explicitly when entering the editor;
otherwise an account whose last-used mode was Video silently routes
``image t2i`` / ``image batch`` prompts to the video endpoint (no
``batchGenerateImages`` response observed; 3-minute listener timeout).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports import ui_automation as mod
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import (
    MODE_SWITCH_TRIGGER_SELECTORS,
)


def _cascade_page(visible: set[str]) -> MagicMock:
    """A fake page whose ``locator(sel)`` is 'visible' only for ``sel in visible``."""
    page = MagicMock()

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        loc.first = loc
        if sel in visible:
            loc.wait_for = AsyncMock()
        else:
            loc.wait_for = AsyncMock(side_effect=Exception("not visible"))
        loc.click = AsyncMock()
        return loc

    page.locator = MagicMock(side_effect=_locator)
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    return page


class TestSwitchToImageMode:
    @pytest.mark.asyncio
    async def test_opens_dropdown_then_clicks_image_tab(self) -> None:
        trigger = MODE_SWITCH_TRIGGER_SELECTORS[0]
        image_tab = mod.IMAGE_TAB_IN_MENU_SELECTORS[0]
        page = _cascade_page({trigger, image_tab})
        await UiAutomationTransport._switch_to_image_mode(page, out_dir=None)
        assert page.locator.call_count >= 2

    @pytest.mark.asyncio
    async def test_raises_when_trigger_missing(self) -> None:
        page = _cascade_page(set())
        with pytest.raises(RuntimeError, match="mode-switch"):
            await UiAutomationTransport._switch_to_image_mode(page, out_dir=None)

    @pytest.mark.asyncio
    async def test_raises_when_image_tab_missing(self) -> None:
        page = _cascade_page({MODE_SWITCH_TRIGGER_SELECTORS[0]})
        with pytest.raises(RuntimeError, match="Image tab"):
            await UiAutomationTransport._switch_to_image_mode(page, out_dir=None)
