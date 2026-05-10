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
