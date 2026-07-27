"""Unit tests for top banner, alert, and modal overlay dismissal (#369)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation import (
    CHANGELOG_IFRAME_SELECTORS,
    TOP_BANNER_SELECTORS,
    WELCOME_SCREEN_SELECTORS,
    UiAutomationTransport,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_mock_page(
    visible_selectors: set[str] | None = None,
    keyboard_press_raises: bool = False,
) -> MagicMock:
    """Build a fake Playwright page for overlay dismissal tests."""
    visible = visible_selectors or set()
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock()

    if keyboard_press_raises:
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock(side_effect=RuntimeError("keyboard press failed"))
    else:
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()

    clicked: list[str] = []

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        if sel in visible:
            loc.is_visible = AsyncMock(return_value=True)
        else:
            loc.is_visible = AsyncMock(return_value=False)

        async def _click(**_kwargs: object) -> None:
            clicked.append(sel)

        loc.click = AsyncMock(side_effect=_click)
        wrapper = MagicMock()
        wrapper.first = loc
        return wrapper

    page.locator = MagicMock(side_effect=_locator)
    page._clicked = clicked  # type: ignore[attr-defined]
    return page


class TestTopBannerAndAlertDismissal:
    """Unit tests for Issue #369 top banner and alert dismissal."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("selector", TOP_BANNER_SELECTORS)
    async def test_detect_overlay_identifies_top_banners(self, selector: str) -> None:
        """_detect_overlay returns True for each top banner/alert selector."""
        page = _make_mock_page(visible_selectors={selector})
        result = await UiAutomationTransport._detect_overlay(page)
        assert result is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "selector",
        CHANGELOG_IFRAME_SELECTORS + WELCOME_SCREEN_SELECTORS,
    )
    async def test_detect_overlay_identifies_existing_overlays(self, selector: str) -> None:
        """_detect_overlay returns True for changelog and welcome screen selectors."""
        page = _make_mock_page(visible_selectors={selector})
        result = await UiAutomationTransport._detect_overlay(page)
        assert result is True

    @pytest.mark.asyncio
    async def test_detect_overlay_returns_false_when_clean(self) -> None:
        """_detect_overlay returns False when no banner or overlay is visible."""
        page = _make_mock_page(visible_selectors=set())
        result = await UiAutomationTransport._detect_overlay(page)
        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "close_sel",
        [
            "button:has(i.google-symbols:text('clear'))",
            "button:has(i:text('clear'))",
            "button:has(i.google-symbols:text('close'))",
            "button:has(i:text('close'))",
            "[aria-label*='Got it' i]",
            "button:has-text('Got it')",
            "[aria-label*='Dismiss' i]",
        ],
    )
    async def test_dismiss_blocking_overlays_clicks_new_close_buttons(self, close_sel: str) -> None:
        """_dismiss_blocking_overlays force-clicks clear/close/Got-it/Dismiss buttons."""
        t = UiAutomationTransport()
        # Banner visible AND the target close button visible
        page = _make_mock_page(visible_selectors={"[role='alert']", close_sel})
        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]
        assert result is True
        assert close_sel in page._clicked  # type: ignore[attr-defined]
        page.keyboard.press.assert_not_called()

    @pytest.mark.asyncio
    async def test_dismiss_blocking_overlays_escape_fallback(self) -> None:
        """Banner detected but no close button matches -> presses Escape as fallback."""
        t = UiAutomationTransport()
        page = _make_mock_page(visible_selectors={"[role='banner']"})
        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]
        assert result is True
        page.keyboard.press.assert_called_once_with("Escape")
        assert page._clicked == []  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_dismiss_blocking_overlays_escape_failure_handles_error(
        self, tmp_path: Path
    ) -> None:
        """When close buttons missing and Escape raises, returns False and takes screenshot."""
        t = UiAutomationTransport()
        page = _make_mock_page(
            visible_selectors={"[role='dialog']"},
            keyboard_press_raises=True,
        )
        result = await t._dismiss_blocking_overlays(page, out_dir=tmp_path)  # type: ignore[attr-defined]
        assert result is False
        page.screenshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_dismiss_blocking_overlays_no_overlay_returns_false(self) -> None:
        """When page has no overlay, returns False without clicking or pressing keys."""
        t = UiAutomationTransport()
        page = _make_mock_page(visible_selectors=set())
        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]
        assert result is False
        assert page._clicked == []  # type: ignore[attr-defined]
        page.keyboard.press.assert_not_called()
