from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from gflow_cli.api.credit_cap import resolve_max_credits
from gflow_cli.cli_gen import video_gen
from gflow_cli.cli_video import video


@pytest.mark.parametrize("name", ["t2v", "i2v", "r2v", "chain", "extend"])
def test_billed_video_commands_expose_max_credits(name: str) -> None:
    command = video.commands[name]
    assert any("--max-credits" in option.opts for option in command.params)
    assert any("--no-max-credits" in option.opts for option in command.params)


def test_video_generate_bridge_exposes_max_credits() -> None:
    assert any("--max-credits" in option.opts for option in video_gen.params)


def test_max_credits_cli_value_overrides_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_MAX_CREDITS", "6")
    assert resolve_max_credits(10, 6) == 10
    assert resolve_max_credits(None, 6) == 6
    assert resolve_max_credits(None, None) is None


def test_negative_max_credits_is_click_usage_error() -> None:
    result = CliRunner().invoke(video, ["t2v", "test prompt", "--max-credits", "-1"])
    assert result.exit_code == 2


def test_chain_dry_run_shows_max_credits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from gflow_cli import cli_video

    monkeypatch.setattr(cli_video, "_resolve_profile", lambda _profile: "test")
    monkeypatch.setattr(cli_video, "_make_provider_dir", lambda _profile: tmp_path)
    manifest = tmp_path / "chain.jsonl"
    manifest.write_text('{"prompt":"one"}\n', encoding="utf-8")
    result = CliRunner().invoke(video, ["chain", str(manifest), "--dry-run", "--max-credits", "12"])
    assert result.exit_code == 0, result.output
    assert "max credits : 12" in result.output


def test_extend_dry_run_shows_max_credits() -> None:
    result = CliRunner().invoke(
        video,
        [
            "extend",
            "b9458021-fc2d-4d95-ab53-cf844c6f1079",
            "continue",
            "--project",
            "7d3d6bd9-a39f-4c2d-b772-146e73e539cf",
            "--dry-run",
            "--max-credits",
            "12",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "max credits : 12" in result.output


def test_chain_and_extend_dry_run_json_match_develop_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli import cli_video

    monkeypatch.setattr(cli_video, "_resolve_profile", lambda _profile: "test")
    monkeypatch.setattr(cli_video, "_make_provider_dir", lambda _profile: tmp_path)
    manifest = tmp_path / "chain.jsonl"
    manifest.write_text('{"prompt":"one"}\n', encoding="utf-8")
    chain_result = CliRunner().invoke(video, ["chain", str(manifest), "--dry-run", "--json"])
    assert chain_result.exit_code == 0, chain_result.output
    assert "Chain plan" in chain_result.output
    assert "--dry-run: no video operations submitted, no clips generated." in chain_result.output
    assert "max credits" not in chain_result.output

    extend_result = CliRunner().invoke(
        video,
        [
            "extend",
            "b9458021-fc2d-4d95-ab53-cf844c6f1079",
            "continue",
            "--project",
            "7d3d6bd9-a39f-4c2d-b772-146e73e539cf",
            "--dry-run",
            "--json",
        ],
    )
    assert extend_result.exit_code == 0, extend_result.output
    assert extend_result.output == ""


def test_no_max_credits_ignores_environment_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli import cli_video

    captured: dict[str, object] = {}

    async def capture(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setenv("GFLOW_CLI_MAX_CREDITS", "6")
    monkeypatch.setattr(cli_video, "_run_t2v", capture)
    monkeypatch.setattr(cli_video, "_resolve_profile", lambda _profile: "test")
    monkeypatch.setattr(cli_video, "_make_provider_dir", lambda _profile: Path("."))
    result = CliRunner().invoke(
        video, ["t2v", "cat", "--no-max-credits"], env={"GFLOW_CLI_MAX_CREDITS": "6"}
    )
    assert result.exit_code == 0, result.output
    assert captured["max_credits"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["t2v", "i2v", "r2v"])
async def test_video_builders_use_environment_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str
) -> None:
    from types import SimpleNamespace

    from gflow_cli import cli_video

    captured: dict[str, object] = {}

    async def capture(request: object, **_: object) -> None:
        captured["request"] = request

    monkeypatch.setattr(cli_video, "get_settings", lambda: SimpleNamespace(max_credits=7))
    monkeypatch.setattr(cli_video, "_generate_and_report", capture)
    common = dict(profile_name="test", profile_dir=tmp_path, out_dir=None)
    if mode == "t2v":
        await cli_video._run_t2v(**common, prompt="cat", aspect="16:9", max_credits=None)
    elif mode == "i2v":
        await cli_video._run_i2v(
            **common,
            params=cli_video._I2VParams(image="frame.png", prompt="cat", aspect="16:9"),
        )
    else:
        await cli_video._run_r2v(**common, prompt="cat", refs=("ref.png",), aspect="16:9")
    assert captured["request"].max_credits == 7


def test_chain_resume_refuses_an_unresolved_billed_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from gflow_cli import cli_video
    from gflow_cli.data.chain_repo import ChainLinkRecorder
    from gflow_cli.errors import FrameExtractionError

    probe = MagicMock()
    probe.completed_links.return_value = [SimpleNamespace(local_path=str(tmp_path / "missing.mp4"))]
    monkeypatch.setattr(ChainLinkRecorder, "open", lambda *_args, **_kwargs: probe)
    with pytest.raises(FrameExtractionError):
        cli_video._resolve_chain_resume(
            "chain-id", [object()], settings=object(), profile_name="test", profile_dir=tmp_path
        )
    probe.close.assert_called_once()


def test_migrated_extend_refuses_cap_before_confirmation_or_client_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli import cli_video
    from gflow_cli.cli import main
    from gflow_cli.config import Settings

    client = MagicMock()
    monkeypatch.setattr(cli_video, "get_settings", lambda: Settings(flow_host="flow.google.com"))
    monkeypatch.setattr(cli_video, "FlowApiClient", lambda **_: client)
    result = CliRunner().invoke(
        main,
        [
            "video",
            "extend",
            "b9458021-fc2d-4d95-ab53-cf844c6f1079",
            "continue",
            "--project",
            "7d3d6bd9-a39f-4c2d-b772-146e73e539cf",
            "--max-credits",
            "4",
        ],
    )
    assert result.exit_code == 45, result.output
    assert "Submit" not in result.output
    client.assert_not_called()


@pytest.mark.asyncio
async def test_t2v_builder_carries_explicit_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli import cli_video

    captured = {}

    async def capture(request: object, **_: object) -> None:
        captured["request"] = request

    monkeypatch.setattr(cli_video, "_generate_and_report", capture)
    await cli_video._run_t2v(
        profile_name="test",
        profile_dir=tmp_path,
        prompt="test prompt",
        aspect="16:9",
        out_dir=None,
        max_credits=4,
    )
    assert captured["request"].max_credits == 4


@pytest.mark.asyncio
async def test_extend_cap_refuses_before_scene_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli import cli_video
    from gflow_cli.errors import CreditCapExceededError

    listing = json.loads(
        (
            Path(__file__).parent / "api" / "fixtures" / "project_initial_data_extend_models.json"
        ).read_text(encoding="utf-8")
    )
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.served_route.return_value = "labs"
    client.capability_listing = AsyncMock(return_value=listing)
    client.create_scene = AsyncMock()
    client.extend_video = AsyncMock()
    monkeypatch.setattr(cli_video, "FlowApiClient", lambda **_: client)
    with pytest.raises(CreditCapExceededError) as caught:
        await cli_video._extend_session(
            profile_name="test",
            profile_dir=tmp_path,
            media_id="b9458021-fc2d-4d95-ab53-cf844c6f1079",
            prompts=("continue",),
            segments=2,
            aspect="9:16",
            jitter_range=(0.0, 0.0),
            output_file=None,
            project_id="7d3d6bd9-a39f-4c2d-b772-146e73e539cf",
            scene_id=None,
            seed=None,
            as_json=True,
            recorder=None,
            max_credits=15,
        )
    assert caught.value.quoted == 20
    assert caught.value.max_credits == 15
    assert caught.value.charged == "no"
    client.create_scene.assert_not_awaited()
    client.extend_video.assert_not_awaited()
