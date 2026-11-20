"""Installed-wheel CLI and MCP coverage for local video assembly."""

from __future__ import annotations

import json
import os
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


@pytest.mark.asyncio
async def test_catalog_media_ids_assemble_in_cli_and_mcp(
    installed_chain_gflow: InstalledGFlow,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Assemble two verified catalog videos through both installed entry points."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    profile = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not profile:
        pytest.skip("set GFLOW_CLI_E2E_PROFILE to a profile with two catalogued local videos")

    from gflow_cli.config import Settings
    from gflow_cli.data.models import AssetKind
    from gflow_cli.data.queries import list_videos
    from gflow_cli.data.repository import DataRepository, verified_local_path
    from gflow_cli.data.store import DataStore

    with monkeypatch.context() as clean_settings_env:
        clean_settings_env.delenv("GFLOW_CLI_HOME", raising=False)
        clean_settings_env.delenv("GFLOW_CLI_DB_PATH", raising=False)
        real_settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    candidates = list_videos(
        db_path=real_settings.resolved_db_path(), profile=profile, limit=1000, offset=0
    )
    usable: list[tuple[str, Path, float]] = []
    with DataStore.open(real_settings.resolved_db_path()) as store:
        repo = DataRepository(store)
        for candidate in candidates:
            asset = repo.get_asset_by_flow_media_id(profile, candidate.media_id)
            if asset is None or asset.kind is not AssetKind.VIDEO:
                continue
            local_path = next(
                (path for local in asset.local_files if (path := verified_local_path(local))),
                None,
            )
            if local_path is None:
                continue
            with av.open(str(local_path)) as container:
                duration = float(container.duration or 0) / av.time_base
            usable.append((candidate.media_id, local_path, duration))
            if len(usable) == 2:
                break
    if len(usable) < 2:
        pytest.skip(
            f"profile {profile!r} has {len(usable)} catalogued videos with verified local files; "
            "two are required"
        )

    clean_home_data = request.getfixturevalue("_clean_home_fixture")
    assert isinstance(clean_home_data, tuple)
    _, base_env = clean_home_data
    env = {
        **base_env,
        "GFLOW_CLI_HOME": str(real_settings.home),
        "GFLOW_CLI_DB_PATH": str(real_settings.resolved_db_path()),
        "GFLOW_CLI_OUTPUT_DIR": str(tmp_path),
    }
    media_ids = [item[0] for item in usable]
    expected_duration = sum(item[2] for item in usable)
    cli_output = tmp_path / "catalog-cli.mp4"
    cli = subprocess.run(
        [
            str(installed_chain_gflow.gflow),
            "video",
            "assemble",
            *media_ids,
            "--profile",
            profile,
            "--out-file",
            str(cli_output),
            "--json",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cli.returncode == 0, cli.stderr
    cli_result = json.loads(cli.stdout)
    assert cli_result["duration_seconds"] == pytest.approx(expected_duration, abs=0.2)
    with av.open(str(cli_output)) as container:
        assert float(container.duration or 0) / av.time_base == pytest.approx(
            expected_duration, abs=0.2
        )

    params = StdioServerParameters(
        command=str(installed_chain_gflow.gflow),
        args=["mcp", "run", "--no-spend"],
        env=env,
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.call_tool(
                "gflow_video_assemble",
                {
                    "clips": media_ids,
                    "profile": profile,
                    "out_file": "catalog-mcp.mp4",
                },
            )
            assert not result.is_error, result.content
            mcp_result = json.loads(result.content[0].text)
            assert mcp_result["duration_seconds"] == pytest.approx(expected_duration, abs=0.2)
            with av.open(str(tmp_path / "catalog-mcp.mp4")) as container:
                assert float(container.duration or 0) / av.time_base == pytest.approx(
                    expected_duration, abs=0.2
                )
