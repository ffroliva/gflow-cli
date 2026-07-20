"""ClassicFlowUiDriver.configure_video_settings — duration branch (#288).

The mixin unit tests pin `_select_video_duration` itself; these pin the
driver call site: the explicit-duration guard, the `out_dir` forwarding
(the screenshot is lost if a regression drops it), and that a probe-miss
`UiSelectorDriftError` propagates out of the driver instead of being
swallowed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver
from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
from gflow_cli.api.video import GenerateVideoRequest
from gflow_cli.errors import UiSelectorDriftError


def _settings_page() -> MagicMock:
    page = MagicMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    return page


def _stub_settings_helpers(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stub every mixin helper configure_video_settings drives; return the
    duration stub for assertions."""
    duration = AsyncMock()
    monkeypatch.setattr(VideoGenerationMixin, "_select_video_model", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_switch_video_sub_mode", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_select_video_aspect", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_set_output_count", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_select_video_duration", duration)
    return duration


def test_classic_driver_does_not_advertise_unimplemented_await_images() -> None:
    """The classic cohort observes results by draining the batchGenerateImages
    wire response inside the transport — not via a driver method. The driver
    must NOT expose an ``await_images`` that only raises ``NotImplementedError``
    (advertising a capability it refuses)."""
    assert not hasattr(ClassicFlowUiDriver(), "await_images")


class TestConfigureVideoSettingsDuration:
    @pytest.mark.asyncio
    async def test_explicit_duration_forwards_out_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        duration = _stub_settings_helpers(monkeypatch)
        page = _settings_page()
        request = GenerateVideoRequest(prompt="a cat", duration=4)
        await ClassicFlowUiDriver().configure_video_settings(page, request, out_dir=tmp_path)
        duration.assert_awaited_once_with(page, 4, out_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_no_duration_skips_the_tab(self, monkeypatch: pytest.MonkeyPatch) -> None:
        duration = _stub_settings_helpers(monkeypatch)
        page = _settings_page()
        request = GenerateVideoRequest(prompt="a cat")
        await ClassicFlowUiDriver().configure_video_settings(page, request)
        duration.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_drift_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        duration = _stub_settings_helpers(monkeypatch)
        duration.side_effect = UiSelectorDriftError(detail="probe=duration_tab: miss")
        page = _settings_page()
        request = GenerateVideoRequest(prompt="a cat", duration=4)
        with pytest.raises(UiSelectorDriftError):
            await ClassicFlowUiDriver().configure_video_settings(page, request)
