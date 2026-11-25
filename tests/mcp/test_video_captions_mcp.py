"""MCP contract and output confinement for local video captions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gflow_cli.config import reset_settings
from gflow_cli.mcp.server import server
from gflow_cli.mcp.video_captions_tools import gflow_video_captions, gflow_video_transcribe


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


def _plant_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"file symlink creation not permitted: {exc}")


@pytest.mark.asyncio
async def test_mcp_rejects_default_output_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.mcp import video_captions_tools as tools

    output = tmp_path / "out"
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(output))
    reset_settings()
    dispatched: list[str] = []
    monkeypatch.setattr(
        tools, "transcribe_video", lambda *_a, **_k: dispatched.append("transcribe")
    )
    monkeypatch.setattr(tools, "caption_video", lambda *_a, **_k: dispatched.append("captions"))
    try:
        _plant_symlink(output / "clip.srt", outside / "stolen.srt")
        transcribe_res = await gflow_video_transcribe("clip.mp4")
        assert transcribe_res["status"] == "error"
        assert transcribe_res["error"]["title"] == "Invalid Output"
        assert "outside the configured output directory" in transcribe_res["error"]["detail"]

        explicit_out_auto_srt_res = await gflow_video_captions("clip.mp4", out_file="safe.mp4")
        assert explicit_out_auto_srt_res["status"] == "error"
        assert explicit_out_auto_srt_res["error"]["title"] == "Invalid Output"
        assert (
            "outside the configured output directory"
            in explicit_out_auto_srt_res["error"]["detail"]
        )
        (output / "clip.srt").unlink()

        _plant_symlink(output / "clip-captions.mp4", outside / "stolen.mp4")
        captions_res = await gflow_video_captions("clip.mp4", srt=str(tmp_path / "authored.srt"))
        assert captions_res["status"] == "error"
        assert captions_res["error"]["title"] == "Invalid Output"
        assert "outside the configured output directory" in captions_res["error"]["detail"]
        assert dispatched == []
    finally:
        reset_settings()


@pytest.mark.asyncio
async def test_mcp_rejects_directory_at_default_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.mcp import video_captions_tools as tools

    output = tmp_path / "out"
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(output))
    reset_settings()
    dispatched: list[str] = []
    monkeypatch.setattr(
        tools, "transcribe_video", lambda *_a, **_k: dispatched.append("transcribe")
    )
    monkeypatch.setattr(tools, "caption_video", lambda *_a, **_k: dispatched.append("captions"))
    try:
        (output / "clip.srt").mkdir(parents=True)
        transcribe_res = await gflow_video_transcribe("clip.mp4")
        assert transcribe_res["status"] == "error"
        assert transcribe_res["error"]["title"] == "Invalid Output"
        assert "is a directory, not a file" in transcribe_res["error"]["detail"]

        explicit_out_auto_srt_res = await gflow_video_captions("clip.mp4", out_file="safe.mp4")
        assert explicit_out_auto_srt_res["status"] == "error"
        assert explicit_out_auto_srt_res["error"]["title"] == "Invalid Output"
        assert "is a directory, not a file" in explicit_out_auto_srt_res["error"]["detail"]
        (output / "clip.srt").rmdir()

        (output / "clip-captions.mp4").mkdir(parents=True)
        captions_res = await gflow_video_captions("clip.mp4", srt=str(tmp_path / "authored.srt"))
        assert captions_res["status"] == "error"
        assert captions_res["error"]["title"] == "Invalid Output"
        assert "is a directory, not a file" in captions_res["error"]["detail"]
        assert dispatched == []
    finally:
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


def test_video_captions_adapter_imports_in_fresh_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import gflow_cli.mcp.video_captions_tools"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_mcp_catalog_lookup_leaves_profile_unscoped_by_default() -> None:
    from gflow_cli.mcp.video_captions_tools import _profile

    assert _profile("11111111-2222-3333-4444-555555555555", None) is None


@pytest.mark.asyncio
async def test_mcp_refuses_explicit_out_file_under_cloud_storage_and_allows_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.mcp import video_captions_tools as tools

    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("GFLOW_CLI_STORAGE_URI", "s3://bucket/output")
    reset_settings()
    try:
        transcribe_err = await gflow_video_transcribe("clip.mp4", out_file="custom.srt")
        assert transcribe_err["status"] == "error"
        assert "GFLOW_CLI_STORAGE_URI" in transcribe_err["error"]["detail"]
        assert transcribe_err["error"]["charged"] == "no"

        captions_err = await gflow_video_captions(
            "clip.mp4", srt=str(tmp_path / "a.srt"), out_file="custom.mp4"
        )
        assert captions_err["status"] == "error"
        assert "GFLOW_CLI_STORAGE_URI" in captions_err["error"]["detail"]
        assert captions_err["error"]["charged"] == "no"

        monkeypatch.setattr(
            tools,
            "caption_video",
            lambda file, **kwargs: SimpleNamespace(
                source_file=Path(file),
                output_file=kwargs["out_file"],
                srt_file=kwargs["srt_path"],
                duration_seconds=1.0,
                total_frames=1,
                frames_burned=1,
                bytes=10,
            ),
        )
        plain_ok = await gflow_video_captions("clip.mp4", srt=str(tmp_path / "a.srt"))
        assert plain_ok["status"] == "ok"
    finally:
        reset_settings()


@pytest.mark.asyncio
async def test_mcp_captions_forwards_style_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.mcp import video_captions_tools as tools

    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
    reset_settings()
    captured: dict[str, object] = {}

    def fake_caption(file: str, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            source_file=Path(file),
            output_file=kwargs["out_file"],
            srt_file=kwargs["srt_path"],
            duration_seconds=1.0,
            total_frames=1,
            frames_burned=1,
            bytes=10,
        )

    monkeypatch.setattr(tools, "caption_video", fake_caption)
    try:
        res = await gflow_video_captions(
            "clip.mp4",
            srt=str(tmp_path / "a.srt"),
            position="top",
            margin=48,
            font_size=28,
            box_opacity=0.5,
        )
        assert res["status"] == "ok"
        assert captured["position"] == "top"
        assert captured["margin"] == 48
        assert captured["font_size"] == 28
        assert captured["box_opacity"] == 0.5
    finally:
        reset_settings()


def test_mcp_captions_schema_exposes_style_bounds_and_matches_plugin_manifest() -> None:
    import json

    tool = server._tool_manager._tools["gflow_video_captions"]
    props = tool.parameters["properties"]
    assert props["position"]["enum"] == ["bottom", "top", "middle"]
    assert props["position"]["default"] == "bottom"
    assert props["margin"]["minimum"] == 0
    assert props["margin"]["default"] == 28
    font_int = next(item for item in props["font_size"]["anyOf"] if item.get("type") == "integer")
    assert font_int["minimum"] == 1
    assert font_int["maximum"] == 1000
    opacity_num = next(
        item for item in props["box_opacity"]["anyOf"] if item.get("type") == "number"
    )
    assert opacity_num["minimum"] == 0.0
    assert opacity_num["maximum"] == 1.0

    manifest_path = Path(__file__).resolve().parents[2] / "lhm.plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_entry = next(t for t in manifest["tools"] if t["name"] == "gflow_video_captions")
    assert manifest_entry["inputSchema"] == tool.parameters


@pytest.mark.asyncio
@pytest.mark.parametrize(("raw_position", "expected"), [("Top", "top"), (" MIDDLE ", "middle")])
async def test_mcp_captions_accepts_case_insensitive_position_on_wire(
    raw_position: str,
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp import Client

    from gflow_cli.mcp import video_captions_tools as tools

    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
    reset_settings()
    captured: dict[str, object] = {}

    def fake_caption(file: str, **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            source_file=Path(file),
            output_file=kwargs["out_file"],
            srt_file=kwargs["srt_path"],
            duration_seconds=1.0,
            total_frames=1,
            frames_burned=1,
            bytes=10,
        )

    monkeypatch.setattr(tools, "caption_video", fake_caption)
    try:
        async with Client(server, mode="2026-07-28") as client:
            result = await client.call_tool(
                "gflow_video_captions",
                {"file": "clip.mp4", "srt": str(tmp_path / "a.srt"), "position": raw_position},
            )
        assert result.is_error is False
        assert captured["position"] == expected
    finally:
        reset_settings()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_args",
    [
        {"margin": -1},
        {"font_size": 0},
        {"font_size": 1001},
        {"box_opacity": -0.1},
        {"box_opacity": 1.1},
        {"position": "left"},
        {"margin": True},
        {"font_size": True},
        {"box_opacity": True},
    ],
)
async def test_mcp_captions_rejects_out_of_bounds_style_on_wire(
    bad_args: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp import Client

    from gflow_cli.mcp import video_captions_tools as tools

    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
    reset_settings()
    calls: list[object] = []
    monkeypatch.setattr(tools, "caption_video", lambda *a, **k: calls.append((a, k)))
    try:
        async with Client(server, mode="2026-07-28") as client:
            result = await client.call_tool(
                "gflow_video_captions",
                {"file": "clip.mp4", "srt": str(tmp_path / "a.srt"), **bad_args},
            )
        assert result.is_error is True
        assert calls == []
    finally:
        reset_settings()


@pytest.mark.asyncio
async def test_mcp_captions_invalid_style_returns_error_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
    reset_settings()
    try:
        err = await gflow_video_captions(
            "clip.mp4",
            srt=str(tmp_path / "a.srt"),
            position="left",
        )
        assert err["status"] == "error"
        assert err["error"]["charged"] == "no"
    finally:
        reset_settings()
