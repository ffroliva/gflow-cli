"""#788: a model picker present in the DOM but hidden (#749's agent-mode shape) must be
refused, never read: reading its text "matched" the requested model, so the run went on and
billed whatever Flow actually had bound."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.video import VideoModel
from gflow_cli.errors import UiSelectorDriftError


def _pane_with_hidden_button(text: str) -> tuple[Any, Any]:
    button = MagicMock()
    button.wait_for = AsyncMock(side_effect=TimeoutError("not visible"))
    button.count = AsyncMock(return_value=1)  # present in the DOM
    button.text_content = AsyncMock(return_value=text)
    button.click = AsyncMock(side_effect=AssertionError("a hidden picker must not be clicked"))
    pane = MagicMock()
    pane.locator.return_value.filter.return_value.first = button
    return pane, button


@pytest.mark.asyncio
async def test_a_hidden_image_model_picker_is_refused_not_read() -> None:
    pane, button = _pane_with_hidden_button("arrow_drop_downNano Banana 2.1")
    with pytest.raises(UiSelectorDriftError, match="hidden") as ei:
        await MigratedComposer()._select_image_model(MagicMock(), pane, ImageModel.NARWHAL)  # noqa: SLF001
    button.text_content.assert_not_awaited()
    # The one cause with a user action (#749): not the class's "file a frontend bug".
    assert "switch Agent off" in ei.value.remediation_hint


@pytest.mark.asyncio
async def test_a_hidden_video_model_picker_is_refused_not_read() -> None:
    pane, button = _pane_with_hidden_button("arrow_drop_downVeo 3.1 - Fast")
    with pytest.raises(UiSelectorDriftError, match="hidden"):
        await MigratedComposer()._select_model(MagicMock(), pane, VideoModel.VEO_3_1_FAST)  # noqa: SLF001
    button.text_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_missing_model_picker_keeps_the_drift_hint() -> None:
    pane, button = _pane_with_hidden_button("")
    button.count = AsyncMock(return_value=0)
    with pytest.raises(UiSelectorDriftError, match="missing") as ei:
        await MigratedComposer()._select_image_model(MagicMock(), pane, ImageModel.NARWHAL)  # noqa: SLF001
    assert "switch Agent off" not in ei.value.remediation_hint
