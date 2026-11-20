from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from gflow_cli.cli_movie import movie
from gflow_cli.composition import Scene


def test_movie_run_exposes_max_credits() -> None:
    command = movie.commands["run"]
    assert any("--max-credits" in option.opts for option in command.params)


def test_movie_dry_run_displays_max_credits(tmp_path: Path) -> None:
    manifest = tmp_path / "movie.toml"
    manifest.write_text(
        'title = "T"\nproject = "p"\n[[scenes]]\nid = "one"\naction = "A"\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(movie, ["run", str(manifest), "--dry-run", "--max-credits", "12"])
    assert result.exit_code == 0, result.output
    assert "max credits: 12" in result.output


@pytest.mark.asyncio
async def test_movie_scene_builder_passes_remaining_cap(tmp_path: Path) -> None:
    from gflow_cli import cli_movie

    captured = {}
    client = MagicMock()
    result = MagicMock()
    result.status.succeeded = True
    result.status.media_id = "media-1"
    result.flow_operation_id = None
    result.local_path = None

    async def generate(**kwargs):
        captured["request"] = kwargs["req"]
        return result

    client.generate_video = AsyncMock(side_effect=generate)
    await cli_movie._generate_scene(
        client=client,
        recorder=MagicMock(),
        scene=Scene(id="one", action="A"),
        prompt="A",
        profile_name="test",
        profile_dir=tmp_path,
        out_dir=tmp_path,
        project_id="project",
        max_credits=2,
    )
    assert captured["request"].max_credits == 2


@pytest.mark.asyncio
async def test_movie_cap_refusal_stops_even_with_continue_on_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli import cli_movie
    from gflow_cli.api.credit_cap import CreditBudget
    from gflow_cli.errors import CreditCapExceededError
    from gflow_cli.movie_manifest import MovieManifest, MovieState

    async def no_instructions(**_: object) -> None:
        return None

    monkeypatch.setattr(cli_movie, "_sync_scene_instructions", no_instructions)
    client = MagicMock()
    client.generate_video = AsyncMock(side_effect=CreditCapExceededError(quoted=4, max_credits=2))
    manifest = MovieManifest.from_toml_path(_manifest_file(tmp_path))
    state = MovieState(title="T", project="project")
    with pytest.raises(CreditCapExceededError):
        await cli_movie._run_one_scene(
            client=client,
            recorder=MagicMock(),
            scene=manifest.scenes[0],
            manifest=manifest,
            state=state,
            state_path=tmp_path / "movie-state.json",
            profile_name="test",
            profile_dir=tmp_path,
            out_dir=tmp_path,
            generated_any=False,
            continue_on_error=True,
            brief_cache=MagicMock(),
            budget=CreditBudget(2),
        )
    assert state.scenes[manifest.scenes[0].id].status == "failed"


@pytest.mark.asyncio
async def test_movie_deducts_quote_and_refuses_later_scene(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli import cli_movie
    from gflow_cli.api.credit_cap import CreditBudget
    from gflow_cli.api.video import VideoResult, VideoStatus
    from gflow_cli.errors import CreditCapExceededError
    from gflow_cli.movie_manifest import MovieManifest, MovieState

    async def no_instructions(**_: object) -> None:
        return None

    monkeypatch.setattr(cli_movie, "_sync_scene_instructions", no_instructions)
    manifest_path = _manifest_file(tmp_path)
    manifest_path.write_text(
        'title = "T"\nproject = "project"\n'
        '[[scenes]]\nid = "one"\naction = "A"\n'
        '[[scenes]]\nid = "two"\naction = "B"\n',
        encoding="utf-8",
    )
    manifest = MovieManifest.from_toml_path(manifest_path)
    state = MovieState(title="T", project="project")
    budget = CreditBudget(6)
    requests = []
    client = MagicMock()

    async def generate(**kwargs):
        request = kwargs["req"]
        requests.append(request)
        if len(requests) == 2:
            raise CreditCapExceededError(quoted=4, max_credits=request.max_credits or 0)
        return VideoResult(
            status=VideoStatus(media_id="media-1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            local_path=None,
            quoted_credits=4,
        )

    client.generate_video = AsyncMock(side_effect=generate)
    await cli_movie._run_one_scene(
        client=client,
        recorder=MagicMock(),
        scene=manifest.scenes[0],
        manifest=manifest,
        state=state,
        state_path=tmp_path / "movie-state.json",
        profile_name="test",
        profile_dir=tmp_path,
        out_dir=tmp_path,
        generated_any=False,
        continue_on_error=True,
        brief_cache=MagicMock(),
        budget=budget,
    )
    with pytest.raises(CreditCapExceededError) as caught:
        await cli_movie._run_one_scene(
            client=client,
            recorder=MagicMock(),
            scene=manifest.scenes[1],
            manifest=manifest,
            state=state,
            state_path=tmp_path / "movie-state.json",
            profile_name="test",
            profile_dir=tmp_path,
            out_dir=tmp_path,
            generated_any=True,
            continue_on_error=True,
            brief_cache=MagicMock(),
            budget=budget,
        )
    assert [request.max_credits for request in requests] == [6, 2]
    assert state.scenes["one"].status == "completed"
    assert state.scenes["two"].status == "failed"
    assert caught.value.charged == "yes"
    assert caught.value.next_action == "reconcile"
    assert caught.value.completed_media_ids == ["media-1"]


def _manifest_file(tmp_path: Path) -> Path:
    path = tmp_path / "movie.toml"
    path.write_text(
        'title = "T"\nproject = "project"\n[[scenes]]\nid = "one"\naction = "A"\n',
        encoding="utf-8",
    )
    return path
