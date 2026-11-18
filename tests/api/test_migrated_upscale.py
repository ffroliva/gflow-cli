"""Unit tests for migrated_upscale (flow.google.com)."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.api.transports.migrated_upscale import menu_token_pattern, upscale_image_migrated
from gflow_cli.errors import (
    TransportTimeoutError,
    UiSelectorDriftError,
    UpscaleUnavailableError,
    WireFormatError,
)
from tests.api._upscale_fakes import BATCHEXECUTE_URL, FakeItem, emit_response, fake_page

_PROJECT_ID = "00000000-0000-4000-8000-000000000001"
_MEDIA_ID = "00000000-0000-4000-8000-000000000002"
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode("ascii")


def _make_sprcad_body(b64_data: str) -> str:
    inner = json.dumps([["metadata"], b64_data])
    row = json.dumps([["wrb.fr", "SPrCad", inner]])
    return ")]}'\n\n1234\n" + row


def _make_sprcad_error_body() -> str:
    status = [7, None, [["type.googleapis.com/google.rpc.ErrorInfo", ["FAILED"]]]]
    row = json.dumps([["wrb.fr", "SPrCad", None, None, None, status]])
    return ")]}'\n\n1234\n" + row


async def _run(page, resolution=TargetResolution.RES_2K, timeout_s=5.0) -> bytes:
    return await upscale_image_migrated(
        page,
        project_id=_PROJECT_ID,
        media_id=_MEDIA_ID,
        target_resolution=resolution,
        timeout_s=timeout_s,
    )


def _replying_item(text: str, body: str) -> tuple[FakeItem, list]:
    holder: list = []

    async def reply() -> None:
        await emit_response(holder[0], body, rpcid="SPrCad")

    return FakeItem(text, on_click=reply), holder


@pytest.mark.parametrize(
    ("token", "text", "matches"),
    [
        ("2K", "2K", True),
        ("2K", "2K (Aprimorada)", True),
        ("2K", "Upscaled 2k", True),
        ("2K", "12K", False),
        ("2K", "2KB", False),
        ("1K", "1K Tamanho original", True),
        ("1080p", "1080p Full HD", True),
        ("080p", "1080p", False),
        ("720p", "1080p", False),
        ("270p", "GIF animado 270p", True),
    ],
)
def test_menu_token_pattern_matches_whole_token(token: str, text: str, matches: bool) -> None:
    assert bool(menu_token_pattern(token).search(text)) is matches


async def test_migrated_upscale_2k_happy_path() -> None:
    item_2k, holder = _replying_item("2K", _make_sprcad_body(_PNG_B64))
    page = fake_page([FakeItem("1K"), item_2k, FakeItem("4K", disabled=True)])
    holder.append(page)

    assert await _run(page) == _PNG_BYTES
    assert item_2k.clicked


async def test_migrated_upscale_picks_token_from_pt_labels_in_any_order() -> None:
    """pt labels and a reordered menu still select the 2K item, never 12K or 1K."""
    item_2k, holder = _replying_item("2K (Aprimorada)", _make_sprcad_body(_PNG_B64))
    decoys = [FakeItem("4K (Aprimorada)", disabled=True), FakeItem("12K"), FakeItem("1K")]
    page = fake_page([decoys[0], decoys[1], item_2k, decoys[2]])
    holder.append(page)

    assert await _run(page) == _PNG_BYTES
    assert item_2k.clicked
    assert not any(d.clicked for d in decoys)


async def test_migrated_upscale_4k_disabled_raises_unavailable() -> None:
    page = fake_page([FakeItem("1K"), FakeItem("2K"), FakeItem("4K", disabled=True)])

    with pytest.raises(UpscaleUnavailableError, match="4K upscale requires a Flow Ultra"):
        await _run(page, TargetResolution.RES_4K)


async def test_migrated_upscale_scale_flow_does_not_list_is_unavailable() -> None:
    page = fake_page([FakeItem("1K"), FakeItem("2K"), FakeItem("14K")])

    with pytest.raises(UpscaleUnavailableError, match="Flow does not offer 4K") as exc_info:
        await _run(page, TargetResolution.RES_4K)
    assert "lists 1K, 2K, 14K" in exc_info.value.detail
    assert "gflow can export: 2K." in exc_info.value.remediation_hint


async def test_migrated_upscale_menu_without_any_scale_token_raises_drift() -> None:
    page = fake_page([FakeItem("Original size"), FakeItem("Enhanced")])

    with pytest.raises(UiSelectorDriftError, match="menu item for 4K was not found"):
        await _run(page, TargetResolution.RES_4K)


async def test_migrated_upscale_menu_never_opens_raises_drift() -> None:
    page = fake_page([])

    with pytest.raises(UiSelectorDriftError, match="download menu did not open") as exc_info:
        await _run(page)
    assert exc_info.value.charged == "no"  # #1320: before the billed menu click


async def test_migrated_upscale_missing_tile_raises_drift() -> None:
    page = fake_page([FakeItem("2K")], tile=False)

    with pytest.raises(UiSelectorDriftError, match="image tile for media_id") as exc_info:
        await _run(page)
    # A wrong id/project is likelier than a frontend change; the class default blames Google.
    assert "--project" in exc_info.value.remediation_hint
    # #1067: the 30 s wait spans three grid loads, not one.
    assert page.reload.await_count == 2


async def test_migrated_upscale_missing_download_button_raises_drift() -> None:
    page = fake_page([FakeItem("2K")], download=False)

    with pytest.raises(UiSelectorDriftError, match="download button not found"):
        await _run(page)


async def test_migrated_upscale_timeout_raises_transport_timeout() -> None:
    page = fake_page([FakeItem("2K")])

    with pytest.raises(TransportTimeoutError, match="Timed out after 0.01s"):
        await _run(page, timeout_s=0.01)


async def test_migrated_upscale_rpc_error_raises_wireformat() -> None:
    item_2k, holder = _replying_item("2K", _make_sprcad_error_body())
    page = fake_page([item_2k])
    holder.append(page)

    with pytest.raises(WireFormatError, match="SPrCad RPC refused"):
        await _run(page)


async def test_migrated_upscale_non_image_payload_raises_wireformat() -> None:
    """A JPEG needs FF D8 FF, not just FF D8."""
    bogus = base64.b64encode(b"\xff\xd8\x00not-a-jpeg").decode("ascii")
    item_2k, holder = _replying_item("2K", _make_sprcad_body(bogus))
    page = fake_page([item_2k])
    holder.append(page)

    with pytest.raises(WireFormatError, match="not a valid PNG/JPEG"):
        await _run(page)


def _lost_reply_item() -> tuple[FakeItem, list]:
    """A 2K item whose SPrCad reply body Chrome no longer has (#1027)."""
    holder: list = []

    async def lost() -> None:
        res = MagicMock()
        res.url = f"{BATCHEXECUTE_URL}?rpcids=SPrCad"
        res.text = AsyncMock(
            side_effect=RuntimeError(
                "Response.text: Protocol error (Network.getResponseBody): "
                "No data found for resource with given identifier"
            )
        )
        for handler in list(holder[0].response_handlers):
            await handler(res)

    return FakeItem("2K", on_click=lost), holder


async def test_migrated_upscale_unreadable_reply_still_returns_the_image() -> None:
    """#1027: Chrome lost the SPrCad body; the image the page received is still returned."""
    item, holder = _lost_reply_item()
    page = fake_page([item], captured_b64=_PNG_B64)
    holder.append(page)

    assert await _run(page) == _PNG_BYTES


async def test_migrated_upscale_unreadable_reply_and_no_capture_times_out() -> None:
    item, holder = _lost_reply_item()
    page = fake_page([item])
    holder.append(page)

    with pytest.raises(TransportTimeoutError, match="Timed out after 0.2s"):
        await _run(page, timeout_s=0.2)


async def test_migrated_upscale_readable_refusal_wins_over_a_captured_blob() -> None:
    item_2k, holder = _replying_item("2K", _make_sprcad_error_body())
    page = fake_page([item_2k], captured_b64=_PNG_B64)
    holder.append(page)

    with pytest.raises(WireFormatError, match="SPrCad RPC refused"):
        await _run(page)
