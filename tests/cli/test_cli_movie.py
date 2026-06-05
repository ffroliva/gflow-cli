"""Tests for `gflow movie` — movie.toml orchestrator CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main as cli_main
from gflow_cli.movie_manifest import (
    AssemblyDef,
    CharacterDef,
    CharacterState,
    MovieManifest,
    MovieState,
    SceneDef,
    SceneState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _make_video_result(
    *, succeeded: bool = True, flow_op_id: str | None = "op-1"
) -> MagicMock:
    r = MagicMock()
    r.status.media_id = "media-1"
    r.status.succeeded = succeeded
    r.status.failure_reasons = []
    r.status.error_message = "video failed"
    r.flow_operation_id = flow_op_id
    r.local_path = Path("/out/video.mp4")
    return r


_VALID_MANIFEST = """\
title = "My Film"
project = "proj-abc"

[[scenes]]
title = "Intro"
type = "t2v"
prompt = "A wide panoramic shot"
aspect = "16:9"
duration = 8
"""


# ---------------------------------------------------------------------------
# gflow movie --help
# ---------------------------------------------------------------------------


class TestMovieHelp:
    def test_movie_group_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli_main, ["movie", "--help"])
        assert result.exit_code == 0
        assert "movie.toml" in result.output

    def test_run_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli_main, ["movie", "run", "--help"])
        assert result.exit_code == 0
        assert "--dry-run" in result.output
        assert "--fail-fast" in result.output

    def test_template_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli_main, ["movie", "template", "--help"])
        assert result.exit_code == 0
        assert "--force" in result.output


# ---------------------------------------------------------------------------
# gflow movie template
# ---------------------------------------------------------------------------


class TestMovieTemplate:
    def test_template_writes_file(self, tmp_path: Path) -> None:
        runner = CliRunner()
        out = tmp_path / "movie.toml"
        result = runner.invoke(cli_main, ["movie", "template", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "[[characters]]" in content
        assert "[[scenes]]" in content
        assert "[assemble]" in content

    def test_template_default_path(self, tmp_path: Path) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=tmp_path):
            result = runner.invoke(cli_main, ["movie", "template"])
            assert result.exit_code == 0
            assert Path("movie.toml").exists()

    def test_template_refuses_existing_without_force(self, tmp_path: Path) -> None:
        runner = CliRunner()
        out = tmp_path / "movie.toml"
        out.write_text("existing", encoding="utf-8")
        result = runner.invoke(cli_main, ["movie", "template", str(out)])
        assert result.exit_code == 1
        assert out.read_text(encoding="utf-8") == "existing"

    def test_template_force_overwrites(self, tmp_path: Path) -> None:
        runner = CliRunner()
        out = tmp_path / "movie.toml"
        out.write_text("old content", encoding="utf-8")
        result = runner.invoke(cli_main, ["movie", "template", "--force", str(out)])
        assert result.exit_code == 0
        assert "old content" not in out.read_text(encoding="utf-8")

    def test_generated_template_is_valid_manifest(self, tmp_path: Path) -> None:
        runner = CliRunner()
        out = tmp_path / "movie.toml"
        runner.invoke(cli_main, ["movie", "template", str(out)])
        m = MovieManifest.from_toml_path(out)
        assert m.title
        assert len(m.scenes) >= 1


# ---------------------------------------------------------------------------
# gflow movie run --dry-run
# ---------------------------------------------------------------------------


class TestMovieDryRun:
    def test_dry_run_prints_plan(self, tmp_path: Path) -> None:
        runner = CliRunner()
        manifest = _write_toml(tmp_path / "movie.toml", _VALID_MANIFEST)
        result = runner.invoke(cli_main, ["movie", "run", str(manifest), "--dry-run"])
        assert result.exit_code == 0
        assert "dry-run" in result.output.lower()
        assert "Intro" in result.output

    def test_dry_run_shows_characters(self, tmp_path: Path) -> None:
        runner = CliRunner()
        toml = (
            'title = "F"\nproject = "p"\n'
            '[[characters]]\nname = "Alice"\nface_prompt = "x"\n'
            '[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "y"\n'
        )
        manifest = _write_toml(tmp_path / "movie.toml", toml)
        result = runner.invoke(cli_main, ["movie", "run", str(manifest), "--dry-run"])
        assert result.exit_code == 0
        assert "Alice" in result.output
        assert "credit" in result.output.lower()

    def test_dry_run_shows_assembly(self, tmp_path: Path) -> None:
        runner = CliRunner()
        toml = (
            'title = "F"\nproject = "p"\n'
            '[assemble]\noutput = "./final.mp4"\n'
            '[[scenes]]\ntitle = "S"\ntype = "t2v"\nprompt = "y"\n'
        )
        manifest = _write_toml(tmp_path / "movie.toml", toml)
        result = runner.invoke(cli_main, ["movie", "run", str(manifest), "--dry-run"])
        assert result.exit_code == 0
        assert "Assembly" in result.output or "final.mp4" in result.output

    def test_dry_run_no_api_calls(self, tmp_path: Path) -> None:
        """Dry-run must not invoke FlowApiClient."""
        runner = CliRunner()
        manifest = _write_toml(tmp_path / "movie.toml", _VALID_MANIFEST)
        with patch("gflow_cli.cli_movie.FlowApiClient") as mock_client:
            result = runner.invoke(cli_main, ["movie", "run", str(manifest), "--dry-run"])
        assert result.exit_code == 0
        mock_client.assert_not_called()

    def test_dry_run_shows_skip_for_completed_scene(self, tmp_path: Path) -> None:
        runner = CliRunner()
        manifest = _write_toml(tmp_path / "movie.toml", _VALID_MANIFEST)
        state_path = MovieState.state_path_for(manifest)
        state = MovieState(title="My Film", project="proj-abc")
        state.scenes["Intro"] = SceneState(
            media_id="m1",
            flow_operation_id="op-1",
            local_path="/out/intro.mp4",
            status="completed",
        )
        state.save(state_path)

        result = runner.invoke(cli_main, ["movie", "run", str(manifest), "--dry-run"])
        assert result.exit_code == 0
        assert "skip" in result.output.lower()


# ---------------------------------------------------------------------------
# gflow movie run — manifest validation errors
# ---------------------------------------------------------------------------


class TestMovieRunValidation:
    def test_missing_manifest_exits_11(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli_main, ["movie", "run", str(tmp_path / "nonexistent.toml"), "--dry-run"]
        )
        assert result.exit_code == 11

    def test_invalid_manifest_exits_11(self, tmp_path: Path) -> None:
        runner = CliRunner()
        manifest = _write_toml(tmp_path / "bad.toml", 'title = "T"\n# no project, no scenes\n')
        result = runner.invoke(cli_main, ["movie", "run", str(manifest), "--dry-run"])
        assert result.exit_code == 11


# ---------------------------------------------------------------------------
# gflow movie run — live execution path (mocked at CLI level)
# ---------------------------------------------------------------------------


class TestMovieRunLive:
    def test_live_path_resolves_profile_and_calls_run_with_handlers(
        self, tmp_path: Path
    ) -> None:
        runner = CliRunner()
        manifest = _write_toml(tmp_path / "movie.toml", _VALID_MANIFEST)

        with (
            patch(
                "gflow_cli.cli_movie._resolve_profile", return_value="default"
            ) as mock_resolve,
            patch(
                "gflow_cli.cli_movie._make_provider_dir", return_value=tmp_path / "profile"
            ),
            patch("gflow_cli.cli_movie.run_with_handlers") as mock_run,
        ):
            result = runner.invoke(cli_main, ["movie", "run", str(manifest)])

        assert result.exit_code == 0
        mock_resolve.assert_called_once()
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# _collect_refs
# ---------------------------------------------------------------------------


class TestCollectRefs:
    def test_t2v_returns_empty_regardless_of_characters(self) -> None:
        from gflow_cli.cli_movie import _collect_refs

        scene = SceneDef(title="S", type="t2v", prompt="x", characters=("Alice",))
        state = MovieState(title="T", project="p")
        assert _collect_refs(scene, state) == []

    def test_i2v_returns_empty(self) -> None:
        from gflow_cli.cli_movie import _collect_refs

        scene = SceneDef(title="S", type="i2v", prompt="x", initial_frame="/f.png")
        state = MovieState(title="T", project="p")
        assert _collect_refs(scene, state) == []

    def test_r2v_returns_face_and_body_paths(self) -> None:
        from gflow_cli.cli_movie import _collect_refs

        scene = SceneDef(title="S", type="r2v", prompt="x", characters=("Alice",))
        state = MovieState(title="T", project="p")
        state.characters["Alice"] = CharacterState(
            entity_id="ent-1", image_paths=["/face.png", "/body.png"]
        )
        assert _collect_refs(scene, state) == ["/face.png", "/body.png"]

    def test_r2v_skips_none_image_slots(self) -> None:
        from gflow_cli.cli_movie import _collect_refs

        scene = SceneDef(title="S", type="r2v", prompt="x", characters=("Alice",))
        state = MovieState(title="T", project="p")
        state.characters["Alice"] = CharacterState(
            entity_id="ent-1", image_paths=["/face.png", None]
        )
        assert _collect_refs(scene, state) == ["/face.png"]

    def test_r2v_unknown_character_returns_empty(self) -> None:
        from gflow_cli.cli_movie import _collect_refs

        scene = SceneDef(title="S", type="r2v", prompt="x", characters=("Ghost",))
        state = MovieState(title="T", project="p")
        assert _collect_refs(scene, state) == []

    def test_r2v_multiple_characters(self) -> None:
        from gflow_cli.cli_movie import _collect_refs

        scene = SceneDef(title="S", type="r2v", prompt="x", characters=("Alice", "Bob"))
        state = MovieState(title="T", project="p")
        state.characters["Alice"] = CharacterState(
            entity_id="a", image_paths=["/a_face.png"]
        )
        state.characters["Bob"] = CharacterState(
            entity_id="b", image_paths=["/b_face.png", "/b_body.png"]
        )
        assert _collect_refs(scene, state) == ["/a_face.png", "/b_face.png", "/b_body.png"]


# ---------------------------------------------------------------------------
# _print_summary
# ---------------------------------------------------------------------------


class TestPrintSummary:
    def test_single_scene_completed(self) -> None:
        from gflow_cli.cli_movie import _print_summary

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(),
            scenes=(SceneDef(title="S", type="t2v", prompt="x"),),
        )
        _print_summary(
            manifest=manifest,
            completed_scene_ids=["op-1"],
            completed_local_paths=[Path("/out/a.mp4")],
        )

    def test_multi_scene_with_assemble(self) -> None:
        from gflow_cli.cli_movie import _print_summary

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(),
            scenes=(
                SceneDef(title="S1", type="t2v", prompt="x"),
                SceneDef(title="S2", type="t2v", prompt="y"),
            ),
            assemble=AssemblyDef(output="./final.mp4"),
        )
        _print_summary(
            manifest=manifest,
            completed_scene_ids=["op-1", "op-2"],
            completed_local_paths=[Path("/out/a.mp4"), Path("/out/b.mp4")],
        )

    def test_no_completed_scenes(self) -> None:
        from gflow_cli.cli_movie import _print_summary

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(),
            scenes=(SceneDef(title="S", type="t2v", prompt="x"),),
        )
        _print_summary(
            manifest=manifest,
            completed_scene_ids=[],
            completed_local_paths=[],
        )


# ---------------------------------------------------------------------------
# _run_movie — full async orchestrator
# ---------------------------------------------------------------------------


def _mock_client_cm() -> MagicMock:
    """Return a MagicMock that behaves as an async context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=cm)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestRunMovieOrchestrator:
    async def test_happy_path_no_characters(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(),
            scenes=(SceneDef(title="S", type="t2v", prompt="x"),),
        )
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "state.json"

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as mock_recorder_cls,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch(
                "gflow_cli.cli_movie._generate_scene",
                new=AsyncMock(return_value=_make_video_result()),
            ),
        ):
            mock_recorder_cls.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
                continue_on_error=True,
            )

        assert state.scenes["S"].status == "completed"
        assert state.scenes["S"].flow_operation_id == "op-1"
        assert state_path.exists()

    async def test_skips_completed_scene(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(),
            scenes=(SceneDef(title="S", type="t2v", prompt="x"),),
        )
        state = MovieState(title="T", project="p")
        state.scenes["S"] = SceneState(
            media_id="m",
            flow_operation_id="op-old",
            local_path="/out/v.mp4",
            status="completed",
        )
        state_path = tmp_path / "state.json"
        mock_generate = AsyncMock()

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as mock_recorder_cls,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie._generate_scene", new=mock_generate),
        ):
            mock_recorder_cls.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
                continue_on_error=True,
            )

        mock_generate.assert_not_called()

    async def test_continue_on_error_records_failure(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(),
            scenes=(SceneDef(title="S", type="t2v", prompt="x"),),
        )
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "state.json"

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as mock_recorder_cls,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch(
                "gflow_cli.cli_movie._generate_scene",
                new=AsyncMock(side_effect=RuntimeError("API down")),
            ),
        ):
            mock_recorder_cls.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
                continue_on_error=True,
            )

        assert state.scenes["S"].status == "failed"

    async def test_fail_fast_propagates_exception(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(),
            scenes=(SceneDef(title="S", type="t2v", prompt="x"),),
        )
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "state.json"

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as mock_recorder_cls,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch(
                "gflow_cli.cli_movie._generate_scene",
                new=AsyncMock(side_effect=RuntimeError("API down")),
            ),
            pytest.raises(RuntimeError, match="API down"),
        ):
            mock_recorder_cls.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
                continue_on_error=False,
            )

    async def test_creates_character_when_not_in_state(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(CharacterDef(name="Alice", face_prompt="young woman"),),
            scenes=(SceneDef(title="S", type="t2v", prompt="x"),),
        )
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "state.json"
        mock_create = AsyncMock()
        mock_generate = AsyncMock(return_value=_make_video_result())

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as mock_recorder_cls,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie._create_character", new=mock_create),
            patch("gflow_cli.cli_movie._generate_scene", new=mock_generate),
        ):
            mock_recorder_cls.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
                continue_on_error=True,
            )

        mock_create.assert_called_once()

    async def test_skips_existing_character(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie

        manifest = MovieManifest(
            title="T",
            project="p",
            characters=(CharacterDef(name="Alice", face_prompt="young woman"),),
            scenes=(SceneDef(title="S", type="t2v", prompt="x"),),
        )
        state = MovieState(title="T", project="p")
        state.characters["Alice"] = CharacterState(
            entity_id="ent-1", image_paths=["/face.png"]
        )
        state_path = tmp_path / "state.json"
        mock_create = AsyncMock()

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as mock_recorder_cls,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie._create_character", new=mock_create),
            patch(
                "gflow_cli.cli_movie._generate_scene",
                new=AsyncMock(return_value=_make_video_result()),
            ),
        ):
            mock_recorder_cls.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
                continue_on_error=True,
            )

        mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# _create_character
# ---------------------------------------------------------------------------


class TestCreateCharacter:
    async def test_face_only_saves_state(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _create_character

        char_def = CharacterDef(name="Alice", face_prompt="young woman")
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "state.json"

        mock_result = MagicMock()
        mock_result.entity_id = "ent-1"
        mock_result.image_paths = [Path("/face.png"), None]

        with patch(
            "gflow_cli.cli_movie.character_create", new=AsyncMock(return_value=mock_result)
        ):
            await _create_character(
                client=MagicMock(),
                recorder=MagicMock(),
                char_def=char_def,
                project_id="proj-abc",
                profile_name="default",
                profile_dir=tmp_path / "profile",
                state=state,
                state_path=state_path,
            )

        assert "Alice" in state.characters
        assert state.characters["Alice"].entity_id == "ent-1"
        assert state.characters["Alice"].image_paths == ["/face.png", None]
        assert state_path.exists()

    async def test_with_body_prompt_saves_both_paths(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _create_character

        char_def = CharacterDef(
            name="Bob", face_prompt="grey beard", body_prompt="casual jacket"
        )
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "state.json"

        mock_result = MagicMock()
        mock_result.entity_id = "ent-2"
        mock_result.image_paths = [Path("/bob_face.png"), Path("/bob_body.png")]

        with patch(
            "gflow_cli.cli_movie.character_create", new=AsyncMock(return_value=mock_result)
        ):
            await _create_character(
                client=MagicMock(),
                recorder=MagicMock(),
                char_def=char_def,
                project_id="proj-abc",
                profile_name="default",
                profile_dir=tmp_path / "profile",
                state=state,
                state_path=state_path,
            )

        assert state.characters["Bob"].image_paths == ["/bob_face.png", "/bob_body.png"]


# ---------------------------------------------------------------------------
# _generate_scene
# ---------------------------------------------------------------------------


class TestGenerateScene:
    async def test_t2v_calls_generate_video(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene

        scene = SceneDef(title="S", type="t2v", prompt="a beautiful sunset", duration=8)
        mock_client = AsyncMock()
        mock_result = _make_video_result()
        mock_client.generate_video.return_value = mock_result

        with patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None):
            result = await _generate_scene(
                client=mock_client,
                recorder=MagicMock(),
                scene_def=scene,
                refs=[],
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        assert result is mock_result
        mock_client.generate_video.assert_called_once()

    async def test_r2v_passes_reference_images(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene

        face = tmp_path / "face.png"
        body = tmp_path / "body.png"
        face.touch()
        body.touch()

        scene = SceneDef(title="S", type="r2v", prompt="x", characters=("Alice",))
        mock_client = AsyncMock()
        mock_client.generate_video.return_value = _make_video_result()

        with patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None):
            await _generate_scene(
                client=mock_client,
                recorder=MagicMock(),
                scene_def=scene,
                refs=[str(face), str(body)],
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        call_kwargs = mock_client.generate_video.call_args.kwargs
        assert hasattr(call_kwargs["req"], "reference_images")

    async def test_i2v_passes_start_image(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene

        frame = tmp_path / "frame.png"
        frame.touch()

        scene = SceneDef(title="S", type="i2v", prompt="x", initial_frame=str(frame))
        mock_client = AsyncMock()
        mock_client.generate_video.return_value = _make_video_result()

        with patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None):
            await _generate_scene(
                client=mock_client,
                recorder=MagicMock(),
                scene_def=scene,
                refs=[],
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        call_kwargs = mock_client.generate_video.call_args.kwargs
        assert hasattr(call_kwargs["req"], "start_image")

    async def test_i2v_with_end_frame(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene

        frame = tmp_path / "frame.png"
        end_frame = tmp_path / "end.png"
        frame.touch()
        end_frame.touch()

        scene = SceneDef(
            title="S",
            type="i2v",
            prompt="x",
            initial_frame=str(frame),
            end_frame=str(end_frame),
        )
        mock_client = AsyncMock()
        mock_client.generate_video.return_value = _make_video_result()

        with patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None):
            await _generate_scene(
                client=mock_client,
                recorder=MagicMock(),
                scene_def=scene,
                refs=[],
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        call_kwargs = mock_client.generate_video.call_args.kwargs
        assert hasattr(call_kwargs["req"], "end_image")

    async def test_failed_video_raises_runtime_error(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene

        scene = SceneDef(title="S", type="t2v", prompt="x")
        mock_client = AsyncMock()
        failed = _make_video_result(succeeded=False)
        failed.status.failure_reasons = ["quota exceeded"]
        mock_client.generate_video.return_value = failed

        with (
            patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None),
            pytest.raises(RuntimeError, match="quota exceeded"),
        ):
            await _generate_scene(
                client=mock_client,
                recorder=MagicMock(),
                scene_def=scene,
                refs=[],
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

    async def test_on_started_datastore_error_is_silent(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene
        from gflow_cli.errors import DataStoreError

        scene = SceneDef(title="S", type="t2v", prompt="x")
        mock_result = _make_video_result()

        async def fake_generate(**kwargs: object) -> MagicMock:
            on_started = kwargs.get("on_started")
            if callable(on_started):
                on_started(MagicMock())
            return mock_result

        mock_recorder = MagicMock()
        mock_recorder.record_started_video.side_effect = DataStoreError("db fail")
        mock_client = AsyncMock()
        mock_client.generate_video = AsyncMock(side_effect=fake_generate)

        with patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None):
            result = await _generate_scene(
                client=mock_client,
                recorder=mock_recorder,
                scene_def=scene,
                refs=[],
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        assert result is mock_result

    async def test_completed_datastore_error_is_silent(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene
        from gflow_cli.errors import DataStoreError

        scene = SceneDef(title="S", type="t2v", prompt="x")
        mock_result = _make_video_result()
        mock_recorder = MagicMock()
        mock_recorder.record_completed_video.side_effect = DataStoreError("db fail")
        mock_client = AsyncMock()
        mock_client.generate_video.return_value = mock_result

        with patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None):
            result = await _generate_scene(
                client=mock_client,
                recorder=mock_recorder,
                scene_def=scene,
                refs=[],
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        assert result is mock_result
