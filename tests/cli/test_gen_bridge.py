"""`image gen` / `video gen` — bridge verbs that infer the mode from the inputs (#1193).

A bridge verb is the mode verb it resolves to, reached by another name: same options,
same output, same exit codes. These tests pin the routing table, the usage errors a
mode-only input raises on the wrong mode, and that the resolved mode verb's runner
receives exactly what it gets when the mode verb is called directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.cli_image import resolve_image_mode
from gflow_cli.cli_video import resolve_video_mode

_UUID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def png(tmp_path: Path) -> Path:
    p = tmp_path / "hero.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return p


# ---------------------------------------------------------------- routing table (pure)


@pytest.mark.parametrize(("refs", "mode"), [(False, "t2i"), (True, "i2i")])
def test_image_routing_table(refs: bool, mode: str) -> None:
    assert resolve_image_mode(refs=refs) == mode


@pytest.mark.parametrize(
    ("frames", "refs", "mode"),
    [(False, False, "t2v"), (True, False, "i2v"), (False, True, "r2v")],
)
def test_video_routing_table(frames: bool, refs: bool, mode: str) -> None:
    assert resolve_video_mode(frames=frames, refs=refs) == mode


def test_video_frames_and_refs_is_ambiguous_and_names_the_mode_verbs() -> None:
    with pytest.raises(click.UsageError) as exc:
        resolve_video_mode(frames=True, refs=True)
    assert "video i2v" in exc.value.message
    assert "video r2v" in exc.value.message


# ------------------------------------------------------- usage errors through the CLI


def _usage_error(args: list[str]) -> str:
    result = CliRunner().invoke(main, args, catch_exceptions=False)
    assert result.exit_code == 2, result.output
    return result.output


@pytest.mark.parametrize(
    ("extra", "flag"),
    [
        (["--prompts-file", "p.txt"], "--prompts-file"),
        (["--stdin"], "--stdin"),
        (["--fail-fast"], "--continue-on-error"),
        (["--jitter", "1-2"], "--jitter"),
    ],
)
def test_image_t2i_only_option_with_ref_is_a_usage_error(
    extra: list[str], flag: str, png: Path
) -> None:
    out = _usage_error(["image", "gen", "a cat", "--ref", str(png), *extra])
    assert flag in out
    assert "image i2i" in out


def test_image_several_prompts_with_ref_is_a_usage_error(png: Path) -> None:
    out = _usage_error(["image", "gen", "one", "two", "--ref", str(png)])
    assert "image i2i" in out


@pytest.mark.parametrize(
    ("args", "flag", "mode"),
    [
        (["--initial-frame", "{png}", "--ref", "{png}"], "", ""),
        (["--initial-frame", "{png}", "--reference-entity", "abc"], "--reference-entity", "i2v"),
        (
            ["--initial-frame", "{png}", "--reference-entity-name", "Bob"],
            "--reference-entity-name",
            "i2v",
        ),
        (["--ref", "{png}", "--ui-mode", "classic"], "--ui-mode", "r2v"),
    ],
)
def test_video_mode_only_inputs_on_the_wrong_mode(
    args: list[str], flag: str, mode: str, png: Path
) -> None:
    argv = ["video", "gen", "a knight", *(a.replace("{png}", str(png)) for a in args)]
    out = _usage_error(argv)
    if flag:
        assert flag in out
        assert f"video {mode}" in out
    else:  # ambiguity: names both mode verbs
        assert "video i2v" in out
        assert "video r2v" in out


# ------------------------------------------------------------------------------ help


def test_image_gen_help_names_its_mode_verbs() -> None:
    result = CliRunner().invoke(main, ["image", "gen", "--help"])
    assert result.exit_code == 0
    assert "image t2i" in result.output
    assert "image i2i" in result.output


def test_video_gen_help_names_its_mode_verbs() -> None:
    result = CliRunner().invoke(main, ["video", "gen", "--help"])
    assert result.exit_code == 0
    for verb in ("video t2v", "video i2v", "video r2v"):
        assert verb in result.output


# ---------------------------------------- same arguments reach the resolved mode runner


def _captured(module: str, runner_name: str, argv: list[str], tmp_path: Path) -> Any:
    with (
        patch(f"gflow_cli.{module}._resolve_profile", return_value="default"),
        patch(f"gflow_cli.{module}._make_provider_dir", return_value=tmp_path),
        patch(f"gflow_cli.{module}.{runner_name}", new_callable=AsyncMock) as run,
    ):
        result = CliRunner().invoke(main, argv, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    run.assert_awaited_once()
    return run.await_args


@pytest.mark.parametrize(
    ("mode", "runner_name", "args"),
    [
        ("t2i", "_run_t2i", ["a cat", "-n", "2", "--aspect", "1:1"]),
        ("i2i", "_run_i2i", ["a cat", "--ref", "{png}", "--ref", _UUID]),
        ("i2i", "_run_i2i", ["a cat", "--ref", "{png}", "--json", "--count", "3"]),
    ],
)
def test_image_gen_forwards_the_same_arguments(
    mode: str, runner_name: str, args: list[str], png: Path, tmp_path: Path
) -> None:
    args = [a.replace("{png}", str(png)) for a in args]
    direct = _captured("cli_image", runner_name, ["image", mode, *args], tmp_path)
    bridged = _captured("cli_image", runner_name, ["image", "gen", *args], tmp_path)
    assert bridged == direct


@pytest.mark.parametrize(
    ("mode", "runner_name", "args"),
    [
        ("t2v", "_run_t2v", ["a sunset", "--aspect", "16:9", "--reference-entity", "abc"]),
        ("i2v", "_run_i2v", ["slow pan", "--initial-frame", "{png}", "--end-frame", "{png}"]),
        ("r2v", "_run_r2v", ["knight", "--ref", "{png}", "--model", "veo-fast", "--json"]),
    ],
)
def test_video_gen_forwards_the_same_arguments(
    mode: str, runner_name: str, args: list[str], png: Path, tmp_path: Path
) -> None:
    args = [a.replace("{png}", str(png)) for a in args]
    with patch("gflow_cli.cli_video._media_picker_metadata", return_value=({}, {})):
        direct = _captured("cli_video", runner_name, ["video", mode, *args], tmp_path)
        bridged = _captured("cli_video", runner_name, ["video", "gen", *args], tmp_path)
    assert bridged == direct


# ------------------------------------------------------------- --json carries the mode


@pytest.mark.parametrize(
    ("extra", "mode"), [([], "t2i"), (["--ref", _UUID], "i2i")], ids=["t2i", "i2i"]
)
def test_image_gen_json_adds_the_resolved_mode(extra: list[str], mode: str, tmp_path: Path) -> None:
    from tests.cli.test_cli_image import _make_generated_image, _make_t2i_client

    client = _make_t2i_client(images=[_make_generated_image(media_name=f"m-{mode}")])
    with (
        patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
    ):
        argv = ["image", "gen", "a cat", *extra, "--json", "--out", str(tmp_path)]
        result = CliRunner().invoke(main, argv)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["mode"] == mode
    assert data["command"] == f"image {mode}"


def test_mode_verb_json_has_no_top_level_mode(tmp_path: Path) -> None:
    from tests.cli.test_cli_image import _make_generated_image, _make_t2i_client

    client = _make_t2i_client(images=[_make_generated_image(media_name="m1")])
    with (
        patch("gflow_cli.cli_image.FlowApiClient", return_value=client),
        patch("gflow_cli.cli_image._make_provider_dir", return_value=tmp_path / "prof"),
        patch("gflow_cli.cli_image._resolve_profile", return_value="default"),
    ):
        result = CliRunner().invoke(
            main, ["image", "t2i", "a cat", "--json", "--out", str(tmp_path)]
        )
    assert "mode" not in json.loads(result.stdout)


def test_video_gen_json_adds_the_resolved_mode(tmp_path: Path) -> None:
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.api.video import VideoResult, VideoStarted, VideoStatus
    from tests.cli.test_cli_video import FakeVideoRecorder

    stub = VideoResult(
        status=VideoStatus(media_id="m1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=tmp_path / "v.mp4",
        project_id="p1",
        flow_operation_id="o1",
    )

    async def fake_generate_video(**kwargs: Any) -> VideoResult:
        started = kwargs.get("on_started")
        if started is not None:
            res = started(VideoStarted(media_id="m1", project_id="p1", flow_operation_id="o1"))
            if res is not None:
                await res
        return stub

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch("gflow_cli.data.recorder.OperationRecorder.open", return_value=FakeVideoRecorder()),
        patch("gflow_cli.api.client.FlowApiClient.__aenter__", new_callable=AsyncMock) as enter,
        patch("gflow_cli.api.client.FlowApiClient.__aexit__", new_callable=AsyncMock),
    ):
        client = MagicMock(spec=FlowApiClient)
        client.generate_video = fake_generate_video
        enter.return_value = client
        result = CliRunner().invoke(main, ["video", "gen", "a sunset", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["mode"] == "t2v"
    assert data["command"] == "video t2v"


def test_gen_json_error_payload_carries_the_mode(tmp_path: Path) -> None:
    from gflow_cli.errors import ConfigurationError

    with (
        patch("gflow_cli.cli_video._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_video._make_provider_dir", return_value=tmp_path),
        patch(
            "gflow_cli.cli_video._run_t2v",
            new_callable=AsyncMock,
            side_effect=ConfigurationError("nope"),
        ),
    ):
        result = CliRunner().invoke(main, ["video", "gen", "a sunset", "--json"])
    assert result.exit_code == 11, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "fail"
    assert data["mode"] == "t2v"


def test_video_gen_keeps_where_project_name_came_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`default_project` ignores an env-sourced --project-name: keep the source visible."""
    from click.core import ParameterSource

    monkeypatch.setenv("GFLOW_CLI_PROJECT_NAME", "pinned title")
    seen: list[ParameterSource | None] = []

    def spy(project_id: str | None, project_name: str | None, profile: str) -> str | None:
        seen.append(click.get_current_context().get_parameter_source("project_name"))
        return project_id

    with patch("gflow_cli.cli_video.default_project", side_effect=spy):
        for verb in ("t2v", "gen"):
            _captured("cli_video", "_run_t2v", ["video", verb, "a sunset"], tmp_path)
    assert seen == [ParameterSource.ENVIRONMENT, ParameterSource.ENVIRONMENT]


# ------------------------------- the real entrypoint, at $0 (no profile, no browser)


@pytest.mark.parametrize(
    ("argv", "names"),
    [
        (["video", "gen", "x", "--initial-frame", "{png}", "--ref", "{png}"], ["i2v", "r2v"]),
        (["image", "gen", "x", "--ref", "{png}", "--stdin"], ["image i2i", "--stdin"]),
        (["video", "gen", "x", "--ref", "{png}", "--ui-mode", "classic"], ["video r2v"]),
    ],
)
def test_entrypoint_usage_errors_exit_2_before_any_browser(
    argv: list[str], names: list[str], png: Path, tmp_path: Path
) -> None:
    import os
    import subprocess
    import sys

    env = {**os.environ, "GFLOW_CLI_HOME": str(tmp_path / "home"), "PYTHONUTF8": "1"}
    args = [a.replace("{png}", str(png)) for a in argv]
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter
        [sys.executable, "-m", "gflow_cli", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 2, proc.stderr
    for name in names:
        assert name in proc.stderr
