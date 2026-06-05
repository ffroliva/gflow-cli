"""Tests for `gflow movie` — movie.toml orchestrator CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main as cli_main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


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
        from gflow_cli.movie_manifest import MovieManifest

        runner = CliRunner()
        out = tmp_path / "movie.toml"
        runner.invoke(cli_main, ["movie", "template", str(out)])
        # Should parse without error (project id placeholder is a valid string)
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
        from unittest.mock import patch

        runner = CliRunner()
        manifest = _write_toml(tmp_path / "movie.toml", _VALID_MANIFEST)
        with patch("gflow_cli.cli_movie.FlowApiClient") as mock_client:
            result = runner.invoke(cli_main, ["movie", "run", str(manifest), "--dry-run"])
        assert result.exit_code == 0
        mock_client.assert_not_called()

    def test_dry_run_shows_skip_for_completed_scene(self, tmp_path: Path) -> None:
        from gflow_cli.movie_manifest import MovieState, SceneState

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
