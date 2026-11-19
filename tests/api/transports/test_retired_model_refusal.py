"""#1375 scenario 2: when Flow's menu no longer offers a model it listed as leaving, the
pre-submit refusal stays exit 11 (charged: no) and names the retirement date and the models
Flow does offer."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports import _mc_settings_pane
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.video import VideoModel
from gflow_cli.errors import ConfigurationError, legacy_exit_code_for

_OFFERED = ["Veo 3.1 - Lite", "Omni 1.2 Flash"]


def _page_with_menu(offered: list[str]) -> Any:
    items = MagicMock()
    items.first.wait_for = AsyncMock()
    items.all_text_contents = AsyncMock(return_value=offered)
    page = MagicMock()
    page.locator.return_value = items
    page.keyboard.press = AsyncMock()
    return page


async def _refusal(
    monkeypatch: pytest.MonkeyPatch, model: VideoModel, offered: list[str]
) -> ConfigurationError:
    button = MagicMock()
    button.text_content = AsyncMock(return_value="arrow_drop_downVeo 3.1 - Lite")
    button.click = AsyncMock()
    monkeypatch.setattr(_mc_settings_pane, "_visible_model_picker", AsyncMock(return_value=button))
    with pytest.raises(ConfigurationError) as ei:
        await MigratedComposer()._select_model(_page_with_menu(offered), MagicMock(), model)  # noqa: SLF001
    return ei.value


@pytest.mark.asyncio
async def test_a_retired_model_missing_from_the_menu_names_the_date_and_the_offer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = await _refusal(monkeypatch, VideoModel.VEO_3_1_FAST, _OFFERED)

    assert legacy_exit_code_for(err) == 11
    assert err.charged == "no"
    assert "2026-12-05" in err.detail
    for name in _OFFERED:
        assert name in err.detail


@pytest.mark.asyncio
async def test_a_model_flow_is_not_retiring_keeps_the_plain_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = await _refusal(monkeypatch, VideoModel.OMNI_1_2_FLASH, ["Veo 3.1 - Lite"])

    assert legacy_exit_code_for(err) == 11
    assert "leaving" not in err.detail
    assert "Veo 3.1 - Lite" in err.detail
