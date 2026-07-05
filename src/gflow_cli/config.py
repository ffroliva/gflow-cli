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
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from gflow_cli import paths

_LEGACY_ENV_PREFIX = "FLOW_CLI_"
_NEW_ENV_PREFIX = "GFLOW_CLI_"


def _migrate_legacy_env() -> None:
    """Promote legacy `FLOW_CLI_*` env vars to `GFLOW_CLI_*` when the new
    var is unset. Emits a single `DeprecationWarning` summarizing the
    promoted keys. Removed in v0.5.0.
    """
    promoted: list[str] = []
    legacy_keys = [k for k in os.environ if k.startswith(_LEGACY_ENV_PREFIX)]
    for key in legacy_keys:
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


def _env_files() -> tuple[str, str]:
    """Dotenv files for :class:`Settings`, in pydantic-settings order (later wins).

    Implements the documented fallback (docs/CONFIGURATION.md): a CWD ``.env``
    takes precedence over ``$GFLOW_CLI_HOME/.env``. Home is resolved from the
    process env var (the ``home`` field default isn't computed yet at this
    point) falling back to :func:`gflow_cli.paths.default_home` — the same
    resolution the field itself uses.
    """
    home = os.environ.get(_NEW_ENV_PREFIX + "HOME")
    home_dir = Path(home) if home else paths.default_home()
    return (str(home_dir / ".env"), ".env")


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


class BrowserEngine(StrEnum):
    """Browser-automation engine backing the Playwright API.

    PLAYWRIGHT is the default and the only engine the standard install ships.
    PATCHRIGHT is an opt-in, drop-in patched Playwright (Chromium) that avoids
    the ``Runtime.enable`` CDP leak for stronger reCAPTCHA-Enterprise evasion on
    the headed path; it must be installed separately (``pip install patchright``)
    and is NOT a headless unlock.
    """

    PLAYWRIGHT = "playwright"
    PATCHRIGHT = "patchright"


class Settings(BaseSettings):
    """All gflow-cli configuration. Build via `Settings()` (or `get_settings()`)."""

    model_config = SettingsConfigDict(
        env_prefix="GFLOW_CLI_",
        # env_file is intentionally absent: dotenv files are resolved per
        # construction in `settings_customise_sources` so $GFLOW_CLI_HOME is
        # honored at call time (issue #240).
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Standard source order, with dotenv files resolved at call time.

        A static ``model_config["env_file"]`` tuple is evaluated once at class
        creation and cannot depend on the environment; this hook rebuilds the
        dotenv source per construction so ``$GFLOW_CLI_HOME/.env`` is found
        (docs/CONFIGURATION.md, issue #240).
        """
        dotenv = DotEnvSettingsSource(
            settings_cls,
            env_file=_env_files(),
            env_file_encoding="utf-8",
        )
        return (init_settings, env_settings, dotenv, file_secret_settings)

    # --- paths ------------------------------------------------------------
    home: Path = Field(
        default_factory=paths.default_home,
        description="Root for profiles, config.toml, etc.",
    )
    output_dir: Path = Field(
        default_factory=paths.default_output_dir,
        description="Where generated assets land (local storage).",
    )

    # --- cloud storage ----------------------------------------------------
    storage_uri: str | None = Field(
        default=None,
        description=(
            "Cloud storage URI prefix for generated assets. "
            "When set, generated asset files are uploaded to cloud storage instead "
            "of local disk. Supported schemes: gs:// (GCS), s3:// (S3/MinIO). "
            "Example: gs://my-bucket/gflow/  or  s3://my-bucket/gflow/ "
            "Requires gflow-cli[gcs] or gflow-cli[s3] extras. "
            "Override via GFLOW_CLI_STORAGE_URI."
        ),
    )

    @field_validator("storage_uri", mode="before")
    @classmethod
    def _validate_storage_uri(cls, v: object) -> object:
        if v is None or v == "":
            return None
        if not isinstance(v, str):
            msg = "storage_uri must be a string"
            raise ValueError(msg)
        allowed = ("gs://", "s3://", "memory://")
        if not any(v.startswith(scheme) for scheme in allowed):
            msg = (
                f"GFLOW_CLI_STORAGE_URI scheme not supported: {v!r}. "
                "Use gs:// (GCS), s3:// (S3/MinIO), or memory:// (tests only)."
            )
            raise ValueError(
                msg,
            )
        return v

    db_path: Path | None = Field(
        default=None,
        description=(
            "SQLite data-layer path. Defaults to <GFLOW_CLI_HOME>/gflow.db. "
            "Override with GFLOW_CLI_DB_PATH for tests or advanced local setups."
        ),
    )
    history_prompts: Literal["store", "redacted"] = Field(
        default="store",
        description=(
            "Controls prompt persistence in the local DB. 'store' stores prompt text; "
            "'redacted' stores only prompt_hash and prompt_redacted=1."
        ),
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
        description=(
            "Public Gemini API key. Enables prompt tools (--tool/-t) and, "
            "in future, provider=official. Override via GFLOW_CLI_GEMINI_API_KEY."
        ),
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description=(
            "Gemini model used for prompt tools (--tool/-t). Override via GFLOW_CLI_GEMINI_MODEL."
        ),
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
    auth_login_timeout: int = Field(
        default=600,
        ge=1,
        le=86400,
        description=(
            "Seconds to wait for the user to complete interactive sign-in. "
            "Applies to both Real Chrome (Passive Capture) and Internal Chromium strategies. "
            "Override via GFLOW_CLI_AUTH_LOGIN_TIMEOUT."
        ),
    )
    concurrency: int = Field(default=1, ge=1, le=16)
    headless: bool = Field(
        default=False,
        description=(
            "Run the Playwright Chromium headless. The ui_automation transport "
            "requires headed Chrome — reCAPTCHA Enterprise rejects headless "
            "browsers with an immediate 403. Only set True in CI/CD environments "
            "that use a different transport (e.g. bearer/sapisidhash)."
        ),
    )
    browser_engine: BrowserEngine = Field(
        default=BrowserEngine.PLAYWRIGHT,
        description=(
            "Browser automation engine: 'playwright' (default) or 'patchright'. "
            "Patchright is an OPT-IN, drop-in patched Playwright (Chromium) that "
            "avoids the Runtime.enable CDP leak for stronger reCAPTCHA-Enterprise "
            "evasion on the HEADED path. It must be installed separately "
            "(`pip install patchright`) and is NOT a headless unlock — the default "
            "stays playwright and is unaffected. Override via GFLOW_CLI_BROWSER_ENGINE."
        ),
    )
    prefer_classic: bool = Field(
        default=False,
        description=(
            "Try to force the Classic Flow UI mode for image generation by clicking "
            "the Agent toggle pill if the page mounts in Agentic mode. This ensures "
            "deterministic aspect ratio controls. "
            "If the page cannot be switched back to Classic, falls back to Agentic mode. "
            "Override via GFLOW_CLI_PREFER_CLASSIC."
        ),
    )
    daemon_token: str | None = Field(
        default=None,
        description="Auth token required when binding the serve daemon to a non-localhost address.",
    )

    # --- logging ----------------------------------------------------------
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.AUTO

    # --- daemon -----------------------------------------------------------
    daemon_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GFLOW_CLI_DAEMON_TOKEN", "GFLOW_DAEMON_TOKEN"),
        description="API token to authenticate calls to the daemon server.",
    )
    daemon_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("GFLOW_CLI_DAEMON_PORT", "GFLOW_DAEMON_PORT"),
        description="Port for the FastAPI daemon server. Default is 8000.",
    )

    # --- derived path helpers --------------------------------------------

    def resolved_db_path(self) -> Path:
        return self.db_path or paths.database_path(self.home)

    def profile_subdir(self, name: str) -> Path:
        return paths.profile_subdir(self.home, name)

    def config_file(self) -> Path:
        return paths.config_file(self.home)

    def user_tools_dir(self) -> Path:
        return paths.user_tools_dir(self.home)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Tests should call `reset_settings()`."""
    return Settings()


def reset_settings() -> None:
    """Clear the cache. Call between tests that munge env vars."""
    get_settings.cache_clear()
