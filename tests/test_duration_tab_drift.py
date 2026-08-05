"""Unit tests for video duration_tab selector cascade and fail-closed behavior (#451)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
from gflow_cli.errors import UiSelectorDriftError


@pytest.mark.asyncio
async def test_select_video_duration_matches_button_and_tab() -> None:
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    # Mock probe_selector_cascade to return a dummy element
    mock_element = AsyncMock()

    async def mock_probe_cascade(
        _page: MagicMock, probe_name: str, selectors: tuple[str, ...]
    ) -> AsyncMock:
        assert probe_name == "duration_tab"
        # Verify that selectors include both role='tab' and button / option selectors
        assert any("[role='tab']" in s for s in selectors)
        assert any(
            "button" in s or "[role='button']" in s or "[role='option']" in s for s in selectors
        )
        return mock_element

    original_probe = VideoGenerationMixin._probe_selector_cascade
    VideoGenerationMixin._probe_selector_cascade = mock_probe_cascade  # type: ignore[assignment]
    try:
        await VideoGenerationMixin._select_video_duration(page, 6, out_dir=None)
        mock_element.click.assert_awaited_once()
    finally:
        VideoGenerationMixin._probe_selector_cascade = original_probe  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_select_video_duration_fails_closed_when_missing() -> None:
    page = MagicMock()

    async def mock_probe_cascade(
        _page: MagicMock, _probe_name: str, _selectors: tuple[str, ...]
    ) -> None:
        return None

    original_probe = VideoGenerationMixin._probe_selector_cascade
    VideoGenerationMixin._probe_selector_cascade = mock_probe_cascade  # type: ignore[assignment]
    try:
        with pytest.raises(UiSelectorDriftError) as exc_info:
            await VideoGenerationMixin._select_video_duration(page, 4, out_dir=None)
        assert "duration_tab" in str(exc_info.value)
    finally:
        VideoGenerationMixin._probe_selector_cascade = original_probe  # type: ignore[assignment]
