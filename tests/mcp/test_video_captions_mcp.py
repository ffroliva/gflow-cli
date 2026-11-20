"""MCP contract and output confinement for local video captions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gflow_cli.config import reset_settings
from gflow_cli.mcp.server import server
from gflow_cli.mcp.tools import gflow_video_captions, gflow_video_transcribe


def test_caption_tools_are_registered_with_expected_parameters() -> None:
    tools = server._tool_manager._tools
    for name in ("gflow_video_transcribe", "gflow_video_captions"):
        assert name in tools
        assert "file" in tools[name].parameters["properties"]
        assert "out_file" in tools[name].parameters["properties"]


@pytest.mark.asyncio
async def test_mcp_rejects_output_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
    reset_settings()
    result = await gflow_video_captions("clip.mp4", out_file="../escape.mp4")
    assert result["status"] == "error"
    assert result["error"]["title"] == "Invalid Output"
    reset_settings()


@pytest.mark.asyncio
async def test_mcp_default_outputs_stay_under_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.mcp import video_captions_tools as tools

    output = tmp_path / "output"
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(output))
    reset_settings()
    called: dict[str, object] = {}

    def fake_caption(file: str, **kwargs: object) -> SimpleNamespace:
        called.update(kwargs)
        return SimpleNamespace(
            source_file=Path(file),
            output_file=kwargs["out_file"],
            srt_file=kwargs["srt_out_file"],
            duration_seconds=1.0,
            total_frames=1,
            frames_burned=0,
            bytes=10,
        )

    monkeypatch.setattr(tools, "caption_video", fake_caption)
    result = await gflow_video_captions("C:/private/clip.mp4")
    assert result["output_file"].startswith(str(output.resolve()))
    assert result["srt_file"].startswith(str(output.resolve()))
    reset_settings()


@pytest.mark.asyncio
async def test_transcribe_tool_is_credit_free_and_returns_guarded_error() -> None:
    result = await gflow_video_transcribe("missing.mp4")
    assert result["status"] == "error"
    assert result["error"]["charged"] == "no"
