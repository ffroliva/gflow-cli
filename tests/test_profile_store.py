"""Tests for the profile inventory + default-resolution logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli import profile_store


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pretend $GFLOW_CLI_HOME is a fresh tmp dir."""
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.delenv("GFLOW_CLI_PROFILE", raising=False)
    reset_settings()
    yield tmp_path
    reset_settings()


def _mk_profile(home: Path, name: str, with_cookies: bool = True) -> Path:
    pdir = home / f"profile_{name}"
    (pdir / "Default").mkdir(parents=True, exist_ok=True)
    if with_cookies:
        (pdir / "Default" / "Cookies").write_bytes(b"")
    return pdir


class TestListProfiles:
    def test_empty_when_home_missing(self, home: Path) -> None:
        # home dir exists (fixture made it) but has no profile_* subdirs
        assert profile_store.list_profiles() == []

    def test_lists_profile_dirs_sorted(self, home: Path) -> None:
        _mk_profile(home, "work")
        _mk_profile(home, "default")
        _mk_profile(home, "personal")
        names = [p.name for p in profile_store.list_profiles()]
        assert names == ["default", "personal", "work"]

    def test_ignores_non_profile_dirs(self, home: Path) -> None:
        (home / "scratch").mkdir()
        (home / "config.toml").write_text("")
        _mk_profile(home, "ok")
        assert [p.name for p in profile_store.list_profiles()] == ["ok"]

    def test_cookies_present_flag(self, home: Path) -> None:
        _mk_profile(home, "with_cookies", with_cookies=True)
        _mk_profile(home, "no_cookies", with_cookies=False)
        by_name = {p.name: p for p in profile_store.list_profiles()}
        assert by_name["with_cookies"].cookies_present is True
        assert by_name["no_cookies"].cookies_present is False


class TestSetDefault:
    def test_sets_and_reads_back(self, home: Path) -> None:
        _mk_profile(home, "work")
        cfg = profile_store.set_default_profile("work")
        assert cfg == home / "config.toml"
        assert profile_store.get_default_profile() == "work"

    def test_rejects_nonexistent_profile(self, home: Path) -> None:
        with pytest.raises(FileNotFoundError):
            profile_store.set_default_profile("ghost")

    def test_overwrites(self, home: Path) -> None:
        _mk_profile(home, "a")
        _mk_profile(home, "b")
        profile_store.set_default_profile("a")
        profile_store.set_default_profile("b")
        assert profile_store.get_default_profile() == "b"


class TestClearDefault:
    def test_removes_key_with_multiple_profiles(self, home: Path) -> None:
        # Two profiles so auto-select doesn't fall through to a single profile.
        _mk_profile(home, "x")
        _mk_profile(home, "y")
        profile_store.set_default_profile("x")
        profile_store.clear_default_profile()
        assert profile_store.get_default_profile() is None

    def test_clear_then_single_profile_autoselects(self, home: Path) -> None:
        # With exactly one profile, clearing the explicit default still yields
        # that profile via auto-select. This documents the layered default
        # resolution chain.
        _mk_profile(home, "only")
        profile_store.set_default_profile("only")
        profile_store.clear_default_profile()
        assert profile_store.get_default_profile() == "only"


class TestGetDefaultAutoselect:
    def test_one_profile_is_de_facto_default(self, home: Path) -> None:
        _mk_profile(home, "only_one")
        # No explicit default set, but a single profile should be auto-picked.
        assert profile_store.get_default_profile() == "only_one"

    def test_two_profiles_no_explicit_default_is_ambiguous(self, home: Path) -> None:
        _mk_profile(home, "a")
        _mk_profile(home, "b")
        assert profile_store.get_default_profile() is None


class TestResolveProfile:
    def test_cli_flag_wins(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _mk_profile(home, "default")
        monkeypatch.setenv("GFLOW_CLI_PROFILE", "env_pref")
        assert profile_store.resolve_profile("flag_pref") == "flag_pref"

    def test_env_beats_config(self, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _mk_profile(home, "from_config")
        profile_store.set_default_profile("from_config")
        monkeypatch.setenv("GFLOW_CLI_PROFILE", "env_choice")
        assert profile_store.resolve_profile(None) == "env_choice"

    def test_config_beats_autoselect(self, home: Path) -> None:
        _mk_profile(home, "alpha")
        _mk_profile(home, "beta")
        profile_store.set_default_profile("beta")
        assert profile_store.resolve_profile(None) == "beta"

    def test_autoselect_when_only_one(self, home: Path) -> None:
        _mk_profile(home, "solo")
        assert profile_store.resolve_profile(None) == "solo"

    def test_no_profiles_raises(self, home: Path) -> None:
        with pytest.raises(profile_store.NoProfilesError):
            profile_store.resolve_profile(None)

    def test_multiple_no_default_raises(self, home: Path) -> None:
        _mk_profile(home, "a")
        _mk_profile(home, "b")
        with pytest.raises(profile_store.NoDefaultProfileError) as exc:
            profile_store.resolve_profile(None)
        assert exc.value.available == ["a", "b"]


class TestDelete:
    def test_removes_dir(self, home: Path) -> None:
        pdir = _mk_profile(home, "byebye")
        assert pdir.exists()
        profile_store.delete_profile("byebye")
        assert not pdir.exists()

    def test_clears_default_if_was_default(self, home: Path) -> None:
        _mk_profile(home, "primary")
        profile_store.set_default_profile("primary")
        profile_store.delete_profile("primary")
        assert profile_store.get_default_profile() is None

    def test_missing_raises(self, home: Path) -> None:
        with pytest.raises(FileNotFoundError):
            profile_store.delete_profile("never_existed")


class TestGoogleAccount:
    def test_none_when_file_absent(self, home: Path) -> None:
        _mk_profile(home, "alice")
        profiles = {p.name: p for p in profile_store.list_profiles()}
        assert profiles["alice"].google_account is None

    def test_populated_from_account_file(self, home: Path) -> None:
        pdir = _mk_profile(home, "alice")
        (pdir / profile_store.ACCOUNT_FILE).write_text("alice@example.com", encoding="utf-8")
        profiles = {p.name: p for p in profile_store.list_profiles()}
        assert profiles["alice"].google_account == "alice@example.com"

    def test_empty_account_file_treated_as_none(self, home: Path) -> None:
        pdir = _mk_profile(home, "bob")
        (pdir / profile_store.ACCOUNT_FILE).write_text("   ", encoding="utf-8")
        profiles = {p.name: p for p in profile_store.list_profiles()}
        assert profiles["bob"].google_account is None


class TestRenameProfile:
    def test_renames_directory(self, home: Path) -> None:
        _mk_profile(home, "old")
        new_dir = profile_store.rename_profile("old", "new")
        assert new_dir.exists()
        assert not (home / "profile_old").exists()

    def test_missing_source_raises(self, home: Path) -> None:
        with pytest.raises(FileNotFoundError):
            profile_store.rename_profile("ghost", "new")

    def test_existing_target_raises(self, home: Path) -> None:
        _mk_profile(home, "old")
        _mk_profile(home, "new")
        with pytest.raises(FileExistsError):
            profile_store.rename_profile("old", "new")

    def test_updates_default_in_config(self, home: Path) -> None:
        _mk_profile(home, "old")
        profile_store.set_default_profile("old")
        profile_store.rename_profile("old", "new")
        assert profile_store.get_default_profile() == "new"

    def test_leaves_config_alone_when_not_default(self, home: Path) -> None:
        _mk_profile(home, "keep")
        _mk_profile(home, "old")
        profile_store.set_default_profile("keep")
        profile_store.rename_profile("old", "renamed")
        assert profile_store.get_default_profile() == "keep"
