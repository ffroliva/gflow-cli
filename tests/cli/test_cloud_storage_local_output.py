"""#1008: a cloud GFLOW_CLI_STORAGE_URI plus a local-file need is refused before billing.

`-o/--output` and the MCP `output` move the downloaded clip, and `video chain` hands it
to ffmpeg; neither works on a bucket object, and both used to fail after Flow billed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gflow_cli.config import reset_settings
from gflow_cli.errors import ConfigurationError
from gflow_cli.storage import refuse_cloud_storage_for_local_file


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_STORAGE_URI", "memory://bucket/gflow/")
    reset_settings()


def _no_client(*_a: Any, **_k: Any) -> None:
    raise AssertionError("refused before any browser or submit")


def test_helper_refuses_only_with_cloud_storage() -> None:
    refuse_cloud_storage_for_local_file(None, "-o/--output")
    with pytest.raises(ConfigurationError, match="-o/--output"):
        refuse_cloud_storage_for_local_file("s3://b/", "-o/--output")


@pytest.mark.usefixtures("bucket")
def test_t2v_output_with_cloud_storage_exits_11_before_submit(tmp_path: Path) -> None:
    """#1008, #1158: --out-file is refused under cloud storage before submit."""
    from gflow_cli.cli import main

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video.FlowApiClient", _no_client),
    ):
        result = CliRunner().invoke(
            main, ["video", "t2v", "a sunset", "--out-file", str(tmp_path / "clip.mp4")]
        )

    assert result.exit_code == 11, result.output
    assert "GFLOW_CLI_STORAGE_URI" in result.output
    assert "--out-file needs the clip" in result.output


@pytest.mark.usefixtures("bucket")
def test_t2v_without_output_still_runs_with_cloud_storage(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video.FlowApiClient", _no_client),
    ):
        result = CliRunner().invoke(main, ["video", "t2v", "a sunset"])

    assert "GFLOW_CLI_STORAGE_URI" not in result.output  # reached the client instead


@pytest.mark.usefixtures("bucket")
def test_chain_with_cloud_storage_exits_11_before_submit(tmp_path: Path) -> None:
    from gflow_cli.cli import main

    manifest = tmp_path / "chain.jsonl"
    manifest.write_text('{"prompt": "a"}\n{"prompt": "b"}\n', encoding="utf-8")
    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.cli_video.FlowApiClient", _no_client),
    ):
        result = CliRunner().invoke(main, ["video", "chain", str(manifest), "--yes"])

    assert result.exit_code == 11, result.output
    assert "GFLOW_CLI_STORAGE_URI" in result.output


@pytest.mark.asyncio
@pytest.mark.usefixtures("bucket")
async def test_mcp_video_output_with_cloud_storage_is_refused_before_queueing() -> None:
    from gflow_cli.mcp import tools as tools_mod

    async def _never(**_k: Any) -> dict[str, Any]:
        raise AssertionError("refused before the worker")

    with (
        patch("gflow_cli.mcp.tools._rate_limiter", tools_mod._TokenBucket(8, 0.0)),
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
        patch.object(tools_mod, "_run_generation_task", _never),
    ):
        result = await tools_mod.gflow_generate_video(prompt="a dog", out_file="C:/out/x.mp4")

    assert result["status"] == "error"
    assert "GFLOW_CLI_STORAGE_URI" in result["error"]["detail"], result
    assert result["error"]["type"].endswith("/configuration"), result


@pytest.mark.asyncio
@pytest.mark.usefixtures("bucket")
async def test_mcp_video_refusal_names_the_parameter_passed() -> None:
    """#1158: out_file is refused under cloud storage, naming what was passed."""
    from gflow_cli.mcp import tools as tools_mod

    async def _never(**_k: Any) -> dict[str, Any]:
        raise AssertionError("refused before the worker")

    with (
        patch("gflow_cli.mcp.tools._rate_limiter", tools_mod._TokenBucket(8, 0.0)),
        patch("gflow_cli.mcp.tools._resolve_and_validate_profile", return_value="default"),
        patch.object(tools_mod, "_run_generation_task", _never),
    ):
        result = await tools_mod.gflow_generate_video(prompt="a dog", out_file="x.mp4")

    assert result["status"] == "error"
    assert result["error"]["detail"].startswith("out_file needs the clip"), result


@pytest.mark.asyncio
@pytest.mark.usefixtures("bucket")
async def test_movie_stitch_with_cloud_storage_is_refused_before_the_browser(
    tmp_path: Path,
) -> None:
    from unittest.mock import MagicMock

    from gflow_cli.cli_movie import _run_movie

    with (
        patch("gflow_cli.cli_movie.FlowApiClient", _no_client),
        pytest.raises(ConfigurationError, match="movie run --stitch"),
    ):
        await _run_movie(
            manifest=MagicMock(),
            state=MagicMock(),
            state_path=tmp_path / "s.json",
            profile_name="default",
            profile_dir=tmp_path,
            out_dir=tmp_path,
            continue_on_error=False,
            stitch=True,
        )
