"""BDD steps for catalog-name media-picker resolution (#529)."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api.transports.ui_automation_video import (
    ADD_MEDIA_BUTTON,
    DIALOG_ANY,
    PICKER_SEARCH_INPUT,
    VideoGenerationMixin,
)

scenarios("media_picker_tiers.feature")


_MEDIA_ID = "11111111-2222-4333-8444-555555555555"


def _tile_mock() -> MagicMock:
    tile = MagicMock()
    tile.first = tile
    tile.click = AsyncMock()
    tile.wait_for = AsyncMock()
    tile.evaluate = AsyncMock(return_value=True)
    tile.count = AsyncMock(return_value=1)
    return tile


def _page_with_tile(tile: MagicMock) -> MagicMock:
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()
    dialog = MagicMock()
    dialog.last = dialog
    dialog.hover = AsyncMock()
    dialog.wait_for = AsyncMock()
    search = MagicMock()
    search.first = search
    search.press_sequentially = AsyncMock()
    search.fill = AsyncMock()
    search.wait_for = AsyncMock()
    search.count = AsyncMock(return_value=1)

    def _locator(selector: str) -> MagicMock:
        if selector == DIALOG_ANY:
            return dialog
        if selector == PICKER_SEARCH_INPUT:
            return search
        return tile

    page.locator = MagicMock(side_effect=_locator)
    return page


@pytest.fixture
def picker_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    tile = _tile_mock()
    page = _page_with_tile(tile)
    search = page.locator(PICKER_SEARCH_INPUT)
    dialog = page.locator(DIALOG_ANY)
    add = MagicMock()
    add.first = add
    add.wait_for = AsyncMock()
    add.click = AsyncMock()

    base_locator = page.locator

    def _locator(selector: str) -> MagicMock:
        if selector == ADD_MEDIA_BUTTON:
            return add
        if selector == PICKER_SEARCH_INPUT:
            return search
        if selector == DIALOG_ANY:
            return dialog
        return base_locator(selector)

    page.locator = MagicMock(side_effect=_locator)
    scroll = AsyncMock(return_value=False)
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_scroll_picker_grid_until_rendered",
        scroll,
    )
    upload = AsyncMock()
    monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", upload)
    return {
        "media_id": _MEDIA_ID,
        "display_name": "",
        "local_path": "",
        "local_sha256": "",
        "page": page,
        "tile": tile,
        "search": search,
        "scroll": scroll,
        "upload": upload,
        "other_tile": None,
        "mode": "image",
    }


@given(parsers.parse('an image UUID reference named "{display_name}"'))
def _given_image_ref(picker_state: dict[str, Any], display_name: str) -> None:
    picker_state["display_name"] = display_name
    picker_state["mode"] = "image"


@given(parsers.parse('a video frame UUID named "{display_name}"'))
def _given_video_ref(
    picker_state: dict[str, Any], display_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    picker_state["display_name"] = display_name
    picker_state["mode"] = "video"
    slot = MagicMock()
    slot.click = AsyncMock()
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_resolve_frame_slot",
        AsyncMock(return_value=slot),
    )
    monkeypatch.setattr(VideoGenerationMixin, "_sync_picker_project", AsyncMock())


@given("picker search surfaces the exact UUID tile")
def _given_exact_tile(picker_state: dict[str, Any]) -> None:
    picker_state["tile"].wait_for = AsyncMock()


@given("another picker tile has the same display name")
def _given_name_collision(picker_state: dict[str, Any]) -> None:
    other = MagicMock()
    other.click = AsyncMock()
    picker_state["other_tile"] = other


@given("the exact UUID tile is absent from the picker")
def _given_missing_tile(picker_state: dict[str, Any]) -> None:
    picker_state["tile"].wait_for = AsyncMock(side_effect=TimeoutError("not found"))


@given("the catalog has a recorded local fallback")
def _given_local_fallback(picker_state: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "recorded-ref.png"
    content = b"recorded image"
    path.write_bytes(content)
    picker_state["local_path"] = str(path)
    picker_state["local_sha256"] = hashlib.sha256(content).hexdigest()


@when("the transport binds the image reference")
def _bind_image(picker_state: dict[str, Any]) -> None:
    asyncio.run(
        VideoGenerationMixin._attach_image_uuid_refs(
            picker_state["page"],
            [
                (
                    picker_state["media_id"],
                    picker_state["display_name"],
                    picker_state["local_path"],
                    picker_state["local_sha256"],
                )
            ],
            out_dir=None,
        )
    )


@when("the transport binds the video frame")
def _bind_video(picker_state: dict[str, Any]) -> None:
    asyncio.run(
        VideoGenerationMixin._attach_frame_by_media_id(
            picker_state["page"],
            0,
            "Start",
            picker_state["media_id"],
            picker_state["display_name"],
            out_dir=None,
        )
    )


@then("only the catalog display name is typed into picker search")
def _then_name_only(picker_state: dict[str, Any]) -> None:
    assert [call.args[0] for call in picker_state["search"].press_sequentially.await_args_list] == [
        picker_state["display_name"]
    ]


@then("the exact UUID tile is attached")
def _then_exact_tile_attached(picker_state: dict[str, Any]) -> None:
    picker_state["tile"].click.assert_awaited_once()


@then("no grid scroll is attempted")
def _then_no_scroll(picker_state: dict[str, Any]) -> None:
    picker_state["scroll"].assert_not_awaited()
    picker_state["page"].mouse.wheel.assert_not_awaited()


@then("the target locator contains the requested UUID")
def _then_uuid_locator(picker_state: dict[str, Any]) -> None:
    expected = f"[role='option']:has(img[src*='{picker_state['media_id']}'])"
    assert any(call.args == (expected,) for call in picker_state["page"].locator.call_args_list)


@then("the other same-name tile is not attached")
def _then_other_not_attached(picker_state: dict[str, Any]) -> None:
    picker_state["other_tile"].click.assert_not_awaited()


@then("the recorded local file is uploaded")
def _then_uploaded(picker_state: dict[str, Any]) -> None:
    picker_state["upload"].assert_awaited_once()
    assert picker_state["upload"].await_args.args[1] == Path(picker_state["local_path"])
