"""Tests for the XDG-aware path resolver."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from gflow_cli import paths


class TestDefaultRoots:
    def test_default_home_returns_path(self) -> None:
        h = paths.default_home()
        assert isinstance(h, Path)
        # The directory may not exist yet — we don't auto-create.
        assert "gflow-cli" in str(h).lower()

    def test_default_output_dir_returns_path(self) -> None:
        out = paths.default_output_dir()
        assert isinstance(out, Path)
        assert "gflow-cli" in str(out).lower()


class TestProfileSubdir:
    def test_under_home(self) -> None:
        home = Path("/x/gflow-cli")
        assert paths.profile_subdir(home, "default") == Path("/x/gflow-cli/profile_default")
        assert paths.profile_subdir(home, "work") == Path("/x/gflow-cli/profile_work")


class TestConfigFile:
    def test_under_home(self) -> None:
        home = Path("/x/gflow-cli")
        assert paths.config_file(home) == Path("/x/gflow-cli/config.toml")


class TestVideoOutputPath:
    def test_default_uses_today(self) -> None:
        p = paths.video_output_path(Path("/out"), job_id="abcd-1234")
        assert "videos" in p.parts
        assert "abcd-1234.mp4" == p.name

    def test_explicit_date(self) -> None:
        p = paths.video_output_path(Path("/out"), job_id="x", on=date(2026, 1, 15))
        assert p == Path("/out/videos/2026-01-15/x.mp4")


class TestImageOutputPath:
    def test_indexed_filename(self) -> None:
        p = paths.image_output_path(Path("/out"), job_id="x", index=3, on=date(2026, 1, 15))
        assert p == Path("/out/images/2026-01-15/x_3.png")


class TestResolveBatchOutputDirExpanduser:
    """resolve_batch_output_dir() must expand ``~`` so users can put
    ``~/gflow-output`` in their config files / CLI flags without it being
    interpreted as a literal directory name. Regression guard for the
    examples/sample_config.json default that landed in the root-leak
    cleanup PR."""

    def test_config_value_expanduser(self) -> None:
        home = Path.home()
        out = paths.resolve_batch_output_dir(
            cli_override=None,
            config_value="~/gflow-output/example-batch",
            output_root=Path("/unused"),
        )
        assert out == home / "gflow-output" / "example-batch"
        assert "~" not in str(out)

    def test_cli_override_expanduser(self) -> None:
        home = Path.home()
        out = paths.resolve_batch_output_dir(
            cli_override=Path("~/some/where"),
            output_root=Path("/unused"),
        )
        assert out == home / "some" / "where"

    def test_output_root_expanduser(self) -> None:
        home = Path.home()
        out = paths.resolve_batch_output_dir(
            cli_override=None,
            config_value=None,
            output_root=Path("~/data-root"),
            kind="images",
        )
        expected_prefix = (home / "data-root" / "images").parts
        assert out.parts[: len(expected_prefix)] == expected_prefix

    def test_absolute_config_value_unchanged(self) -> None:
        out = paths.resolve_batch_output_dir(
            cli_override=None,
            config_value="/abs/path",
            output_root=Path("/unused"),
        )
        assert out == Path("/abs/path")
