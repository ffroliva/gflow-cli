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
