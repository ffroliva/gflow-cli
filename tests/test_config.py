"""Tests for the pydantic-settings Settings model."""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.config import (
    BrowserEngine,
    LogFormat,
    LogLevel,
    Provider,
    Settings,
    reset_settings,
)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strip every GFLOW_CLI_* / legacy FLOW_CLI_* env var AND fence the dotenv sweep.

    Stripping alone removes the autouse tmp GFLOW_CLI_HOME (conftest
    `_isolate_settings`), which would send the home-.env lookup to the
    developer's REAL platform home; the CWD entry would read whatever
    directory pytest was launched from. Both are re-fenced into tmp_path so
    "defaults" tests never depend on real machine files. Tests that need a
    different home/cwd simply set their own afterwards (later patches win).
    """
    for key in list(__import__("os").environ):
        if key.startswith("GFLOW_CLI_") or key.startswith("FLOW_CLI_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("gflow_cli.config.paths.default_home", lambda: tmp_path / "clean-home")
    clean_cwd = tmp_path / "clean-cwd"
    clean_cwd.mkdir(exist_ok=True)
    monkeypatch.chdir(clean_cwd)


class TestCleanEnvHermeticity:
    """`clean_env` must fence the dotenv sweep, not just strip env vars.

    Stripping removes the autouse tmp GFLOW_CLI_HOME, which would otherwise
    send the home-.env lookup to the developer's REAL platform home — a real
    home .env (e.g. GFLOW_CLI_TIMEOUT_SECONDS=300) then fails the defaults
    tests on that machine while CI stays green. Same for the CWD entry when
    pytest is launched from a directory containing a .env (the repo root's
    gitignored .env is the documented dev setup).
    """

    def test_dotenv_sweep_is_fenced_inside_the_test_tmp_dir(
        self, clean_env: None, tmp_path: Path
    ) -> None:
        from gflow_cli import config as config_module

        home_env_file, _cwd_env_file = config_module._env_files()
        assert str(tmp_path) in home_env_file  # not the real platform home
        assert str(tmp_path) in str(Path.cwd())  # not the pytest launch dir


class TestDefaults:
    def test_provider_defaults_to_flow(self, clean_env: None) -> None:
        s = Settings()
        assert s.provider == Provider.FLOW

    def test_log_level_defaults_to_info(self, clean_env: None) -> None:
        assert Settings().log_level == LogLevel.INFO

    def test_log_format_defaults_to_auto(self, clean_env: None) -> None:
        assert Settings().log_format == LogFormat.AUTO

    def test_timeout_defaults_to_600(self, clean_env: None) -> None:
        assert Settings().timeout_seconds == 600

    def test_concurrency_defaults_to_1(self, clean_env: None) -> None:
        assert Settings().concurrency == 1

    def test_home_default_is_a_real_path(self, clean_env: None) -> None:
        # Don't assume a specific OS layout — just check it resolves to *something*.
        h = Settings().home
        assert isinstance(h, Path)
        assert "gflow-cli" in str(h).lower()

    def test_output_dir_default_includes_gflow_cli(self, clean_env: None) -> None:
        out = Settings().output_dir
        assert isinstance(out, Path)
        assert "gflow-cli" in str(out).lower()


class TestEnvOverrides:
    def test_home_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
        assert Settings().home == tmp_path

    def test_output_dir_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GFLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
        assert Settings().output_dir == tmp_path / "out"

    def test_provider_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_PROVIDER", "official")
        assert Settings().provider == Provider.OFFICIAL

    def test_log_level_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_LOG_LEVEL", "DEBUG")
        assert Settings().log_level == LogLevel.DEBUG

    def test_invalid_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_PROVIDER", "invented")
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings()

    def test_timeout_below_min_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pydantic import ValidationError

        monkeypatch.setenv("GFLOW_CLI_TIMEOUT_SECONDS", "0")
        with pytest.raises(ValidationError):
            Settings()

    def test_headless_defaults_false(self, clean_env: None) -> None:
        # ui_automation transport requires headed Chrome — reCAPTCHA rejects headless
        assert Settings().headless is False

    def test_headless_override_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_HEADLESS", "true")
        assert Settings().headless is True


class TestDerivedPaths:
    def test_profile_subdir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
        s = Settings()
        assert s.profile_subdir("work") == tmp_path / "profile_work"

    def test_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
        s = Settings()
        assert s.config_file() == tmp_path / "config.toml"


class TestLegacyEnvShim:
    """`FLOW_CLI_*` → `GFLOW_CLI_*` migration shim (config._migrate_legacy_env).

    The shim is removed in v0.5.0. Until then it promotes any legacy var to
    the new prefix when the new var isn't set, and emits one
    `DeprecationWarning` per process listing the promoted keys.
    """

    def test_legacy_var_promoted_when_new_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import warnings

        from gflow_cli.config import _migrate_legacy_env

        monkeypatch.setenv("FLOW_CLI_HOME", "/legacy/path")
        monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _migrate_legacy_env()

        import os

        assert os.environ.get("GFLOW_CLI_HOME") == "/legacy/path"
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)
        assert any("FLOW_CLI_HOME" in str(w.message) for w in caught)

    def test_new_var_wins_when_both_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If both prefixes are set, the new GFLOW_CLI_* var MUST NOT be clobbered."""
        from gflow_cli.config import _migrate_legacy_env

        monkeypatch.setenv("FLOW_CLI_HOME", "/legacy/value")
        monkeypatch.setenv("GFLOW_CLI_HOME", "/explicit/new/value")

        _migrate_legacy_env()

        import os

        assert os.environ["GFLOW_CLI_HOME"] == "/explicit/new/value"

    def test_no_warning_when_no_legacy_vars_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Strip both prefixes to give a clean baseline.
        import os
        import warnings

        from gflow_cli.config import _migrate_legacy_env

        for key in list(os.environ):
            if key.startswith("FLOW_CLI_") or key.startswith("GFLOW_CLI_"):
                monkeypatch.delenv(key, raising=False)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _migrate_legacy_env()

        assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


class TestHomeEnvFileFallback:
    """Issue #240: `.env` must also load from `$GFLOW_CLI_HOME/.env`.

    docs/CONFIGURATION.md documents a home-`.env` fallback with CWD winning;
    before the fix only the CWD `.env` was ever read.
    """

    @pytest.fixture
    def isolated_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        """Run the test from an empty directory so the repo's own `.env` never leaks in."""
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        return cwd

    @pytest.fixture
    def home_with_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        home = tmp_path / "home"
        home.mkdir()
        (home / ".env").write_text("GFLOW_CLI_TIMEOUT_SECONDS=123\n", encoding="utf-8")
        monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
        return home

    def test_home_env_file_is_loaded_without_cwd_env(
        self, clean_env: None, isolated_cwd: Path, home_with_env: Path
    ) -> None:
        assert Settings().timeout_seconds == 123

    def test_home_and_cwd_env_files_merge(
        self, clean_env: None, isolated_cwd: Path, home_with_env: Path
    ) -> None:
        (isolated_cwd / ".env").write_text("GFLOW_CLI_CONCURRENCY=3\n", encoding="utf-8")
        s = Settings()
        assert s.timeout_seconds == 123  # from home .env
        assert s.concurrency == 3  # from CWD .env

    def test_cwd_env_file_wins_over_home_env_file(
        self, clean_env: None, isolated_cwd: Path, home_with_env: Path
    ) -> None:
        (isolated_cwd / ".env").write_text("GFLOW_CLI_TIMEOUT_SECONDS=456\n", encoding="utf-8")
        assert Settings().timeout_seconds == 456

    def test_process_env_var_beats_both_env_files(
        self,
        clean_env: None,
        isolated_cwd: Path,
        home_with_env: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (isolated_cwd / ".env").write_text("GFLOW_CLI_TIMEOUT_SECONDS=456\n", encoding="utf-8")
        monkeypatch.setenv("GFLOW_CLI_TIMEOUT_SECONDS", "789")
        assert Settings().timeout_seconds == 789

    def test_default_home_env_file_used_when_home_var_unset(
        self,
        clean_env: None,
        isolated_cwd: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        home = tmp_path / "default-home"
        home.mkdir()
        (home / ".env").write_text("GFLOW_CLI_TIMEOUT_SECONDS=321\n", encoding="utf-8")
        monkeypatch.setattr("gflow_cli.config.paths.default_home", lambda: home)
        assert Settings().timeout_seconds == 321

    def test_missing_home_env_file_is_harmless(
        self,
        clean_env: None,
        isolated_cwd: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        home = tmp_path / "home-no-env"
        home.mkdir()
        monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
        assert Settings().timeout_seconds == 600  # built-in default


class TestHomeResolutionCoherence:
    """#240 rework: the home used to locate the home ``.env`` must be the same
    home the ``home`` field resolves to — for every channel that can set it.
    """

    @pytest.fixture
    def isolated_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        return cwd

    def test_home_set_in_cwd_env_file_relocates_home_env_lookup(
        self, clean_env: None, isolated_cwd: Path, tmp_path: Path
    ) -> None:
        new_home = tmp_path / "relocated-home"
        new_home.mkdir()
        (new_home / ".env").write_text("GFLOW_CLI_TIMEOUT_SECONDS=123\n", encoding="utf-8")
        (isolated_cwd / ".env").write_text(f"GFLOW_CLI_HOME={new_home}\n", encoding="utf-8")
        s = Settings()
        assert s.home == new_home
        assert s.timeout_seconds == 123  # the relocated home's .env was loaded

    def test_empty_home_env_var_means_unset_for_field_and_env_lookup(
        self,
        clean_env: None,
        isolated_cwd: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        default = tmp_path / "default-gflow-cli"
        default.mkdir()
        (default / ".env").write_text("GFLOW_CLI_TIMEOUT_SECONDS=321\n", encoding="utf-8")
        monkeypatch.setattr("gflow_cli.config.paths.default_home", lambda: default)
        monkeypatch.setenv("GFLOW_CLI_HOME", "")
        s = Settings()
        assert s.timeout_seconds == 321  # env lookup fell back to default home
        assert s.home == default  # the field agrees — not Path('.')

    def test_lowercase_home_env_var_honored_for_env_lookup(
        self,
        clean_env: None,
        isolated_cwd: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Simulate a POSIX case-sensitive environment where only the lowercase
        # key exists (valid for the field: case_sensitive=False).
        import gflow_cli.config as config_module

        home = tmp_path / "lower-home"
        home.mkdir()
        (home / ".env").write_text("GFLOW_CLI_TIMEOUT_SECONDS=222\n", encoding="utf-8")
        monkeypatch.setattr(config_module.os, "environ", {"gflow_cli_home": str(home)})
        s = Settings()
        assert s.home == home
        assert s.timeout_seconds == 222


class TestEnvFileInitKwarg:
    """The standard pydantic-settings ``_env_file`` init kwarg keeps working.

    Regression for the #240 rework: the first implementation replaced the
    framework dotenv source wholesale, silently ignoring
    ``Settings(_env_file=...)`` including the disable idiom ``_env_file=None``.
    """

    @pytest.fixture
    def isolated_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        return cwd

    @pytest.fixture
    def home_with_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        home = tmp_path / "home"
        home.mkdir()
        (home / ".env").write_text("GFLOW_CLI_TIMEOUT_SECONDS=123\n", encoding="utf-8")
        monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
        return home

    def test_env_file_none_disables_all_dotenv_loading(
        self, clean_env: None, isolated_cwd: Path, home_with_env: Path
    ) -> None:
        (isolated_cwd / ".env").write_text("GFLOW_CLI_CONCURRENCY=3\n", encoding="utf-8")
        s = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
        assert s.timeout_seconds == 600  # home .env skipped
        assert s.concurrency == 1  # CWD .env skipped

    def test_explicit_env_file_replaces_both_defaults(
        self, clean_env: None, isolated_cwd: Path, home_with_env: Path
    ) -> None:
        (isolated_cwd / ".env").write_text("GFLOW_CLI_TIMEOUT_SECONDS=456\n", encoding="utf-8")
        other = isolated_cwd / "other.env"
        other.write_text("GFLOW_CLI_TIMEOUT_SECONDS=999\n", encoding="utf-8")
        s = Settings(_env_file=other)  # pyright: ignore[reportCallIssue]
        assert s.timeout_seconds == 999


class TestBrowserEngine:
    """GFLOW_CLI_BROWSER_ENGINE typed enum field (patchright opt-in)."""

    def test_defaults_to_playwright(self, clean_env: None) -> None:
        assert Settings().browser_engine == BrowserEngine.PLAYWRIGHT

    def test_override_patchright(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_BROWSER_ENGINE", "patchright")
        assert Settings().browser_engine == BrowserEngine.PATCHRIGHT

    def test_invalid_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pydantic import ValidationError

        # A typo must fail loudly at startup naming the field, not fall through.
        monkeypatch.setenv("GFLOW_CLI_BROWSER_ENGINE", "patchwright")
        with pytest.raises(ValidationError):
            Settings()


class TestPreferClassic:
    def test_defaults_to_false(self, clean_env: None) -> None:
        assert Settings().prefer_classic is False

    def test_override_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_PREFER_CLASSIC", "true")
        assert Settings().prefer_classic is True


class TestDaemonSettings:
    def test_daemon_defaults(self, clean_env: None) -> None:
        s = Settings()
        assert s.daemon_token is None
        assert s.daemon_port == 8000

    def test_daemon_token_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_DAEMON_TOKEN", "secret-token")
        assert Settings().daemon_token == "secret-token"

        monkeypatch.setenv("GFLOW_CLI_DAEMON_TOKEN", "other-token")
        reset_settings()
        assert Settings().daemon_token == "other-token"

    def test_daemon_port_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_DAEMON_PORT", "9000")
        assert Settings().daemon_port == 9000

        monkeypatch.setenv("GFLOW_CLI_DAEMON_PORT", "9001")
        reset_settings()
        assert Settings().daemon_port == 9001

    def test_invalid_daemon_port_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pydantic import ValidationError

        monkeypatch.setenv("GFLOW_DAEMON_PORT", "70000")
        with pytest.raises(ValidationError):
            Settings()
