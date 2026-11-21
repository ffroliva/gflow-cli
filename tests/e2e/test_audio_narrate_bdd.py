"""Installed CLI and MCP network e2e coverage for narration and assembly."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import av
import numpy as np
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pytest_bdd import given, scenarios, then, when

from tests.e2e.test_telemetry_local_e2e import InstalledGFlow
from tests.e2e.test_telemetry_local_e2e import clean_home as _clean_home_fixture  # noqa: F401
from tests.e2e.test_telemetry_local_e2e import installed_gflow as _installed_gflow_fixture  # noqa: F401

scenarios("../features/audio_narrate_e2e.feature")


@pytest.fixture
def installed_tts_gflow(request: pytest.FixtureRequest) -> InstalledGFlow:
    installed = request.getfixturevalue("_installed_gflow_fixture")
    assert isinstance(installed, InstalledGFlow)
    result = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(installed.python),
            "edge-tts>=7.3.1",
            "av>=12",
            "numpy>=1.24",
            "pillow>=13",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, f"installing TTS/media extras failed:\n{result.stderr}"
    return installed


@pytest.fixture
def narration_case(request: pytest.FixtureRequest, tmp_path: Path) -> dict[str, Any]:
    installed = request.getfixturevalue("installed_tts_gflow")
    clean = request.getfixturevalue("_clean_home_fixture")
    assert isinstance(installed, InstalledGFlow)
    assert isinstance(clean, tuple)
    _, env = clean
    env["GFLOW_CLI_OUTPUT_DIR"] = str(tmp_path)
    return {"installed": installed, "env": env, "root": tmp_path}


def _silent_clip(path: Path) -> None:
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 160, 90, "yuv420p"
        for index in range(30):
            frame = av.VideoFrame.from_ndarray(
                np.zeros((90, 160, 3), dtype=np.uint8), format="rgb24"
            )
            frame.pts = index
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


def _assert_voice_audio(path: Path) -> None:
    with av.open(str(path)) as container:
        assert container.streams.audio, "assembled video has no audio stream"
        frames = list(container.decode(audio=0))
    assert frames, "assembled voice track decoded no audio"
    samples = np.concatenate([frame.to_ndarray().astype(np.float32).ravel() for frame in frames])
    assert float(np.sqrt(np.mean(np.square(samples)))) > 0.001, "voice track is silent"


@given("a synthetic local silent clip")
def synthetic_clip(narration_case: dict[str, Any]) -> None:
    path = narration_case["root"] / "silent.mp4"
    _silent_clip(path)
    narration_case["clip"] = path


@when("the installed CLI creates narration audio")
def cli_narration(narration_case: dict[str, Any]) -> None:
    installed: InstalledGFlow = narration_case["installed"]
    root: Path = narration_case["root"]
    voice = root / "cli-voice.mp3"
    result = subprocess.run(
        [
            str(installed.gflow),
            "audio",
            "narrate",
            "A clear voice speaks.",
            "--out-file",
            str(voice),
            "--provider",
            "edge",
            "--json",
        ],
        cwd=root,
        env=narration_case["env"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["provider"] == "edge"
    narration_case["voice"] = voice


@when("the installed MCP tool creates narration audio in no-spend mode")
def mcp_narration(narration_case: dict[str, Any]) -> None:
    installed: InstalledGFlow = narration_case["installed"]
    env = {**narration_case["env"], "GFLOW_CLI_OUTPUT_DIR": str(narration_case["root"])}
    params = StdioServerParameters(
        command=str(installed.gflow), args=["mcp", "run", "--no-spend"], env=env
    )

    async def call() -> Path:
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert "gflow_narrate" in names
                result = await session.call_tool(
                    "gflow_narrate",
                    {
                        "text": "A clear voice speaks.",
                        "out_file": "mcp-voice.mp3",
                        "provider": "edge",
                    },
                )
                assert not result.is_error, result
                payload = json.loads(result.content[0].text)
                assert payload["provider"] == "edge"
                return Path(payload["out_file"])

    narration_case["voice"] = asyncio.run(call())


@when("the installed CLI assembles the clip with the narration voiceover")
@when("the installed CLI assembles the clip with the MCP narration voiceover")
def assemble(narration_case: dict[str, Any]) -> None:
    installed: InstalledGFlow = narration_case["installed"]
    root: Path = narration_case["root"]
    output = root / "assembled.mp4"
    result = subprocess.run(
        [
            str(installed.gflow),
            "video",
            "assemble",
            str(narration_case["clip"]),
            "--voiceover",
            str(narration_case["voice"]),
            "--out-file",
            str(output),
            "--json",
        ],
        cwd=root,
        env=narration_case["env"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    narration_case["assembled"] = output


@then("the assembled output contains non-silent voice audio")
def audible_assembly(narration_case: dict[str, Any]) -> None:
    _assert_voice_audio(narration_case["assembled"])


@pytest.mark.e2e
@pytest.mark.e2e_tts
def test_gemini_narration_e2e_skips_without_dedicated_key(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    key = os.environ.get("GFLOW_CLI_GEMINI_TTS_API_KEY", "").strip()
    if not key:
        pytest.skip("GFLOW_CLI_GEMINI_TTS_API_KEY is unset; Gemini TTS e2e was not requested")
    installed = request.getfixturevalue("_installed_gflow_fixture")
    clean = request.getfixturevalue("_clean_home_fixture")
    assert isinstance(installed, InstalledGFlow)
    assert isinstance(clean, tuple)
    _, env = clean
    env["GFLOW_CLI_GEMINI_TTS_API_KEY"] = key
    voice = tmp_path / "gemini-voice.wav"
    result = subprocess.run(
        [
            str(installed.gflow),
            "audio",
            "narrate",
            "A clear voice speaks.",
            "--out-file",
            str(voice),
            "--provider",
            "gemini",
            "--json",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_voice_audio(voice)
