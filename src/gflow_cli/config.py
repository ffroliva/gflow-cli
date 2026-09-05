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
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gflow_cli import paths

_LEGACY_ENV_PREFIX = "FLOW_CLI_"
_NEW_ENV_PREFIX = "GFLOW_CLI_"

#: Default prompt-tools endpoint — Google's OpenAI-compatible Gemini surface.
#: Chosen so a user who only sets ``GFLOW_CLI_LLM_API_KEY`` keeps working with a
#: plain Google key, while anyone pointing at another gateway overrides it.
DEFAULT_LLM_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

#: Hosts for which plain ``http://`` is accepted on ``llm_base_url``. Traffic to
#: these never leaves the machine, so there is no credential-in-cleartext risk.
#: Compared against ``urlsplit().hostname``, which unwraps IPv6 brackets — so
#: this is ``"::1"``, never ``"[::1]"``.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: Removed in v0.46.0 (issue #387). Read only to tell the user it is dead —
#: never forwarded. Without this the removal would be *silent*: the prompt tools
#: never raise, so an unmigrated user would simply stop getting rewritten
#: prompts while still spending full generation credits.
_REMOVED_GEMINI_KEY_ENV = "GFLOW_CLI_GEMINI_API_KEY"
_LLM_ENV_VARS = ("GFLOW_CLI_LLM_BASE_URL", "GFLOW_CLI_LLM_API_KEY", "GFLOW_CLI_LLM_MODEL")
_removed_gemini_key_notified = False


def warn_if_removed_gemini_key_set(env: Mapping[str, str] | None = None) -> bool:
    """Warn once if the removed Gemini key is set and nothing replaced it.

    Returns ``True`` when a notice was emitted, so callers (and tests) can tell
    the difference between "warned" and "already warned / nothing to warn about".
    """
    global _removed_gemini_key_notified
    source = os.environ if env is None else env
    if _removed_gemini_key_notified:
        return False
    if not source.get(_REMOVED_GEMINI_KEY_ENV):
        return False
    if any(source.get(name) for name in _LLM_ENV_VARS):
        return False
    _removed_gemini_key_notified = True
    warnings.warn(
        f"{_REMOVED_GEMINI_KEY_ENV} is no longer read and is NOT forwarded. "
        "The prompt tools now use any OpenAI-compatible endpoint: set "
        "GFLOW_CLI_LLM_API_KEY (your existing Google key still works against the "
        "default endpoint), and optionally GFLOW_CLI_LLM_BASE_URL / "
        "GFLOW_CLI_LLM_MODEL. Until you do, --tool silently leaves prompts "
        "unchanged. See docs/CONFIGURATION.md.",
        DeprecationWarning,
        stacklevel=2,
    )
    return True


def reset_removed_gemini_key_notice() -> None:
    """Test seam — clears the warn-once latch (mirrors :func:`reset_settings`)."""
    global _removed_gemini_key_notified
    _removed_gemini_key_notified = False


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
    ``classic`` recovers the classic composer and, if the arm is still agentic,
    fails fast (``UiModeUnavailableError``, exit 28) BEFORE submitting — zero
    credits. ``agentic`` switches to (and requires) the agentic surface.
    Subsumes the deprecated ``prefer_classic`` / ``force_agent_ui``.
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
    drop.

    Without instructions, ``auto`` resolves to ``classic`` (#595): ``auto``
    means "no arm was asked for", and classic is the arm that can satisfy a
    media request. Binding whatever rendered put an account in Flow's agentic
    cohort on a driver that cannot — failing mid-run as ``image_mode_tab``
    selector drift or a ``WireFormatError`` about video bytes, neither of which
    names the cause. The bind's classic recovery still runs first, and the
    cohort flaps per load, so this only aborts (exit 28, pre-submit, $0) when
    the arm is genuinely pinned. Agentic stays reachable by name
    (``--ui-mode agentic``) or by needing it (``-i``). An explicit ``classic``
    or ``agentic`` passes through unchanged.
    """
    if not has_instructions:
        return UiMode.CLASSIC if base is UiMode.AUTO else base
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

    # --- prompt-tools LLM (provider-agnostic, OpenAI Chat Completions) -----
    llm_base_url: str = Field(
        default=DEFAULT_LLM_BASE_URL,
        description=(
            "Base URL of an OpenAI-compatible Chat Completions endpoint used by the "
            "prompt tools (--tool/-t). Works with any compliant provider or gateway "
            "(OpenRouter, a self-hosted proxy, Ollama, ...). Defaults to Google's "
            "OpenAI-compatible Gemini endpoint. Override via GFLOW_CLI_LLM_BASE_URL."
        ),
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Credential presented to GFLOW_CLI_LLM_BASE_URL as an Authorization: Bearer "
            "header. Optional — a keyless local gateway needs none, and the header is "
            "then omitted entirely. Provider keys stay with the gateway, never here. "
            "Override via GFLOW_CLI_LLM_API_KEY."
        ),
    )
    llm_model: str | None = Field(
        default=None,
        description=(
            "Model used by the prompt tools. Also the provider selector, since "
            "gateways route on the model string. Unset = let the gateway choose. "
            "A tool's TOML config.model pin takes precedence. "
            "Override via GFLOW_CLI_LLM_MODEL."
        ),
    )

    @field_validator("llm_base_url", mode="before")
    @classmethod
    def _validate_llm_base_url(cls, v: object) -> object:
        """Reject URLs this client must not be pointed at.

        ``base_url`` is user-supplied and feeds ``urllib.request.urlopen``, so it
        is a trust boundary: without this, ``file://`` and friends become
        reachable and a plaintext ``http://`` host would receive the API key in
        the clear. Plain http is allowed *only* for loopback, because a local
        gateway is a first-class use case for this feature.
        """
        if v is None or v == "":
            return DEFAULT_LLM_BASE_URL
        if not isinstance(v, str):
            msg = "llm_base_url must be a string"
            raise ValueError(msg)

        parsed = urlsplit(v)
        if parsed.scheme not in ("http", "https"):
            msg = (
                f"GFLOW_CLI_LLM_BASE_URL scheme not supported: {v!r}. "
                "Use https:// (or http:// for a loopback address)."
            )
            raise ValueError(msg)
        if "@" in parsed.netloc:
            msg = (
                "GFLOW_CLI_LLM_BASE_URL must not embed credentials in the URL. "
                "Pass the credential via GFLOW_CLI_LLM_API_KEY instead."
            )
            raise ValueError(msg)
        if parsed.scheme == "http" and (parsed.hostname or "") not in _LOOPBACK_HOSTS:
            msg = (
                f"GFLOW_CLI_LLM_BASE_URL must use https for a non-loopback host: {v!r}. "
                "Plain http would send GFLOW_CLI_LLM_API_KEY in cleartext."
            )
            raise ValueError(msg)
        return v

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
    update_check: bool = Field(
        default=True,
        description=(
            "Once-a-day best-effort PyPI check that prints a one-line notice "
            "when a newer gflow-cli exists (#479). Never blocks or fails a "
            "command; skipped in CI and for editable/source installs. "
            "Set GFLOW_CLI_UPDATE_CHECK=0 to disable."
        ),
    )
    lease_wait_seconds: float = Field(
        default=0.0,
        ge=0.0,
        le=3600.0,
        description=(
            "Seconds to wait for another gflow process to release the profile "
            "lease before giving up with ProfileLockedError (0 = fail fast, "
            "the default). Holders always run to completion — the waiter simply "
            "takes over once the current command or daemon task releases (#478). "
            "Same-process contention never waits (it would deadlock). "
            "Override via GFLOW_CLI_LEASE_WAIT_SECONDS."
        ),
    )
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
    flow_host: Literal["auto", "flow.google.com", "labs.google"] = Field(
        default="auto",
        description=(
            "Which Flow frontend gflow drives. 'auto' (default): flow.google.com is the "
            "default host for every request it can serve today (text-to-video in an "
            "existing project), on moved and unmoved accounts alike; requests it cannot "
            "serve yet keep the labs driver on an unmoved account. 'flow.google.com': force "
            "the migrated composer for everything. 'labs.google': never use it — a moved "
            "account fails with exit 36 (kill switch). Override via GFLOW_CLI_FLOW_HOST."
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

    # --- debugging ---------------------------------------------------------
    incident_capture: bool = Field(
        default=True,
        description=(
            "Automatically write a private incident bundle under "
            "<GFLOW_CLI_HOME>/incidents/ on relevant operational failures "
            "(Flow app crash, UI drift, transport timeout, WAF/network errors, "
            "profile-lock contention). Bundles contain structural metadata only; "
            "screenshots live under sensitive/ and must be reviewed before "
            "sharing. Nothing is ever uploaded. Set false to disable. "
            "Override via GFLOW_CLI_INCIDENT_CAPTURE."
        ),
    )
    har_path: Path | None = Field(
        default=None,
        description=(
            "When set, captures full Playwright network traffic (requests, "
            "responses, headers, cookies) to this HAR file for the session. "
            "SECURITY: HAR files can contain auth cookies and bearer tokens — "
            "never share one publicly; the file is chmod 0o600 on POSIX. "
            "Concurrent gflow processes pointed at the same path will overwrite "
            "each other's HAR (last-writer-wins) — use a distinct path per run. "
            "Override via GFLOW_CLI_HAR_PATH."
        ),
    )
    debug_traceback: bool = Field(
        default=False,
        description=(
            "Prints the real message + traceback for unhandled (non-GFlowError) "
            "exceptions to the console and, under --json, into the payload's "
            "error.traceback field, instead of the generic placeholder. The "
            "structured telemetry event stays hashed either way — this only "
            "affects what the operator/caller sees. SECURITY: may leak "
            "tokens/cookies present in exception text — for local debugging "
            "only. Never pipe --json output under this flag to a "
            "shared/persistent system (CI logs, log aggregators, webhooks) "
            "without redacting it first. Override via GFLOW_CLI_DEBUG_TRACEBACK."
        ),
    )

    # --- logging ----------------------------------------------------------
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.AUTO

    # --- daemon -----------------------------------------------------------
    daemon_token: SecretStr | None = Field(
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
