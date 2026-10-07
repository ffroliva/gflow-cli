"""Unit tests for migrated_video_upscale (flow.google.com)."""

from __future__ import annotations

import base64
import json
import struct
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.migrated_video_upscale import (
    mp4_track_dimensions,
    upscale_video_migrated,
)
from gflow_cli.errors import (
    TransportTimeoutError,
    UiSelectorDriftError,
    UpscaleUnavailableError,
    WireFormatError,
)
from tests.api._upscale_fakes import FakeItem, emit_response, fake_page

_PROJECT_ID = "00000000-0000-4000-8000-000000000001"
_MEDIA_ID = "00000000-0000-4000-8000-000000000002"


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _tkhd(width: int, height: int, *, version: int = 0) -> bytes:
    times = b"\x00" * (20 if version == 0 else 32)  # ctime, mtime, track_id, rsv, duration
    middle = b"\x00" * (8 + 2 + 2 + 2 + 2 + 36)  # rsv, layer, alt group, volume, rsv, matrix
    tail = struct.pack(">II", width << 16, height << 16)  # 16.16 fixed point
    return _box(b"tkhd", bytes([version, 0, 0, 7]) + times + middle + tail)


def _mp4(*tracks: tuple[int, int], version: int = 0) -> bytes:
    ftyp = _box(b"ftyp", b"mp42\x00\x00\x00\x00mp42isom")
    traks = b"".join(_box(b"trak", _tkhd(w, h, version=version)) for w, h in tracks)
    return ftyp + _box(b"moov", _box(b"mvhd", b"\x00" * 100) + traks) + _box(b"mdat", b"\x00" * 64)


_MP4_1080 = _mp4((1920, 1080), (0, 0))  # video + audio track
_GIF = b"GIF89a" + b"\x00" * 32


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _video_menu(**disabled: bool) -> list[FakeItem]:
    """The measured en order: 270p, 720p, 1080p, 4K (disabled)."""
    return [
        FakeItem("270p", disabled=disabled.get("270p", False)),
        FakeItem("720p", disabled=disabled.get("720p", False)),
        FakeItem("1080p", disabled=disabled.get("1080p", False)),
        FakeItem("4K", disabled=True),
    ]


async def _run(page, scale: str = "1080p", timeout_s: float = 5.0) -> bytes:
    return await upscale_video_migrated(
        page, project_id=_PROJECT_ID, media_id=_MEDIA_ID, scale=scale, timeout_s=timeout_s
    )


# --- tkhd helper ---------------------------------------------------------------


def test_mp4_track_dimensions_reads_the_video_track() -> None:
    assert mp4_track_dimensions(_MP4_1080) == (1920, 1080)


def test_mp4_track_dimensions_handles_tkhd_version_1() -> None:
    assert mp4_track_dimensions(_mp4((1080, 1920), version=1)) == (1080, 1920)


def test_mp4_track_dimensions_none_without_moov() -> None:
    assert mp4_track_dimensions(_box(b"ftyp", b"mp42") + _box(b"mdat", b"\x00" * 8)) is None


def test_mp4_track_dimensions_tolerates_truncated_box() -> None:
    assert mp4_track_dimensions(_box(b"ftyp", b"mp42") + b"\x00\x00\x10\x00moov") is None


# --- transport -----------------------------------------------------------------


async def test_migrated_video_upscale_1080p_happy_path() -> None:
    menu = _video_menu()
    page = fake_page(menu, captured_b64=_b64(_MP4_1080))

    assert await _run(page) == _MP4_1080
    assert menu[2].clicked


async def test_migrated_video_upscale_accepts_portrait_1080p() -> None:
    portrait = _mp4((1080, 1920))
    page = fake_page(_video_menu(), captured_b64=_b64(portrait))

    assert await _run(page) == portrait


async def test_migrated_video_upscale_rejects_the_720p_original_for_1080p() -> None:
    page = fake_page(_video_menu(), captured_b64=_b64(_mp4((1280, 720))))

    with pytest.raises(WireFormatError, match="1280x720") as exc_info:
        await _run(page)
    assert "simpler prompt" not in exc_info.value.remediation_hint


async def test_migrated_video_upscale_rejects_mp4_without_track_dimensions() -> None:
    no_moov = _box(b"ftyp", b"mp42\x00\x00\x00\x00") + _box(b"mdat", b"\x00" * 64)
    page = fake_page(_video_menu(), captured_b64=_b64(no_moov))

    with pytest.raises(WireFormatError, match="track dimensions"):
        await _run(page)


async def test_migrated_video_upscale_720p_accepts_720p() -> None:
    clip = _mp4((1280, 720))
    page = fake_page(_video_menu(), captured_b64=_b64(clip))

    assert await _run(page, "720p") == clip


async def test_migrated_video_upscale_picks_token_from_pt_menu_in_any_order() -> None:
    """The contributor's pt capture: 720p, 1080p, 270p with translated words."""
    menu = [
        FakeItem("720p Tamanho original"),
        FakeItem("1080p (Aprimorada)"),
        FakeItem("270p GIF animado"),
        FakeItem("4K", disabled=True),
    ]
    page = fake_page(menu, captured_b64=_b64(_MP4_1080))

    assert await _run(page) == _MP4_1080
    assert [i.clicked for i in menu] == [False, True, False, False]


async def test_migrated_video_upscale_270p_gif() -> None:
    page = fake_page(_video_menu(), captured_b64=_b64(_GIF))

    assert await _run(page, "270p") == _GIF


async def test_migrated_video_upscale_invalid_scale() -> None:
    with pytest.raises(ValueError, match="Unsupported video upscale scale"):
        await _run(MagicMock(), "4k")


async def test_migrated_video_upscale_disabled_raises_unavailable() -> None:
    page = fake_page(_video_menu(**{"1080p": True}))

    with pytest.raises(UpscaleUnavailableError, match="1080p video option is not available"):
        await _run(page)


async def test_migrated_video_upscale_missing_menu_item_raises_drift() -> None:
    page = fake_page([FakeItem("270p"), FakeItem("720p"), FakeItem("11080p")])

    with pytest.raises(UiSelectorDriftError, match="menu item for 1080p was not found"):
        await _run(page)


async def test_migrated_video_upscale_missing_download_button_raises_drift() -> None:
    page = fake_page(_video_menu(), download=False)

    with pytest.raises(UiSelectorDriftError, match="download button not found"):
        await _run(page)


async def test_migrated_video_upscale_missing_tile_raises_drift() -> None:
    page = fake_page(_video_menu(), tile=False, download=False)

    with pytest.raises(UiSelectorDriftError, match="video tile for media_id") as exc_info:
        await _run(page)
    assert "--project" in exc_info.value.remediation_hint


async def test_migrated_video_upscale_rpc_refusal_fails_fast() -> None:
    status = [7, None, [["type.googleapis.com/google.rpc.ErrorInfo", ["FAILED"]]]]
    body = ")]}'\n\n1234\n" + json.dumps([["wrb.fr", "p0UkFb", None, None, None, status]])
    holder: list = []

    async def refuse() -> None:
        await emit_response(holder[0], body, rpcid="p0UkFb")

    page = fake_page([FakeItem("1080p", on_click=refuse)])
    holder.append(page)

    started = time.monotonic()
    with pytest.raises(WireFormatError, match="p0UkFb refused"):
        await _run(page, timeout_s=30.0)
    assert time.monotonic() - started < 5.0


async def test_migrated_video_upscale_unreadable_response_fails_with_wireformat() -> None:
    holder: list = []

    async def broken() -> None:
        res = MagicMock()
        res.url = "https://flow.google.com/_/x/data/batchexecute?rpcids=p0UkFb"
        res.text = AsyncMock(side_effect=RuntimeError("body gone"))
        for handler in list(holder[0].response_handlers):
            await handler(res)

    page = fake_page([FakeItem("1080p", on_click=broken)])
    holder.append(page)

    with pytest.raises(WireFormatError, match="body gone"):
        await _run(page, timeout_s=30.0)


async def test_migrated_video_upscale_timeout_raises_transport_timeout() -> None:
    page = fake_page(_video_menu())

    with pytest.raises(TransportTimeoutError, match="Timed out waiting for 1080p video stream"):
        await _run(page, timeout_s=0.01)


async def test_migrated_video_upscale_invalid_magic_bytes() -> None:
    page = fake_page(_video_menu(), captured_b64=_b64(b"not an mp4 file"))

    with pytest.raises(WireFormatError, match="not a valid MP4"):
        await _run(page)


async def test_migrated_video_upscale_invalid_magic_bytes_gif() -> None:
    page = fake_page(_video_menu(), captured_b64=_b64(b"not a gif file"))

    with pytest.raises(WireFormatError, match="not a valid GIF"):
        await _run(page, "270p")


async def test_migrated_video_upscale_undecodable_base64() -> None:
    page = fake_page(_video_menu(), captured_b64="bad-base64-length-!")

    with pytest.raises(WireFormatError, match="undecodable stream data"):
        await _run(page)
