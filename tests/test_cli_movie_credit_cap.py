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


def test_movie_no_max_credits_clears_environment_default(tmp_path: Path) -> None:
    manifest = tmp_path / "movie.toml"
    manifest.write_text(
        'title = "T"\nproject = "p"\n[[scenes]]\nid = "one"\naction = "A"\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        movie,
        ["run", str(manifest), "--dry-run", "--no-max-credits"],
        env={"GFLOW_CLI_MAX_CREDITS": "6"},
    )
    assert result.exit_code == 0, result.output
    assert "max credits:" not in result.output


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
    context = cli_movie.MovieRunContext(
        manifest=manifest,
        state=state,
        state_path=tmp_path / "movie-state.json",
        profile_name="test",
        profile_dir=tmp_path,
        out_dir=tmp_path,
        brief_cache=MagicMock(),
        budget=CreditBudget(2),
    )
    with pytest.raises(CreditCapExceededError):
        await cli_movie._run_one_scene(
            client=client,
            recorder=MagicMock(),
            scene=manifest.scenes[0],
            context=context,
            generated_any=False,
            continue_on_error=True,
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
    run_media_ids: list[str] = []
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
    context = cli_movie.MovieRunContext(
        manifest=manifest,
        state=state,
        state_path=tmp_path / "movie-state.json",
        profile_name="test",
        profile_dir=tmp_path,
        out_dir=tmp_path,
        brief_cache=MagicMock(),
        budget=budget,
    )
    await cli_movie._run_one_scene(
        client=client,
        recorder=MagicMock(),
        scene=manifest.scenes[0],
        context=context,
        generated_any=False,
        run_media_ids=run_media_ids,
        continue_on_error=True,
    )
    with pytest.raises(CreditCapExceededError) as caught:
        await cli_movie._run_one_scene(
            client=client,
            recorder=MagicMock(),
            scene=manifest.scenes[1],
            context=context,
            generated_any=True,
            run_media_ids=run_media_ids,
            continue_on_error=True,
        )
    assert [request.max_credits for request in requests] == [6, 2]
    assert state.scenes["one"].status == "completed"
    assert state.scenes["two"].status == "failed"
    assert caught.value.charged == "yes"
    assert caught.value.next_action == "reconcile"
    assert caught.value.completed_media_ids == ["media-1"]


def test_resumed_movie_cap_refusal_does_not_claim_old_media_was_billed_this_run(
    tmp_path: Path,
) -> None:
    from gflow_cli.cli_movie import _preserve_movie_cap_partials
    from gflow_cli.errors import CreditCapExceededError
    from gflow_cli.movie_manifest import MovieManifest, MovieState, SceneState

    manifest = MovieManifest.from_toml_path(_manifest_file(tmp_path))
    state = MovieState(title="T", project="project")
    state.scenes["one"] = SceneState(
        media_id="old-media", flow_operation_id=None, local_path=None, status="completed"
    )
    error = CreditCapExceededError(quoted=8, max_credits=2)
    _preserve_movie_cap_partials(error, manifest, state, run_media_ids=[])
    assert error.charged == "no"
    assert error.completed_media_ids == []


@pytest.mark.asyncio
async def test_continue_on_error_deducts_quote_from_a_post_submit_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli import cli_movie
    from gflow_cli.api.credit_cap import CreditBudget
    from gflow_cli.errors import CreditCapExceededError, TransportTimeoutError
    from gflow_cli.movie_manifest import MovieManifest, MovieState

    async def no_instructions(**_: object) -> None:
        return None

    monkeypatch.setattr(cli_movie, "_sync_scene_instructions", no_instructions)
    path = _manifest_file(tmp_path)
    path.write_text(
        'title = "T"\nproject = "project"\n'
        '[[scenes]]\nid = "one"\naction = "A"\n'
        '[[scenes]]\nid = "two"\naction = "B"\n',
        encoding="utf-8",
    )
    manifest = MovieManifest.from_toml_path(path)
    state, budget, requests = MovieState(title="T", project="project"), CreditBudget(10), []
    client = MagicMock()

    async def generate(**kwargs: object) -> None:
        cap = kwargs["req"].max_credits
        requests.append(cap)
        if cap == 0:
            raise CreditCapExceededError(quoted=10, max_credits=0)
        error = TransportTimeoutError(detail="poll timeout after submit")
        error.quoted_credits = 10
        raise error

    client.generate_video = AsyncMock(side_effect=generate)
    context = cli_movie.MovieRunContext(
        manifest=manifest,
        state=state,
        state_path=tmp_path / "state.json",
        profile_name="test",
        profile_dir=tmp_path,
        out_dir=tmp_path,
        brief_cache=MagicMock(),
        budget=budget,
    )
    for index, scene in enumerate(manifest.scenes):
        call = cli_movie._run_one_scene(
            client=client,
            recorder=MagicMock(),
            context=context,
            scene=scene,
            generated_any=bool(index),
            run_media_ids=[],
            continue_on_error=True,
        )
        if index:
            with pytest.raises(CreditCapExceededError):
                await call
        else:
            await call
    assert requests == [10, 0]


@pytest.mark.asyncio
async def test_movie_persists_media_id_when_cap_gate_appears_after_submit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli import cli_movie
    from gflow_cli.api.credit_cap import CreditBudget
    from gflow_cli.errors import CreditCapExceededError
    from gflow_cli.movie_manifest import MovieManifest, MovieState

    async def no_instructions(**_: object) -> None:
        return None

    monkeypatch.setattr(cli_movie, "_sync_scene_instructions", no_instructions)
    manifest = MovieManifest.from_toml_path(_manifest_file(tmp_path))
    state, client = MovieState(title="T", project="project"), MagicMock()
    error = CreditCapExceededError(
        quoted=None, max_credits=10, completed_media_ids=["media-billed"]
    )
    error.charged, error.next_action = "maybe", "reconcile"
    client.generate_video = AsyncMock(side_effect=error)
    context = cli_movie.MovieRunContext(
        manifest=manifest,
        state=state,
        state_path=tmp_path / "state.json",
        profile_name="test",
        profile_dir=tmp_path,
        out_dir=tmp_path,
        brief_cache=MagicMock(),
        budget=CreditBudget(10),
    )
    with pytest.raises(CreditCapExceededError) as caught:
        await cli_movie._run_one_scene(
            client=client,
            recorder=MagicMock(),
            scene=manifest.scenes[0],
            context=context,
            generated_any=False,
            run_media_ids=[],
            continue_on_error=True,
        )
    assert state.scenes[manifest.scenes[0].id].media_id == "media-billed"
    assert state.scenes[manifest.scenes[0].id].status == "completed"
    assert caught.value.charged == "maybe"
    assert caught.value.next_action == "reconcile"


@pytest.mark.asyncio
async def test_movie_records_completed_scene_when_billed_submit_returns_no_quote(
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
    manifest = MovieManifest.from_toml_path(_manifest_file(tmp_path))
    state, client = MovieState(title="T", project="project"), MagicMock()
    client.generate_video = AsyncMock(
        return_value=VideoResult(
            status=VideoStatus(
                media_id="media-unquoted", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"
            ),
            local_path=None,
            quoted_credits=None,
        )
    )
    context2 = cli_movie.MovieRunContext(
        manifest=manifest,
        state=state,
        state_path=tmp_path / "state.json",
        profile_name="test",
        profile_dir=tmp_path,
        out_dir=tmp_path,
        brief_cache=MagicMock(),
        budget=CreditBudget(10),
    )
    with pytest.raises(CreditCapExceededError) as caught:
        await cli_movie._run_one_scene(
            client=client,
            recorder=MagicMock(),
            scene=manifest.scenes[0],
            context=context2,
            generated_any=False,
            run_media_ids=[],
            continue_on_error=True,
        )
    assert state.scenes[manifest.scenes[0].id].media_id == "media-unquoted"
    assert state.scenes[manifest.scenes[0].id].status == "completed"
    assert caught.value.charged == "maybe"
    assert caught.value.next_action == "reconcile"
    assert caught.value.completed_media_ids == ["media-unquoted"]


def _manifest_file(tmp_path: Path) -> Path:
    path = tmp_path / "movie.toml"
    path.write_text(
        'title = "T"\nproject = "project"\n[[scenes]]\nid = "one"\naction = "A"\n',
        encoding="utf-8",
    )
    return path
