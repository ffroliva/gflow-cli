"""Auth helpers — pure-function tests (no Playwright, no network)."""

from __future__ import annotations

from pathlib import Path

from flow_cli import auth


class TestProfileDir:
    def test_default_profile_under_home(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FLOW_CLI_HOME", str(tmp_path))
        pdir = auth.profile_dir()
        assert pdir == tmp_path / "profile_default"

    def test_named_profile(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FLOW_CLI_HOME", str(tmp_path))
        pdir = auth.profile_dir("alt")
        assert pdir == tmp_path / "profile_alt"


class TestStatus:
    def test_missing_profile(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FLOW_CLI_HOME", str(tmp_path))
        s = auth.status("noexist")
        assert s["exists"] is False
        assert s["cookies_present"] is False

    def test_present_profile_no_cookies(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FLOW_CLI_HOME", str(tmp_path))
        (tmp_path / "profile_x").mkdir()
        s = auth.status("x")
        assert s["exists"] is True
        assert s["cookies_present"] is False
