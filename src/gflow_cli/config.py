"""Process-wide configuration via `pydantic-settings`.

All knobs are env-var-driven (prefix `GFLOW_CLI_`), with a `.env` fallback
loaded from CWD or `$GFLOW_CLI_HOME/.env`. Validated at startup; bad values
fail loudly with the offending key + the rule it violated.

Legacy `FLOW_CLI_*` env vars are still honored in v0.4.x via the
`_migrate_legacy_env` shim below, which emits a `DeprecationWarning`.
Legacy support will be removed in v0.5.0.

Resolution precedence (highest first):
    1. CLI flag (passed at call site, not here)
    2. Environment variable
    3. `.env` file (CWD wins over $GFLOW_CLI_HOME/.env)
    4. Built-in default (from `gflow_cli.paths`)

Use `get_settings()` to access the cached singleton. Tests should call
`reset_settings()` between cases.
"""

from __future__ import annotations

import os
import warnings
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from gflow_cli import paths

_LEGACY_ENV_PREFIX = "FLOW_CLI_"
_NEW_ENV_PREFIX = "GFLOW_CLI_"


def _migrate_legacy_env() -> None:
    """Promote legacy `FLOW_CLI_*` env vars to `GFLOW_CLI_*` when the new
    var is unset. Emits a single `DeprecationWarning` summarizing the
    promoted keys. Removed in v0.5.0.
    """
    promoted: list[str] = []
    for key in list(os.environ):
        if not key.startswith(_LEGACY_ENV_PREFIX):
            continue
        new_key = _NEW_ENV_PREFIX + key[len(_LEGACY_ENV_PREFIX) :]
        if new_key not in os.environ:
            os.environ[new_key] = os.environ[key]
            promoted.append(key)
    if promoted:
        warnings.warn(
            f"Legacy env vars promoted to GFLOW_CLI_* prefix: {sorted(promoted)}. "
            "Update your .env / shell exports — legacy support is removed in v0.5.0.",
            DeprecationWarning,
            stacklevel=2,
        )


_migrate_legacy_env()


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogFormat(StrEnum):
    AUTO = "auto"
    TEXT = "text"
    JSON = "json"


class Provider(StrEnum):
    FLOW = "flow"
    OFFICIAL = "official"  # planned v0.3+ via googleapis/python-genai


class Settings(BaseSettings):
    """All gflow-cli configuration. Build via `Settings()` (or `get_settings()`)."""

    model_config = SettingsConfigDict(
        env_prefix="GFLOW_CLI_",
        env_file=(".env",),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- paths ------------------------------------------------------------
    home: Path = Field(
        default_factory=paths.default_home,
        description="Root for profiles, config.toml, etc.",
    )
    output_dir: Path = Field(
        default_factory=paths.default_output_dir,
        description="Where generated assets land.",
    )

    # --- profile ----------------------------------------------------------
    profile: str | None = Field(
        default=None,
        description=(
            "Default profile name. None = resolve from config.toml or "
            "auto-pick the only profile present."
        ),
    )

    # --- provider ---------------------------------------------------------
    provider: Provider = Provider.FLOW
    gemini_api_key: str | None = Field(
        default=None,
        description="Required when provider=official (v0.3+).",
    )

    # --- transport --------------------------------------------------------
    transport: str | None = Field(
        default=None,
        description=(
            "Default transport strategy: ui_automation (production-validated; default). "
            "Set GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1 to expose evaluate_fetch / bearer / "
            "sapisidhash in the --transport CLI Choice list. The Python API accepts any "
            "registered key regardless of that env var. Override via GFLOW_CLI_TRANSPORT "
            "env var or --transport."
        ),
    )

    # --- runtime ----------------------------------------------------------
    timeout_seconds: int = Field(default=600, ge=1, le=3600)
    concurrency: int = Field(default=1, ge=1, le=16)
    headless: bool = Field(
        default=True,
        description=(
            "Run the Playwright Chromium headless. Set to False if reCAPTCHA "
            "fails to mint tokens (Google sometimes detects headless)."
        ),
    )

    # --- logging ----------------------------------------------------------
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.AUTO

    # --- derived path helpers --------------------------------------------

    def profile_subdir(self, name: str) -> Path:
        return paths.profile_subdir(self.home, name)

    def config_file(self) -> Path:
        return paths.config_file(self.home)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Tests should call `reset_settings()`."""
    return Settings()


def reset_settings() -> None:
    """Clear the cache. Call between tests that munge env vars."""
    get_settings.cache_clear()
