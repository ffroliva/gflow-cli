"""Tests for `gflow movie` — movie.toml orchestrator CLI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main as cli_main
from gflow_cli.composition import Character, Scene, StyleSpec
from gflow_cli.movie_manifest import (
    CharacterState,
    MovieManifest,
    MovieState,
    SceneState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _make_video_result(*, succeeded: bool = True, flow_op_id: str | None = "op-1") -> MagicMock:
    r = MagicMock()
    r.status.media_id = "media-1"
    r.status.succeeded = succeeded
    r.status.failure_reasons = []
    r.status.error_message = "video failed"
    r.flow_operation_id = flow_op_id
    r.local_path = Path("/out/video.mp4")
    return r


def _manifest(
    *,
    title: str = "T",
    project: str = "p",
    style: StyleSpec | None = None,
    characters: dict[str, Character] | None = None,
    scenes: tuple[Scene, ...] = (),
) -> MovieManifest:
    return MovieManifest(
        title=title,
        project=project,
        style=style if style is not None else StyleSpec(),
        characters=characters if characters is not None else {},
        scenes=scenes,
    )


_VALID_MANIFEST = """\
title = "My Film"
project = "proj-abc"

[[scenes]]
id = "intro"
action = "A wide panoramic shot"
framing = "wide"
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
        assert "--stitch" in result.output

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
        assert "[style]" in content
        assert "[[characters]]" in content
        assert "[[scenes]]" in content

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
        assert "intro" in result.output

    def test_dry_run_shows_characters(self, tmp_path: Path) -> None:
        runner = CliRunner()
        toml = (
            'title = "F"\nproject = "p"\n'
            '[[characters]]\nname = "Alice"\nappearance = "x"\n'
            '[[scenes]]\nid = "s"\naction = "y"\n'
        )
        manifest = _write_toml(tmp_path / "movie.toml", toml)
        result = runner.invoke(cli_main, ["movie", "run", str(manifest), "--dry-run"])
        assert result.exit_code == 0
        assert "Alice" in result.output
        assert "credit" in result.output.lower()

    def test_dry_run_estimates_credits(self, tmp_path: Path) -> None:
        runner = CliRunner()
        toml = (
            'title = "F"\nproject = "p"\n'
            '[[scenes]]\nid = "s1"\naction = "y"\n'
            '[[scenes]]\nid = "s2"\naction = "z"\n'
        )
        manifest = _write_toml(tmp_path / "movie.toml", toml)
        result = runner.invoke(cli_main, ["movie", "run", str(manifest), "--dry-run"])
        assert result.exit_code == 0
        assert "Estimated credits: ~2" in result.output

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
        state.scenes["intro"] = SceneState(
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
    def test_live_path_resolves_profile_and_calls_run_with_handlers(self, tmp_path: Path) -> None:
        runner = CliRunner()
        manifest = _write_toml(tmp_path / "movie.toml", _VALID_MANIFEST)

        with (
            patch("gflow_cli.cli_movie._resolve_profile", return_value="default") as mock_resolve,
            patch("gflow_cli.cli_movie._make_provider_dir", return_value=tmp_path / "profile"),
            patch("gflow_cli.cli_movie.run_with_handlers") as mock_run,
        ):
            result = runner.invoke(cli_main, ["movie", "run", str(manifest)])

        assert result.exit_code == 0
        mock_resolve.assert_called_once()
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# _print_summary
# ---------------------------------------------------------------------------


class TestPrintSummary:
    def test_single_scene_completed(self) -> None:
        from gflow_cli.cli_movie import _print_summary

        manifest = _manifest(scenes=(Scene(id="s", action="x"),))
        _print_summary(
            manifest=manifest,
            completed_local_paths=[Path("/out/a.mp4")],
        )

    def test_multi_scene(self) -> None:
        from gflow_cli.cli_movie import _print_summary

        manifest = _manifest(
            scenes=(Scene(id="s1", action="x"), Scene(id="s2", action="y")),
        )
        _print_summary(
            manifest=manifest,
            completed_local_paths=[Path("/out/a.mp4"), Path("/out/b.mp4")],
        )

    def test_no_completed_scenes(self) -> None:
        from gflow_cli.cli_movie import _print_summary

        manifest = _manifest(scenes=(Scene(id="s", action="x"),))
        _print_summary(
            manifest=manifest,
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

        manifest = _manifest(scenes=(Scene(id="s", action="x"),))
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "m-state.json"

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

        assert state.scenes["s"].status == "completed"
        assert state.scenes["s"].flow_operation_id == "op-1"
        assert state_path.exists()

    async def test_run_movie_writes_handoff_and_composes_prompt(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie

        manifest = _manifest(
            style=StyleSpec(look="ink", negative="no text"),
            characters={"Stickman": Character(name="Stickman", appearance="round head")},
            scenes=(
                Scene(
                    id="s1",
                    action="walks",
                    framing="wide",
                    characters=("Stickman",),
                    duration=8,
                ),
            ),
        )
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "m-state.json"
        captured: dict[str, object] = {}

        async def fake_generate(**kwargs: object) -> MagicMock:
            captured["prompt"] = kwargs.get("prompt")
            return _make_video_result()

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as rec,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie._generate_scene", new=AsyncMock(side_effect=fake_generate)),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            rec.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "p",
                out_dir=tmp_path / "out",
                continue_on_error=True,
            )

        # Prompt was composed and passed to _generate_scene.
        assert captured["prompt"]
        assert "Round head" in str(captured["prompt"])

        handoff = tmp_path / "m-handoff.json"
        assert handoff.exists()
        data = json.loads(handoff.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert data["clips"][0]["id"] == "s1"

    async def test_skips_completed_scene(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie

        manifest = _manifest(scenes=(Scene(id="s", action="x"),))
        state = MovieState(title="T", project="p")
        state.scenes["s"] = SceneState(
            media_id="m",
            flow_operation_id="op-old",
            local_path="/out/v.mp4",
            status="completed",
        )
        state_path = tmp_path / "m-state.json"
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

        manifest = _manifest(scenes=(Scene(id="s", action="x"),))
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "m-state.json"

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

        assert state.scenes["s"].status == "failed"

    async def test_fail_fast_propagates_exception(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie

        manifest = _manifest(scenes=(Scene(id="s", action="x"),))
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "m-state.json"

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

    async def test_two_scene_run_does_not_crash_on_cooldown(self, tmp_path: Path) -> None:
        """Regression: the reCAPTCHA cooldown on scene 2+ must not NameError."""
        from gflow_cli.cli_movie import _run_movie

        manifest = _manifest(
            scenes=(Scene(id="s1", action="x"), Scene(id="s2", action="y")),
        )
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "m-state.json"

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as mock_recorder_cls,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch(
                "gflow_cli.cli_movie._generate_scene",
                new=AsyncMock(return_value=_make_video_result()),
            ),
            patch("asyncio.sleep", new=AsyncMock()),
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

        assert state.scenes["s1"].status == "completed"
        assert state.scenes["s2"].status == "completed"

    async def test_resume_generates_first_new_scene(self, tmp_path: Path) -> None:
        """Regression: on resume, the first NEW scene must generate (not crash
        on the cooldown triggered by the resumed completed scene)."""
        from gflow_cli.cli_movie import _run_movie

        manifest = _manifest(
            scenes=(Scene(id="s1", action="x"), Scene(id="s2", action="y")),
        )
        state = MovieState(title="T", project="p")
        state.scenes["s1"] = SceneState(
            media_id="m",
            flow_operation_id="op-old",
            local_path="/out/v.mp4",
            status="completed",
        )
        state_path = tmp_path / "m-state.json"
        gen = AsyncMock(return_value=_make_video_result())

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as mock_recorder_cls,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie._generate_scene", new=gen),
            patch("asyncio.sleep", new=AsyncMock()),
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

        gen.assert_awaited_once()  # s1 skipped, s2 generated
        assert state.scenes["s2"].status == "completed"


# ---------------------------------------------------------------------------
# _generate_scene
# ---------------------------------------------------------------------------


class TestGenerateScene:
    async def test_t2v_calls_generate_video(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene

        scene = Scene(id="s", action="a beautiful sunset", duration=8)
        mock_client = AsyncMock()
        mock_result = _make_video_result()
        mock_client.generate_video.return_value = mock_result

        with patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None):
            result = await _generate_scene(
                client=mock_client,
                recorder=MagicMock(),
                scene=scene,
                prompt="A beautiful sunset.",
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        assert result is mock_result
        mock_client.generate_video.assert_called_once()
        call_kwargs = mock_client.generate_video.call_args.kwargs
        assert call_kwargs["req"].mode.value == "t2v"
        assert call_kwargs["req"].prompt == "A beautiful sunset."

    async def test_character_scene_without_refs_is_t2v_in_p1(self, tmp_path: Path) -> None:
        """P1 is text-identity: a named-character scene with no reference
        entities submits as T2V (character is embedded in the prompt)."""
        from gflow_cli.cli_movie import _generate_scene

        scene = Scene(id="s", action="x", characters=("Alice",))
        mock_client = AsyncMock()
        mock_client.generate_video.return_value = _make_video_result()

        with patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None):
            await _generate_scene(
                client=mock_client,
                recorder=MagicMock(),
                scene=scene,
                prompt="Alice walks.",
                reference_entities=(),  # P1 text identity
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        call_kwargs = mock_client.generate_video.call_args.kwargs
        assert call_kwargs["req"].mode.value == "t2v"

    async def test_failed_video_raises_runtime_error(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene

        scene = Scene(id="s", action="x")
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
                scene=scene,
                prompt="x.",
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

    async def test_on_started_datastore_error_is_silent(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene
        from gflow_cli.errors import DataStoreError

        scene = Scene(id="s", action="x")
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
                scene=scene,
                prompt="x.",
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        assert result is mock_result

    async def test_completed_datastore_error_is_silent(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _generate_scene
        from gflow_cli.errors import DataStoreError

        scene = Scene(id="s", action="x")
        mock_result = _make_video_result()
        mock_recorder = MagicMock()
        mock_recorder.record_completed_video.side_effect = DataStoreError("db fail")
        mock_client = AsyncMock()
        mock_client.generate_video.return_value = mock_result

        with patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None):
            result = await _generate_scene(
                client=mock_client,
                recorder=mock_recorder,
                scene=scene,
                prompt="x.",
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        assert result is mock_result

    async def test_entity_scene_passes_entities_to_request(self, tmp_path: Path) -> None:
        """_generate_scene forwards reference_entities AND reference_entity_names
        to GenerateVideoRequest and sets mode=R2V when entities are present
        (spec §8 / regression guard for the 'entities accepted but not wired to
        DTO' bug, and the live-e2e bug where the picker searched by UUID instead
        of display name)."""
        from gflow_cli.api.video import Mode
        from gflow_cli.cli_movie import _generate_scene

        scene = Scene(id="s", action="stands tall", characters=("Hero",))
        mock_client = AsyncMock()
        mock_client.generate_video.return_value = _make_video_result()

        with patch("gflow_cli.cli_movie.cloud_info_from_path", return_value=None):
            await _generate_scene(
                client=mock_client,
                recorder=MagicMock(),
                scene=scene,
                prompt="Hero stands tall.",
                reference_entities=("ent-9",),
                reference_entity_names=("Stickman",),
                profile_name="default",
                profile_dir=tmp_path / "profile",
                out_dir=tmp_path / "out",
            )

        call_kwargs = mock_client.generate_video.call_args.kwargs
        req = call_kwargs["req"]
        assert req.reference_entities == ("ent-9",), (
            "reference_entities must be forwarded to GenerateVideoRequest "
            "so the transport can attach entity references"
        )
        assert req.reference_entity_names == ("Stickman",), (
            "reference_entity_names must be forwarded to GenerateVideoRequest "
            "so the UI picker selects tiles by display name, not UUID"
        )
        assert req.mode is Mode.R2V, "entity scene must flip to R2V mode"


# ---------------------------------------------------------------------------
# _ffmpeg_concat / --stitch
# ---------------------------------------------------------------------------


class TestStitch:
    def test_ffmpeg_concat_skips_when_no_ffmpeg(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _ffmpeg_concat

        out = tmp_path / "preview.mp4"
        with patch("gflow_cli.cli_movie.shutil.which", return_value=None):
            _ffmpeg_concat([tmp_path / "a.mp4", tmp_path / "b.mp4"], out)
        assert not out.exists()

    def test_ffmpeg_concat_invokes_subprocess(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _ffmpeg_concat

        out = tmp_path / "preview.mp4"
        with (
            patch("gflow_cli.cli_movie.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("gflow_cli.cli_movie.subprocess.run") as mock_run,
        ):
            _ffmpeg_concat([tmp_path / "a.mp4", tmp_path / "b.mp4"], out)
        mock_run.assert_called_once()
        argv = mock_run.call_args.args[0]
        assert argv[0] == "/usr/bin/ffmpeg"
        assert "concat" in argv


# ---------------------------------------------------------------------------
# P2: native (entity) identity wiring
# ---------------------------------------------------------------------------


class TestEntityIdentity:
    async def test_entity_scene_passes_reference_entities(self, tmp_path: Path) -> None:
        """A scene naming an entity-identity character (entity_id pre-seeded in
        state) flips to the entity path: _generate_scene is awaited with the
        resolved entity id."""
        from gflow_cli.cli_movie import _run_movie

        manifest = _manifest(
            characters={
                "Hero": Character(
                    name="Hero",
                    identity="entity",
                    face_prompt="a heroic face",
                    voice="Charon",
                )
            },
            scenes=(Scene(id="s", action="stands tall", characters=("Hero",)),),
        )
        state = MovieState(title="T", project="p")
        # Pre-seed the created entity so no creation phase runs.
        state.characters["Hero"] = CharacterState(entity_id="ent-9", image_paths=[])
        state_path = tmp_path / "m-state.json"
        gen = AsyncMock(return_value=_make_video_result())

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as rec,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie._generate_scene", new=gen),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            rec.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "p",
                out_dir=tmp_path / "out",
                continue_on_error=True,
            )

        gen.assert_awaited_once()
        assert gen.call_args.kwargs["reference_entities"] == ("ent-9",)
        assert gen.call_args.kwargs["reference_entity_names"] == ("Hero",), (
            "_run_movie must pass character display names so the UI picker "
            "can select tiles by name instead of UUID"
        )
        assert gen.call_args.kwargs["reference_audio"] is None
        assert state.scenes["s"].consistency_method == "entity"

    async def test_missing_entity_id_fails_loud(self, tmp_path: Path) -> None:
        """An entity-identity character with no created entity_id in state must
        fail loud (not silently drop the reference). Under fail-fast the
        ConfigurationError propagates out of the run."""
        from gflow_cli.cli_movie import _run_movie
        from gflow_cli.errors import ConfigurationError

        manifest = _manifest(
            characters={
                "Hero": Character(name="Hero", identity="entity", face_prompt="a face")
            },
            scenes=(Scene(id="s", action="x", characters=("Hero",)),),
        )
        state = MovieState(title="T", project="p")  # no entity created for Hero
        state_path = tmp_path / "m-state.json"

        with (  # noqa: SIM117
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as rec,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie._create_character", new=AsyncMock()),
            patch("gflow_cli.cli_movie._generate_scene", new=AsyncMock()) as gen,
            patch("asyncio.sleep", new=AsyncMock()),
            pytest.raises(ConfigurationError, match="entity"),
        ):
            rec.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "p",
                out_dir=tmp_path / "out",
                continue_on_error=False,  # fail-fast: re-raise
            )
        gen.assert_not_awaited()  # never generated with a dropped reference

    async def test_creation_phase_forwards_voice(self, tmp_path: Path) -> None:
        """The Phase-1 creation loop calls character_create forwarding the
        manifest character's embedded voice (council C2)."""
        from gflow_cli.api.character import CharacterCreateResult
        from gflow_cli.cli_movie import _run_movie

        manifest = _manifest(
            characters={
                "Hero": Character(
                    name="Hero",
                    identity="entity",
                    face_prompt="a heroic face",
                    body_prompt="a heroic body",
                    voice="alnilam",
                )
            },
            scenes=(Scene(id="s", action="x", characters=("Hero",)),),
        )
        state = MovieState(title="T", project="p")
        state_path = tmp_path / "m-state.json"

        create_mock = AsyncMock(
            return_value=CharacterCreateResult(
                entity_id="ent-new",
                project_id="p",
                workflow_ids=(),
                primary_media_ids=(),
                name="Hero",
                voice="alnilam",
                image_paths=("/out/face.png", "/out/body.png"),
            )
        )

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as rec,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie.character_create", new=create_mock),
            patch(
                "gflow_cli.cli_movie._generate_scene",
                new=AsyncMock(return_value=_make_video_result()),
            ),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            rec.open.return_value = MagicMock()
            await _run_movie(
                manifest=manifest,
                state=state,
                state_path=state_path,
                profile_name="default",
                profile_dir=tmp_path / "p",
                out_dir=tmp_path / "out",
                continue_on_error=True,
            )

        create_mock.assert_awaited_once()
        assert create_mock.call_args.kwargs["voice"] == "alnilam"
        assert create_mock.call_args.kwargs["name"] == "Hero"
        # Body prompt present -> a body request was built.
        assert create_mock.call_args.kwargs["body"] is not None
        # Entity id was persisted to state for downstream scene resolution.
        assert state.characters["Hero"].entity_id == "ent-new"
