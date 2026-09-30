"""Unit tests for migrated_video_upscale (flow.google.com)."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.migrated_video_upscale import upscale_video_migrated
from gflow_cli.errors import UpscaleUnavailableError

_DUMMY_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
_DUMMY_B64 = base64.b64encode(_DUMMY_MP4).decode("ascii")


@pytest.mark.asyncio
async def test_migrated_video_upscale_1080p_happy_path(tmp_path) -> None:
    out_path = tmp_path / "upscaled_1080p.mp4"

    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    tile = MagicMock()
    tile.click = AsyncMock()

    download_btn = MagicMock()
    download_btn.click = AsyncMock()

    btn_1080p = MagicMock()
    btn_1080p.is_disabled = AsyncMock(return_value=False)
    btn_1080p.click = AsyncMock()

    async def wait_for_selector(sel, **kwargs):
        if "data-media-id" in sel or "flow-grid-tile-container" in sel:
            return tile
        if "download" in sel:
            return download_btn
        if "1080p" in sel:
            return btn_1080p
        return None

    page.wait_for_selector = AsyncMock(side_effect=wait_for_selector)

    # evaluate: first call sets up hooks, subsequent polls return b64
    eval_call_count = 0

    async def evaluate_mock(expr, *args):
        nonlocal eval_call_count
        if "window._videoCapturedBase64" in expr:
            eval_call_count += 1
            if eval_call_count >= 1:
                return _DUMMY_B64
        return None

    page.evaluate = AsyncMock(side_effect=evaluate_mock)

    result = await upscale_video_migrated(
        page,
        project_id="9f4b4bce-b192-4687-a636-89d4e8c5ba98",
        media_id="412832b1-3685-46f9-a5da-49c472d18a23",
        scale="1080p",
        out_path=out_path,
        timeout_s=5.0,
    )

    assert result == out_path
    assert out_path.exists()
    assert out_path.read_bytes() == _DUMMY_MP4


@pytest.mark.asyncio
async def test_migrated_video_upscale_invalid_scale(tmp_path) -> None:
    out_path = tmp_path / "upscaled.mp4"
    page = MagicMock()

    with pytest.raises(ValueError, match="Unsupported video upscale scale"):
        await upscale_video_migrated(
            page,
            project_id="9f4b4bce-b192-4687-a636-89d4e8c5ba98",
            media_id="412832b1-3685-46f9-a5da-49c472d18a23",
            scale="4k",  # Not valid for video
            out_path=out_path,
            timeout_s=5.0,
        )


@pytest.mark.asyncio
async def test_migrated_video_upscale_disabled_raises_unavailable(tmp_path) -> None:
    out_path = tmp_path / "upscaled.mp4"

    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    tile = MagicMock()
    tile.click = AsyncMock()

    download_btn = MagicMock()
    download_btn.click = AsyncMock()

    btn_1080p = MagicMock()
    btn_1080p.is_disabled = AsyncMock(return_value=True)  # disabled

    async def wait_for_selector(sel, **kwargs):
        if "data-media-id" in sel or "flow-grid-tile-container" in sel:
            return tile
        if "download" in sel:
            return download_btn
        if "1080p" in sel:
            return btn_1080p
        return None

    page.wait_for_selector = AsyncMock(side_effect=wait_for_selector)

    with pytest.raises(UpscaleUnavailableError, match="1080p video option is not available"):
        await upscale_video_migrated(
            page,
            project_id="9f4b4bce-b192-4687-a636-89d4e8c5ba98",
            media_id="412832b1-3685-46f9-a5da-49c472d18a23",
            scale="1080p",
            out_path=out_path,
            timeout_s=5.0,
        )
