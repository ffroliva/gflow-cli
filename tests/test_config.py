"""Tests for the pydantic-settings Settings model."""

from __future__ import annotations

from pathlib import Path

import pytest

from flow_cli.config import LogFormat, LogLevel, Provider, Settings, reset_settings


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every FLOW_CLI_* env var to expose true defaults."""
    for key in list(__import__("os").environ):
        if key.startswith("FLOW_CLI_"):
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
        assert "flow-cli" in str(h).lower()

    def test_output_dir_default_includes_flow_cli(self, clean_env: None) -> None:
        out = Settings().output_dir
        assert isinstance(out, Path)
        assert "flow-cli" in str(out).lower()


class TestEnvOverrides:
    def test_home_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FLOW_CLI_HOME", str(tmp_path))
        assert Settings().home == tmp_path

    def test_output_dir_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FLOW_CLI_OUTPUT_DIR", str(tmp_path / "out"))
        assert Settings().output_dir == tmp_path / "out"

    def test_provider_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLOW_CLI_PROVIDER", "official")
        assert Settings().provider == Provider.OFFICIAL

    def test_log_level_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLOW_CLI_LOG_LEVEL", "DEBUG")
        assert Settings().log_level == LogLevel.DEBUG

    def test_invalid_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FLOW_CLI_PROVIDER", "invented")
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings()

    def test_timeout_below_min_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pydantic import ValidationError

        monkeypatch.setenv("FLOW_CLI_TIMEOUT_SECONDS", "0")
        with pytest.raises(ValidationError):
            Settings()


class TestDerivedPaths:
    def test_profile_subdir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FLOW_CLI_HOME", str(tmp_path))
        s = Settings()
        assert s.profile_subdir("work") == tmp_path / "profile_work"

    def test_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FLOW_CLI_HOME", str(tmp_path))
        s = Settings()
        assert s.config_file() == tmp_path / "config.toml"
