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

import math
import os
import warnings
from collections.abc import Mapping
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from dotenv import dotenv_values
from pydantic import AliasChoices, Field, field_validator
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


_HOME_KEY = (_NEW_ENV_PREFIX + "HOME").lower()  # matched case-insensitively


def _home_key_value(mapping: Mapping[str, str | None]) -> Path | None:
    """Non-empty ``GFLOW_CLI_HOME`` from ``mapping``, matched case-insensitively
    (mirrors the env source's ``case_sensitive=False``); empty string = unset.
    """
    for key, value in mapping.items():
        if key.lower() == _HOME_KEY and value and value.strip():
            return Path(value)
    return None


def _resolve_home() -> Path:
    """The home directory, resolved BEFORE Settings exists (chicken-and-egg).

    Mirrors the ``home`` field's own precedence: process env var, then the CWD
    ``.env`` (the only dotenv file locatable without knowing home), then the
    platform default. By construction the home ``.env`` cannot relocate home —
    that would be circular; set the env var or the CWD ``.env`` instead.
    """
    from_env = _home_key_value(os.environ)
    if from_env is not None:
        return from_env
    try:
        from_cwd_env = _home_key_value(dotenv_values(".env"))
    except OSError:
        from_cwd_env = None
    if from_cwd_env is not None:
        return from_cwd_env
    return paths.default_home()


def _env_files() -> tuple[str, str]:
    """Dotenv files for :class:`Settings`, in pydantic-settings order (later wins):
    a CWD ``.env`` takes precedence over ``<home>/.env`` (docs/CONFIGURATION.md).
    """
    return (str(_resolve_home() / ".env"), ".env")


# Ceiling for a jitter bound (seconds). Anything above this is almost
# certainly a fat-finger (milliseconds pasted as seconds) or 'inf'.
MAX_JITTER_SECONDS = 3600.0


def parse_jitter_range(spec: str) -> tuple[float, float]:
    """Parse a jitter spec: ``MIN-MAX`` seconds, a single ``N`` (uniform 0-N),
    or ``0`` to disable. Raises ``ValueError`` with a user-facing message on an
    unparseable spec, negative/non-finite values, MIN > MAX, or a bound above
    ``MAX_JITTER_SECONDS``.
    """
    parts = spec.split("-")
    try:
        if len(parts) == 1:
            low, high = 0.0, float(parts[0])
        elif len(parts) == 2:
            low, high = float(parts[0]), float(parts[1])
        else:
            raise ValueError(spec)
    except ValueError:
        msg = f"Invalid jitter spec {spec!r}: expected 'MIN-MAX' or a single number (seconds)."
        raise ValueError(msg) from None
    if not (math.isfinite(low) and math.isfinite(high)):
        msg = f"Invalid jitter range {spec!r}: bounds must be finite seconds."
        raise ValueError(msg)
    if low > high:
        msg = f"Invalid jitter range {spec!r}: MIN ({low}) must be <= MAX ({high})."
        raise ValueError(msg)
    if high > MAX_JITTER_SECONDS:
        msg = (
            f"Invalid jitter range {spec!r}: MAX ({high}) exceeds "
            f"{MAX_JITTER_SECONDS:.0f}s — jitter is expressed in seconds."
        )
        raise ValueError(msg)
    return (low, high)


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


class UiMode(StrEnum):
    """Requested Flow UI arm for generation commands (issue #299).

    ``auto`` binds whatever the composer renders (classic or agentic).
    ``classic`` attempts to recover the classic composer and, if the arm is
    still agentic, fails fast (``ClassicUiUnavailableError``, exit 28) BEFORE
    submitting — zero credits. ``agentic`` skips the classic-recovery attempt
    and binds the agentic driver. Subsumes the deprecated ``prefer_classic``.
    """

    AUTO = "auto"
    CLASSIC = "classic"
    AGENTIC = "agentic"


def resolve_ui_mode(cli_value: str | None = None) -> UiMode:
    """Resolve the effective UI mode.

    Precedence: explicit ``cli_value`` (the ``--ui-mode`` flag) > the
    ``GFLOW_CLI_UI_MODE`` setting > the deprecated ``GFLOW_CLI_PREFER_CLASSIC``
    (True → ``classic``, with a one-time deprecation warning) > ``auto``.
    """
    if cli_value is not None:
        return UiMode(cli_value.lower())
    settings = get_settings()
    if settings.ui_mode is not None:
        return settings.ui_mode
    if settings.prefer_classic:
        warnings.warn(
            "GFLOW_CLI_PREFER_CLASSIC is deprecated; use GFLOW_CLI_UI_MODE=classic "
            "(or --ui-mode classic). Note the behavior change: the classic arm is now "
            "required — a run aborts with exit 28 instead of silently falling back to "
            "the agentic UI.",
            DeprecationWarning,
            stacklevel=2,
        )
        return UiMode.CLASSIC
    if settings.force_agent_ui:
        warnings.warn(
            "GFLOW_CLI_FORCE_AGENT_UI is deprecated; use GFLOW_CLI_UI_MODE=agentic "
            "(or --ui-mode agentic).",
            DeprecationWarning,
            stacklevel=2,
        )
        return UiMode.AGENTIC
    return UiMode.AUTO


def infer_required_ui_mode(base: UiMode, *, has_instructions: bool) -> UiMode:
    """Resolve the arm a command actually REQUIRES from its explicit mode + needs.

    Agent instructions (``-i``) are an agentic-only surface, so they force
    agentic when the caller didn't already ask for a specific arm. Explicitly
    demanding ``classic`` *and* passing instructions is contradictory (classic
    cannot apply cards) — a hard :class:`ConfigurationError` instead of a silent
    drop. Without instructions, ``base`` passes through unchanged.
    """
    if not has_instructions:
        return base
    if base is UiMode.CLASSIC:
        from gflow_cli.errors import ConfigurationError

        msg = (
            "Agent instructions (-i) require the agentic Flow UI, which is "
            "incompatible with --ui-mode classic. Drop --ui-mode classic (or "
            "GFLOW_CLI_UI_MODE=classic), or remove the -i instructions."
        )
        raise ConfigurationError(msg)
    return UiMode.AGENTIC


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
        # env_file is intentionally absent: the values in a static tuple here
        # could not depend on the runtime environment, so __init__ below
        # defaults `_env_file` to `_env_files()` per construction instead
        # (issue #240). Re-adding env_file here would be silently ignored.
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    if not TYPE_CHECKING:
        # Runtime-only: defaulting the documented `_env_file` init kwarg keeps
        # `Settings(_env_file=...)` / `_env_file=None` working exactly as in
        # stock pydantic-settings. Hidden from type checkers so pyright keeps
        # the synthesized field-typed constructor.
        def __init__(self, **values: Any) -> None:
            values.setdefault("_env_file", _env_files())
            super().__init__(**values)

    # --- paths ------------------------------------------------------------
    home: Path = Field(
        default_factory=paths.default_home,
        description="Root for profiles, config.toml, etc.",
    )

    @field_validator("home", mode="before")
    @classmethod
    def _empty_home_means_unset(cls, value: object) -> object:
        """`GFLOW_CLI_HOME=` (set-but-empty, common in CI templates) means
        "unset", not `Path('.')` — keeping the field coherent with
        `_resolve_home()`, which treats empty the same way."""
        if isinstance(value, str) and not value.strip():
            return paths.default_home()
        return value

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
    ui_mode: UiMode | None = Field(
        default=None,
        description=(
            "Requested Flow UI arm: 'auto' (bind whatever renders), 'classic' "
            "(require the classic composer; abort with exit 28 if the arm is "
            "agentic — no credits spent), or 'agentic' (skip classic recovery). "
            "Unset resolves via --ui-mode, then the deprecated "
            "GFLOW_CLI_PREFER_CLASSIC, then 'auto'. Override via GFLOW_CLI_UI_MODE."
        ),
    )
    prefer_classic: bool = Field(
        default=False,
        description=(
            "DEPRECATED — use GFLOW_CLI_UI_MODE=classic (or --ui-mode classic). "
            "True maps to ui_mode=classic, but note the behavior change: the "
            "classic arm is now REQUIRED (abort with exit 28) instead of silently "
            "falling back to the agentic UI. Override via GFLOW_CLI_PREFER_CLASSIC."
        ),
    )
    force_agent_ui: bool = Field(
        default=False,
        description=(
            "DEPRECATED — use GFLOW_CLI_UI_MODE=agentic (or --ui-mode agentic). "
            "True maps to ui_mode=agentic. Override via GFLOW_CLI_FORCE_AGENT_UI."
        ),
    )
    jitter_range: str | None = Field(
        default=None,
        description=(
            "Anti-bot pause between prompt submissions in multi-prompt image "
            "runs: 'MIN-MAX' seconds, a single number for 0-N, or 0 to disable. "
            "Unset means the built-in small default (0.5-1.5s). "
            "Override per-run with --jitter."
        ),
    )

    @field_validator("jitter_range")
    @classmethod
    def _jitter_range_must_parse(cls, value: str | None) -> str | None:
        """Fail at settings load (clean pydantic error) instead of mid-run."""
        if value is None or not value.strip():
            # Set-but-empty (common in CI templates) means "unset".
            return None
        parse_jitter_range(value)
        return value

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
