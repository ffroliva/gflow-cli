"""BDD steps for the #529 media-picker tier reorder."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.transports.ui_automation_video import (
    ADD_MEDIA_BUTTON,
    DIALOG_ANY,
    PICKER_SEARCH_INPUT,
    VideoGenerationMixin,
)
from gflow_cli.errors import TransportTimeoutError
from gflow_cli.json_output import exit_code_for
from tests.api.transports.test_ui_automation_video import (
    TestSelectExistingAssetPickerScroll as _PickerFake,
)

scenarios("media_picker_tiers.feature")


_MEDIA_ID = "d6f1927a-3eae-4626-bc90-9a6ea7637bab"


@pytest.fixture
def picker_state() -> dict[str, Any]:
    return {
        "media_id": _MEDIA_ID,
        "display_name": "",
        "local_path": "",
        "order": [],
        "exception": None,
        "generation_submitted": False,
    }


def _image_page(tile: MagicMock) -> tuple[MagicMock, MagicMock]:
    """Reuse the transport unit suite's fake Page, adding the caller's add button."""
    page = _PickerFake._page_with_tile(tile)
    search = page.locator(PICKER_SEARCH_INPUT)
    dialog = page.locator(DIALOG_ANY)
    add = MagicMock()
    add.first = add
    add.wait_for = AsyncMock()
    add.click = AsyncMock()

    def _locator(selector: str) -> MagicMock:
        if selector == ADD_MEDIA_BUTTON:
            return add
        if selector == PICKER_SEARCH_INPUT:
            return search
        if selector == DIALOG_ANY:
            return dialog
        return tile

    page.locator = MagicMock(side_effect=_locator)
    return page, search


def _install_scroll(
    monkeypatch: pytest.MonkeyPatch,
    picker_state: dict[str, Any],
    *,
    found: bool,
) -> None:
    async def _scroll(*_args: object) -> bool:
        picker_state["order"].append("scroll")
        return found

    monkeypatch.setattr(
        VideoGenerationMixin,
        "_scroll_picker_grid_until_rendered",
        AsyncMock(side_effect=_scroll),
    )


@given('an image i2i generation with one bare "--ref <uuid>"')
def _given_image_ref(picker_state: dict[str, Any]) -> None:
    picker_state["media_id"] = _MEDIA_ID


@given("the reference tile is not in the picker's initial viewport")
def _given_off_viewport(picker_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    tile = _PickerFake._tile_mock(
        wait_for_side_effect=[TimeoutError("not initially visible"), None],
        count_side_effect=0,
    )
    page, search = _image_page(tile)

    async def _press(term: str, **_kwargs: object) -> None:
        picker_state["order"].append(f"search:{term}")

    search.press_sequentially = AsyncMock(side_effect=_press)
    picker_state.update(page=page, search=search, tile=tile)
    _install_scroll(monkeypatch, picker_state, found=True)


@given("the reference tile is absent from the picker entirely")
def _given_absent_ref(picker_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    tile = _PickerFake._tile_mock(
        wait_for_side_effect=TimeoutError("never visible"),
        count_side_effect=0,
    )
    page, search = _image_page(tile)

    async def _press(term: str, **_kwargs: object) -> None:
        picker_state["order"].append(f"search:{term}")

    search.press_sequentially = AsyncMock(side_effect=_press)
    upload = AsyncMock()
    monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", upload)
    picker_state.update(
        page=page,
        search=search,
        tile=tile,
        upload=upload,
        local_path="recorded-ref.png",
    )
    _install_scroll(monkeypatch, picker_state, found=False)


@given("a video i2v generation with an initial frame given as a media UUID")
def _given_frame_ref(picker_state: dict[str, Any]) -> None:
    picker_state["slot"] = "Start"


@given("the asset is absent from the picker entirely")
def _given_absent_frame(picker_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    tile = _PickerFake._tile_mock(
        wait_for_side_effect=TimeoutError("never visible"),
        count_side_effect=0,
    )
    page, _search = _image_page(tile)
    slot = MagicMock()
    slot.click = AsyncMock()
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_resolve_frame_slot",
        AsyncMock(return_value=slot),
    )
    monkeypatch.setattr(VideoGenerationMixin, "_sync_picker_project", AsyncMock())
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_select_existing_asset",
        AsyncMock(return_value=None),
    )
    picker_state.update(page=page, tile=tile, frame_slot=slot)


@given("the picker variant renders no search input")
def _given_no_search(picker_state: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    tile = _PickerFake._tile_mock(
        wait_for_side_effect=[TimeoutError("not initially visible"), None],
        count_side_effect=0,
    )
    page, search = _image_page(tile)
    search.count = AsyncMock(return_value=0)
    picker_state.update(page=page, search=search, tile=tile)
    _install_scroll(monkeypatch, picker_state, found=True)


@when("the transport binds the reference")
def _bind_reference(
    picker_state: dict[str, Any],
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    asyncio.run(
        VideoGenerationMixin._attach_image_uuid_refs(
            picker_state["page"],
            [
                (
                    picker_state["media_id"],
                    picker_state["display_name"],
                    picker_state["local_path"],
                )
            ],
            out_dir=None,
        )
    )
    picker_state["logs"] = list(install_log_capture.entries)


@when("the transport binds the frame")
def _bind_frame(picker_state: dict[str, Any]) -> None:
    try:
        asyncio.run(
            VideoGenerationMixin._attach_frame_by_media_id(
                picker_state["page"],
                0,
                picker_state["slot"],
                picker_state["media_id"],
                out_dir=None,
            )
        )
    except TransportTimeoutError as exc:
        picker_state["exception"] = exc


@then("no search term is typed before the grid is scrolled")
def _then_scroll_precedes_search(picker_state: dict[str, Any]) -> None:
    assert picker_state["order"] and picker_state["order"][0] == "scroll"


@then("the tile is attached from the scroll tier")
def _then_attached_from_scroll(picker_state: dict[str, Any]) -> None:
    assert picker_state["order"][0] == "scroll"
    picker_state["tile"].click.assert_awaited_once()


@then('the attach event reports resolved_by "scroll"')
def _then_scroll_event(picker_state: dict[str, Any]) -> None:
    attach_events = [
        event
        for event in picker_state["logs"]
        if event["event"] == "ui_automation_video.image_ref_selected_existing"
    ]
    assert attach_events and attach_events[0]["resolved_by"] == "scroll"


@then("the demoted UUID search tiers are attempted after the scroll")
def _then_uuid_retry_after_scroll(picker_state: dict[str, Any]) -> None:
    assert picker_state["order"] == [
        "scroll",
        f"search:{_MEDIA_ID}",
        "search:d6f1927a",
    ]


@then("the recorded local file is uploaded as the fallback")
def _then_uploaded(picker_state: dict[str, Any]) -> None:
    picker_state["upload"].assert_awaited_once()


@then("the generation never proceeds without the reference")
def _then_image_not_submitted(picker_state: dict[str, Any]) -> None:
    assert picker_state["generation_submitted"] is False


@then("a TransportTimeoutError naming the slot and the UUID is raised")
def _then_frame_error(picker_state: dict[str, Any]) -> None:
    exc = picker_state["exception"]
    assert isinstance(exc, TransportTimeoutError)
    assert exc.detail == (
        f"Start frame asset {_MEDIA_ID!r} could not be located in the media picker — "
        "is it in the target project (missing or wrong --project), and is the UUID "
        "from this profile's library?"
    )


@then("the process exits with code 9")
def _then_exit_9(picker_state: dict[str, Any]) -> None:
    assert exit_code_for(picker_state["exception"]) == 9


@then("no generation is submitted")
def _then_video_not_submitted(picker_state: dict[str, Any]) -> None:
    assert picker_state["generation_submitted"] is False


@then("no search input is filled at any point")
def _then_no_search_fill(picker_state: dict[str, Any]) -> None:
    picker_state["search"].fill.assert_not_awaited()
    picker_state["search"].press_sequentially.assert_not_awaited()


@then("the grid is scrolled to locate the tile")
def _then_grid_scrolled(picker_state: dict[str, Any]) -> None:
    assert picker_state["order"] == ["scroll"]
    picker_state["tile"].click.assert_awaited_once()
