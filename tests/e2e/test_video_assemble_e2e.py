"""Installed-wheel CLI and MCP coverage for local video assembly."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import av
import numpy as np
import pytest

from tests.e2e.test_telemetry_local_e2e import InstalledGFlow
from tests.e2e.test_telemetry_local_e2e import clean_home as _clean_home_fixture  # noqa: F401
from tests.e2e.test_telemetry_local_e2e import installed_gflow as _installed_gflow_fixture  # noqa: F401

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_media]


def _clip(path: Path, *, size: tuple[int, int]) -> None:
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        for index in range(5):
            frame = av.VideoFrame.from_ndarray(
                np.zeros((size[1], size[0], 3), dtype=np.uint8), format="rgb24"
            )
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


def _audio(path: Path, *, frequency: float) -> None:
    rate = 44100
    samples = np.arange(rate // 2, dtype=np.float32) / rate
    tone = (0.2 * np.sin(2 * np.pi * frequency * samples)).astype(np.float32)
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("aac", rate=rate)
        stream.layout = "stereo"
        stream.format = "fltp"
        for start in range(0, len(tone), 1024):
            block = np.vstack([tone[start : start + 1024], tone[start : start + 1024]])
            frame = av.AudioFrame.from_ndarray(block, format="fltp", layout="stereo")
            frame.sample_rate = rate
            frame.pts = start
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


@pytest.fixture
def installed_chain_gflow(request: pytest.FixtureRequest) -> InstalledGFlow:
    """Add the optional media dependencies to the already-installed wheel's venv."""
    installed_gflow = request.getfixturevalue("_installed_gflow_fixture")
    assert isinstance(installed_gflow, InstalledGFlow)
    result = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(installed_gflow.python),
            "av>=12",
            "numpy>=1.24",
            "pillow>=13",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, f"installing [chain] dependencies failed:\n{result.stderr}"
    return installed_gflow


@pytest.mark.asyncio
async def test_installed_cli_and_mcp_assemble_local_media(
    installed_chain_gflow: InstalledGFlow,
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    clean_home_data = request.getfixturevalue("_clean_home_fixture")
    assert isinstance(clean_home_data, tuple)
    _, base_env = clean_home_data
    clip_a, clip_b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    music, voice = tmp_path / "music.m4a", tmp_path / "voice.m4a"
    realistic = request.getfixturevalue("assembly_media")
    assert isinstance(realistic, dict)
    clip_a.write_bytes(realistic["clip"].read_bytes())
    clip_b.write_bytes(realistic["clip"].read_bytes())
    music.write_bytes(realistic["music"].read_bytes())
    voice.write_bytes(realistic["voice"].read_bytes())

    cli_output = tmp_path / "cli.mp4"
    cli = subprocess.run(
        [
            str(installed_chain_gflow.gflow),
            "video",
            "assemble",
            str(clip_a),
            str(clip_b),
            "--music",
            str(music),
            "--voiceover",
            str(voice),
            "--out-file",
            str(cli_output),
            "--json",
        ],
        cwd=tmp_path,
        env=base_env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cli.returncode == 0, cli.stderr
    cli_result = json.loads(cli.stdout)
    assert cli_result["status"] == "succeeded"
    assert cli_result["clips_count"] == 2
    assert cli_result["ducking_applied"] is True
    assert cli_output.is_file()

    mcp_output = tmp_path / "mcp-output"
    mcp_output.mkdir()
    env = {**base_env, "GFLOW_CLI_OUTPUT_DIR": str(mcp_output)}
    params = StdioServerParameters(
        command=str(installed_chain_gflow.gflow),
        args=["mcp", "run", "--no-spend"],
        env=env,
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            available = await session.list_tools()
            assert "gflow_video_assemble" in {tool.name for tool in available.tools}
            result = await session.call_tool(
                "gflow_video_assemble",
                {
                    "clips": [str(clip_a), str(clip_b)],
                    "out_file": "assembled.mp4",
                    "music": str(music),
                    "voiceover": str(voice),
                },
            )
            assert not result.is_error
            mcp_result = json.loads(result.content[0].text)
            assert mcp_result["status"] == "succeeded"
            assert mcp_result["clips_count"] == 2
            assert mcp_result["ducking_applied"] is True
            assert (mcp_output / "assembled.mp4").is_file()

            failed = await session.call_tool(
                "gflow_video_assemble",
                {"clips": [str(clip_a)], "out_file": "bad.mp4", "clip_volume": 2},
            )
            error = json.loads(failed.content[0].text)["error"]
            assert error["type"] == "https://gflow-cli.dev/errors/video-assembly"
            assert error["charged"] == "no"
