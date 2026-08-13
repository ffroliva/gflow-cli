"""Tests for the pydantic-settings Settings model."""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.config import (
    DEFAULT_LLM_BASE_URL,
    BrowserEngine,
    LogFormat,
    LogLevel,
    Provider,
    Settings,
    reset_removed_gemini_key_notice,
    reset_settings,
    warn_if_removed_gemini_key_set,
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
    def test_daemon_token_defined_exactly_once(self) -> None:
        """A duplicated class-body field is silently shadowed by the later
        definition (issue #243) — the dead duplicate must not return, or a
        future edit to the wrong copy would be invisibly ignored."""
        import inspect

        source = inspect.getsource(Settings)
        # Count ANY annotation spelling — a reintroduced duplicate with the old
        # `str | None` annotation would silently shadow the SecretStr field.
        assert source.count("daemon_token:") == 1

    def test_daemon_token_keeps_both_env_aliases(self) -> None:
        """Pin the SURVIVING definition's contract: both env var spellings
        must stay accepted (the shadowed duplicate had no aliases)."""
        field = Settings.model_fields["daemon_token"]
        alias = field.validation_alias
        assert alias is not None
        assert getattr(alias, "choices", None) == ["GFLOW_CLI_DAEMON_TOKEN", "GFLOW_DAEMON_TOKEN"]

    def test_daemon_defaults(self, clean_env: None) -> None:
        s = Settings()
        assert s.daemon_token is None
        assert s.daemon_port == 8000

    def test_daemon_token_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_DAEMON_TOKEN", "secret-token")
        token = Settings().daemon_token
        assert token is not None and token.get_secret_value() == "secret-token"

        monkeypatch.setenv("GFLOW_CLI_DAEMON_TOKEN", "other-token")
        reset_settings()
        token = Settings().daemon_token
        assert token is not None and token.get_secret_value() == "other-token"

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


class TestSecretFieldMasking:
    """Secret settings must be unreadable from any Settings dump by
    construction, not just at logging boundaries (issue #474)."""

    def test_dumps_never_contain_secret_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_LLM_API_KEY", "sk-CANARY-LLM-KEY")
        monkeypatch.setenv("GFLOW_CLI_DAEMON_TOKEN", "CANARY-DAEMON-TOKEN")
        s = Settings()
        for dump in (repr(s), str(s), s.model_dump_json()):
            assert "sk-CANARY-LLM-KEY" not in dump
            assert "CANARY-DAEMON-TOKEN" not in dump


class TestIncidentCapture:
    """GFLOW_CLI_INCIDENT_CAPTURE — private incident diagnostics (S35)."""

    def test_defaults_to_true(self, clean_env: None) -> None:
        assert Settings().incident_capture is True

    def test_env_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_INCIDENT_CAPTURE", "false")
        assert Settings().incident_capture is False

    def test_invalid_value_fails_at_settings_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid values fail through typed settings validation before any
        browser work — never silently coerced (S35)."""
        from pydantic import ValidationError

        monkeypatch.setenv("GFLOW_CLI_INCIDENT_CAPTURE", "notabool")
        with pytest.raises(ValidationError):
            Settings()


class TestLlmSettings:
    """The prompt-tools LLM endpoint (issue #387).

    ``llm_base_url`` is user-supplied and feeds ``urllib.request``, so it is a
    trust boundary rather than an ordinary string setting.
    """

    def test_defaults(self, clean_env: None) -> None:
        s = Settings()
        assert s.llm_base_url == DEFAULT_LLM_BASE_URL
        # Both optional: a key-only user keeps the default endpoint, and a
        # keyless local gateway needs no credential at all.
        assert s.llm_api_key is None
        assert s.llm_model is None

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "https://gw.example/v1")
        monkeypatch.setenv("GFLOW_CLI_LLM_API_KEY", "sk-abc")
        monkeypatch.setenv("GFLOW_CLI_LLM_MODEL", "openai/gpt-4o-mini")
        s = Settings()
        assert s.llm_base_url == "https://gw.example/v1"
        assert s.llm_api_key is not None
        assert s.llm_api_key.get_secret_value() == "sk-abc"
        assert s.llm_model == "openai/gpt-4o-mini"

    def test_empty_base_url_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "")
        assert Settings().llm_base_url == DEFAULT_LLM_BASE_URL

    @pytest.mark.parametrize("bad", ["file:///etc/passwd", "ftp://host/x", "gopher://h"])
    def test_rejects_non_http_schemes(self, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
        """urllib dispatches on scheme; file:// et al must never be reachable."""
        from pydantic import ValidationError

        monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", bad)
        with pytest.raises(ValidationError):
            Settings()

    def test_rejects_plain_http_for_remote_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Plain http would put GFLOW_CLI_LLM_API_KEY on the wire in cleartext."""
        from pydantic import ValidationError

        monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "http://gw.example/v1")
        with pytest.raises(ValidationError):
            Settings()

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:3001/v1",
            "http://127.0.0.1:3001/v1",
            "http://[::1]:3001/v1",
        ],
    )
    def test_allows_plain_http_for_loopback(
        self, monkeypatch: pytest.MonkeyPatch, url: str
    ) -> None:
        """A local gateway is a first-class use case and never leaves the host."""
        monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", url)
        assert Settings().llm_base_url == url

    def test_rejects_credentials_in_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pydantic import ValidationError

        monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "https://user:pass@gw.example/v1")
        with pytest.raises(ValidationError):
            Settings()

    def test_https_remote_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GFLOW_CLI_LLM_BASE_URL", "https://openrouter.ai/api/v1")
        assert Settings().llm_base_url == "https://openrouter.ai/api/v1"


class TestRemovedGeminiKeyNotice:
    """GFLOW_CLI_GEMINI_API_KEY was removed in v0.46.0 (issue #387).

    The prompt tools never raise, so without an explicit notice an unmigrated
    user would see no error at all — just silently un-rewritten prompts on
    full-price generations.
    """

    @pytest.fixture(autouse=True)
    def _reset_latch(self) -> None:
        reset_removed_gemini_key_notice()
        yield
        reset_removed_gemini_key_notice()

    def test_warns_when_only_removed_key_is_set(self) -> None:
        assert warn_if_removed_gemini_key_set({"GFLOW_CLI_GEMINI_API_KEY": "AIza-old"}) is True

    def test_warns_only_once(self) -> None:
        env = {"GFLOW_CLI_GEMINI_API_KEY": "AIza-old"}
        assert warn_if_removed_gemini_key_set(env) is True
        assert warn_if_removed_gemini_key_set(env) is False

    def test_silent_when_nothing_set(self) -> None:
        assert warn_if_removed_gemini_key_set({}) is False

    @pytest.mark.parametrize(
        "replacement",
        ["GFLOW_CLI_LLM_API_KEY", "GFLOW_CLI_LLM_BASE_URL", "GFLOW_CLI_LLM_MODEL"],
    )
    def test_silent_once_migrated(self, replacement: str) -> None:
        """Already migrated — nagging someone who has done the work is noise."""
        env = {"GFLOW_CLI_GEMINI_API_KEY": "AIza-old", replacement: "set"}
        assert warn_if_removed_gemini_key_set(env) is False

    def test_key_is_never_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The notice reports; it must not resurrect the value as a fallback."""
        monkeypatch.setenv("GFLOW_CLI_GEMINI_API_KEY", "AIza-old")
        monkeypatch.delenv("GFLOW_CLI_LLM_API_KEY", raising=False)
        assert Settings().llm_api_key is None
