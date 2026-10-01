"""Unit tests for the gflow_upscale_image MCP tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.mcp.tools import gflow_upscale_image

_VALID_MEDIA_ID = "3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14"
_VALID_PROJECT_ID = "ffb768fb-cf2d-48b7-a135-92978667c37d"


@pytest.fixture(autouse=True)
def _mock_profile(monkeypatch) -> None:
    monkeypatch.setattr("gflow_cli.mcp.tools._resolve_and_validate_profile", lambda p: "default")


@pytest.mark.asyncio
async def test_upscale_invalid_scale() -> None:
    res = await gflow_upscale_image(media_id=_VALID_MEDIA_ID, scale="8k", project=_VALID_PROJECT_ID)
    assert res["status"] == "error"
    assert "Invalid Scale" in res["error"]["title"]


@pytest.mark.asyncio
async def test_upscale_invalid_media_id() -> None:
    res = await gflow_upscale_image(media_id="not-a-uuid", scale="2k", project=_VALID_PROJECT_ID)
    assert res["status"] == "error"
    assert "Invalid Media ID" in res["error"]["title"]


@pytest.mark.asyncio
async def test_upscale_invalid_project_id() -> None:
    res = await gflow_upscale_image(
        media_id=_VALID_MEDIA_ID, scale="2k", project="bad_id with spaces"
    )
    assert res["status"] == "error"
    assert "Invalid Project" in res["error"]["title"]


@pytest.mark.asyncio
async def test_upscale_missing_project_not_in_catalog(monkeypatch) -> None:
    monkeypatch.setattr("gflow_cli.mcp.tools.lookup_project_in_catalog", lambda m, p: None)

    res = await gflow_upscale_image(media_id=_VALID_MEDIA_ID, scale="2k", project=None)
    assert res["status"] == "error"
    assert "Project Required" in res["error"]["title"]


@pytest.mark.asyncio
async def test_upscale_happy_path(tmp_path: Path, monkeypatch) -> None:
    out_file = tmp_path / "upscaled.png"
    out_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    monkeypatch.setattr(
        "gflow_cli.mcp.tools.lookup_project_in_catalog",
        lambda m, p: _VALID_PROJECT_ID,
    )

    mock_client = AsyncMock()
    mock_client.upsample_image = AsyncMock(return_value=out_file)

    class FakeClientContext:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return mock_client

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    monkeypatch.setattr("gflow_cli.mcp.tools.FlowApiClient", FakeClientContext)

    res = await gflow_upscale_image(
        media_id=_VALID_MEDIA_ID,
        scale="2k",
        project=_VALID_PROJECT_ID,
        out_dir=str(tmp_path),
    )

    assert res["status"] == "ok"
    assert res["media_id"] == _VALID_MEDIA_ID
    assert res["project_id"] == _VALID_PROJECT_ID
    assert res["scale"] == "2k"
    assert res["path"] == str(out_file)
    assert res["bytes"] == out_file.stat().st_size
    mock_client.upsample_image.assert_awaited_once()
    assert (
        mock_client.upsample_image.call_args.kwargs["target_resolution"] == TargetResolution.RES_2K
    )


@pytest.mark.asyncio
async def test_video_upscale_invalid_scale() -> None:
    from gflow_cli.mcp.tools import gflow_upscale_video

    res = await gflow_upscale_video(
        media_id=_VALID_MEDIA_ID,
        scale="4k",  # 4k is not valid for video
        project=_VALID_PROJECT_ID,
    )
    assert res["status"] == "error"
    assert "Invalid Scale" in res["error"]["title"]


@pytest.mark.asyncio
async def test_video_upscale_happy_path(tmp_path: Path, monkeypatch) -> None:
    from gflow_cli.mcp.tools import gflow_upscale_video

    out_file = tmp_path / "upscaled_video.mp4"
    out_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)

    monkeypatch.setattr(
        "gflow_cli.mcp.tools.lookup_project_in_catalog",
        lambda m, p: _VALID_PROJECT_ID,
    )

    mock_client = AsyncMock()
    mock_client.upsample_video = AsyncMock(return_value=out_file)

    class FakeClientContext:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return mock_client

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    monkeypatch.setattr("gflow_cli.mcp.tools.FlowApiClient", FakeClientContext)

    res = await gflow_upscale_video(
        media_id=_VALID_MEDIA_ID,
        scale="1080p",
        project=_VALID_PROJECT_ID,
        out_dir=str(tmp_path),
    )

    assert res["status"] == "ok"
    assert res["media_id"] == _VALID_MEDIA_ID
    assert res["project_id"] == _VALID_PROJECT_ID
    assert res["scale"] == "1080p"
    assert res["path"] == str(out_file)
    assert res["bytes"] == out_file.stat().st_size
    mock_client.upsample_video.assert_awaited_once()
    assert mock_client.upsample_video.call_args.kwargs["scale"] == "1080p"
