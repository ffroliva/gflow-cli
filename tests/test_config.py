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
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every GFLOW_CLI_* and legacy FLOW_CLI_* env var to expose true defaults."""
    for key in list(__import__("os").environ):
        if key.startswith("GFLOW_CLI_") or key.startswith("FLOW_CLI_"):
            monkeypatch.delenv(key, raising=False)


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
