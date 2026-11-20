"""Installed CLI and MCP end-to-end checks for local captions (#1358)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_media]


def _assert_caption_pixels(source: Path, output: Path) -> None:
    av = pytest.importorskip("av")
    with av.open(str(source)) as original, av.open(str(output)) as rendered:
        fps = float(original.streams.video[0].average_rate or 10)
        source_frames = list(original.decode(video=0))
        output_frames = list(rendered.decode(video=0))
    assert len(source_frames) == len(output_frames)
    caption_diffs: list[float] = []
    plain_diffs: list[float] = []
    for index, (before, after) in enumerate(zip(source_frames, output_frames, strict=True)):
        diff = float(
            np.mean(np.abs(before.to_ndarray().astype(float) - after.to_ndarray().astype(float)))
        )
        (caption_diffs if 0.2 <= index / fps <= 1.0 else plain_diffs).append(diff)
    assert caption_diffs and max(caption_diffs) > 1
    assert not plain_diffs or max(plain_diffs) < 2


@dataclass(frozen=True)
class InstalledCli:
    python: Path


@pytest.fixture(scope="module")
def installed_cli(tmp_path_factory: pytest.TempPathFactory) -> Iterator[InstalledCli]:
    root = Path(__file__).resolve().parent.parent.parent
    dist = tmp_path_factory.mktemp("captions-dist")
    env_dir = tmp_path_factory.mktemp("captions-venv")
    subprocess.run(["uv", "build", "--wheel", "-o", str(dist)], cwd=root, check=True, timeout=600)
    wheel = next(dist.glob("gflow_cli-*.whl"))
    subprocess.run(
        ["uv", "venv", str(env_dir), "--python", sys.executable], check=True, timeout=600
    )
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(wheel), "av>=12", "pillow>=13"],
        check=True,
        timeout=900,
    )
    numpy = subprocess.run(
        [str(python), "-c", "import numpy"], capture_output=True, check=False, timeout=30
    )
    assert numpy.returncode != 0, "the installed chain extra must not depend on NumPy"
    yield InstalledCli(python)


@pytest.fixture
def clip_files(tmp_path: Path) -> tuple[Path, Path]:
    av = pytest.importorskip("av")
    clip, srt = tmp_path / "clip with spaces.mkv", tmp_path / "sample.srt"
    with av.open(str(clip), mode="w", format="matroska") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
        speech = Path(__file__).resolve().parents[1] / "fixtures" / "video_captions_speech.wav"
        with wave.open(str(speech), "rb") as source_audio:
            sample_rate = source_audio.getframerate()
            audio = output.add_stream("pcm_s16le", rate=sample_rate)
            audio.layout = "mono"
            for index in range(90):
                frame = av.VideoFrame(160, 90, "rgb24")
                frame.pts = index
                for packet in video.encode(frame):
                    output.mux(packet)
            for packet in video.encode():
                output.mux(packet)
            sample_offset = 0
            while raw := source_audio.readframes(2048):
                frame = av.AudioFrame(format="s16", layout="mono", samples=len(raw) // 2)
                frame.sample_rate = sample_rate
                frame.pts = sample_offset
                frame.time_base = Fraction(1, sample_rate)
                frame.planes[0].update(raw)
                sample_offset += frame.samples
                for packet in audio.encode(frame):
                    output.mux(packet)
            for packet in audio.encode():
                output.mux(packet)
    srt.write_text("1\n00:00:00,200 --> 00:00:01,000\nHello captions\n", encoding="utf-8")
    return clip, srt


def test_installed_cli_burns_authored_srt(
    installed_cli: InstalledCli, clip_files: tuple[Path, Path], tmp_path: Path
) -> None:
    clip, srt = clip_files
    output = tmp_path / "cli-output.mp4"
    result = subprocess.run(
        [
            str(installed_cli.python),
            "-m",
            "gflow_cli",
            "video",
            "captions",
            str(clip),
            "--srt",
            str(srt),
            "--out-file",
            str(output),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "GFLOW_CLI_OUTPUT_DIR": str(tmp_path / "out")},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["frames_burned"] > 0
    _assert_caption_pixels(clip, output)
    av = pytest.importorskip("av")
    with av.open(str(output)) as video:
        assert video.streams.audio


@pytest.mark.asyncio
async def test_mcp_tool_burns_srt_inside_output_directory(
    clip_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.config import reset_settings
    from gflow_cli.mcp.video_captions_tools import gflow_video_captions

    clip, srt = clip_files
    output_dir = tmp_path / "mcp-output"
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(output_dir))
    reset_settings()
    result = await gflow_video_captions(str(clip), srt=str(srt))
    reset_settings()
    assert result["status"] == "ok", result
    assert Path(result["output_file"]).is_relative_to(output_dir.resolve())
    assert result["frames_burned"] > 0
    _assert_caption_pixels(clip, Path(result["output_file"]))


@pytest.mark.asyncio
async def test_default_endpoint_error_reaches_cli_and_mcp(
    installed_cli: InstalledCli,
    clip_files: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.config import reset_settings
    from gflow_cli.mcp.video_captions_tools import gflow_video_transcribe

    clip, _ = clip_files
    env = {**os.environ, "GFLOW_CLI_OUTPUT_DIR": str(tmp_path / "out")}
    env.pop("GFLOW_CLI_LLM_BASE_URL", None)
    cli_result = subprocess.run(
        [str(installed_cli.python), "-m", "gflow_cli", "video", "transcribe", str(clip), "--json"],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert cli_result.returncode == 11
    cli_error = json.loads(cli_result.stdout)["error"]
    assert cli_error["type"] == "https://gflow-cli.dev/errors/configuration"
    assert cli_error["charged"] == "no" and cli_error["next_action"] == "fix_input"

    monkeypatch.delenv("GFLOW_CLI_LLM_BASE_URL", raising=False)
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "mcp-out"))
    reset_settings()
    mcp_result = await gflow_video_transcribe(str(clip))
    reset_settings()
    assert mcp_result["status"] == "error"
    assert mcp_result["error"]["type"] == cli_error["type"]
    assert mcp_result["error"]["charged"] == "no"


@pytest.mark.asyncio
async def test_installed_cli_and_mcp_transcribe_with_configured_whisper_endpoint(
    installed_cli: InstalledCli, clip_files: tuple[Path, Path], tmp_path: Path
) -> None:
    endpoint = os.environ.get("GFLOW_CLI_E2E_TRANSCRIBE_BASE_URL")
    if not endpoint:
        pytest.skip(
            "GFLOW_CLI_E2E_TRANSCRIBE_BASE_URL is unset; the local Whisper-compatible server "
            "is an external coordinator prerequisite."
        )
    clip, _ = clip_files
    env = {
        **os.environ,
        "GFLOW_CLI_LLM_BASE_URL": endpoint,
        "GFLOW_CLI_TRANSCRIBE_MODEL": os.environ.get("GFLOW_CLI_TRANSCRIBE_MODEL", "whisper-1"),
        "GFLOW_CLI_OUTPUT_DIR": str(tmp_path / "out"),
    }
    result = subprocess.run(
        [str(installed_cli.python), "-m", "gflow_cli", "video", "transcribe", str(clip), "--json"],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    srt_file = Path(json.loads(result.stdout)["srt_file"])
    assert srt_file.is_file() and srt_file.read_text(encoding="utf-8").strip()

    from gflow_cli.config import reset_settings
    from gflow_cli.mcp.video_captions_tools import gflow_video_transcribe

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", endpoint)
    monkeypatch.setenv("GFLOW_CLI_TRANSCRIBE_MODEL", env["GFLOW_CLI_TRANSCRIBE_MODEL"])
    monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "mcp-out"))
    reset_settings()
    mcp_result = await gflow_video_transcribe(str(clip))
    reset_settings()
    monkeypatch.undo()
    assert mcp_result["status"] == "ok", mcp_result
    assert Path(mcp_result["srt_file"]).read_text(encoding="utf-8").strip()
