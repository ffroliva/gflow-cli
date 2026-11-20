"""Direct MCP behavior for local video assembly."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import av
import numpy as np
import pytest

from gflow_cli.mcp import tools


def _clip(path: Path) -> None:
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width, stream.height = 64, 48
        stream.pix_fmt = "yuv420p"
        for index in range(5):
            frame = av.VideoFrame.from_ndarray(
                np.zeros((48, 64, 3), dtype=np.uint8), format="rgb24"
            )
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


@pytest.mark.asyncio
async def test_direct_tool_returns_assembly_result_and_keeps_no_spend_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "clip.mp4"
    _clip(source)
    monkeypatch.setattr(
        tools, "get_settings", lambda: SimpleNamespace(output_dir=tmp_path, storage_uri=None)
    )
    monkeypatch.setattr(tools, "resolve_assembly_input", lambda value, **_: source)

    result = await tools.gflow_video_assemble(clips=[str(source)], out_file="joined")

    assert result["status"] == "succeeded"
    assert result["clips_count"] == 1
    assert Path(result["output_file"]).is_file()
    assert result["video_stream"]["width"] == 64


@pytest.mark.asyncio
async def test_direct_tool_maps_local_failure_to_problem_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "clip.mp4"
    _clip(source)
    monkeypatch.setattr(
        tools, "get_settings", lambda: SimpleNamespace(output_dir=tmp_path, storage_uri=None)
    )
    monkeypatch.setattr(tools, "resolve_assembly_input", lambda value, **_: source)

    result = await tools.gflow_video_assemble(clips=[str(source)], out_file="clip.mp4")

    assert result["status"] == "error"
    assert result["error"]["type"] == "https://gflow-cli.dev/errors/video-assembly"
    assert result["error"]["charged"] == "no"
