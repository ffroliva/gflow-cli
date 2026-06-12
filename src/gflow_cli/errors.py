from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

__all__ = [
    "EXIT_CODE_MAP",
    "AisandboxAuthError",
    "AuthExpiredError",
    "AuthLoginTimeoutError",
    "AuthMissingError",
    "BatchIntegrityError",
    "BatchPartialError",
    "BrowserEngineUnavailableError",
    "ChainManifestError",
    "ChainPartialError",
    "ConfigurationError",
    "ContentPolicyError",
    "DataIntegrityError",
    "DataMigrationError",
    "DataStoreError",
    "FlowApiError",
    "FrameExtractionError",
    "GFlowError",
    "ModelModeIncompatibilityError",
    "NetworkError",
    "ProblemDetails",
    "RateLimitError",
    "SceneConcatError",
    "SecurityError",
    "TransportTimeoutError",
    "UiSelectorDriftError",
    "UpscaleUnavailableError",
    "VideoModelSelectionError",
    "WafRejectionError",
    "WireFormatError",
]


class ProblemDetails(TypedDict, total=False):
    """RFC 9457 Problem Details JSON shape (https://datatracker.ietf.org/doc/html/rfc9457).
    Two gflow extensions: `remediation_hint` and `route`."""

    type: str  # required
    title: str  # required
    status: int  # optional — only the literal HTTP status of the failed call
    detail: str  # optional
    instance: str  # optional — `gflow:error:<correlation_id>`
    remediation_hint: str  # gflow extension
    route: str  # gflow extension — sanitized route name, NOT full URL


class GFlowError(Exception):
    """Base class for all gflow domain errors. Library-wide root.

    Field shape: RFC 9457 Problem Details. Class-level (`problem_type`,
    `title`, `_default_remediation`) define stable identity per class.
    Instance-level (`detail`, `status`, `instance`, `remediation_hint`,
    `route`) populated per raise.

    `instance` is `gflow:error:<correlation_id>` (per-occurrence ID), NOT
    the failed route URL — RFC 9457 §3.5 says `instance` identifies the
    occurrence, not the endpoint. Route name lives in the `route` extension.
    """

    problem_type: str = "about:blank"
    title: str = "Error"
    _default_remediation: str = ""

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        route: str = "",
        remediation_hint: str | None = None,
    ) -> None:
        message = self.title if not detail else f"{self.title}: {detail}"
        super().__init__(message)
        self.detail = detail
        self.status = status
        self.instance = instance or ""
        self.route = route
        self.remediation_hint = (
            remediation_hint if remediation_hint is not None else self._default_remediation
        )

    def to_problem_details(self) -> ProblemDetails:
        out: ProblemDetails = {
            "type": self.problem_type,
            "title": self.title,
        }
        if self.status is not None:
            out["status"] = self.status
        if self.detail:
            out["detail"] = self.detail
        if self.instance:
            out["instance"] = self.instance
        if self.remediation_hint:
            out["remediation_hint"] = self.remediation_hint
        if self.route:
            out["route"] = self.route
        return out


class FlowApiError(GFlowError):
    """Parent of all API-related errors. Retained as a named parent class so
    `except FlowApiError` continues to catch typed subclasses below.

    Constructor accepts BOTH the legacy 3-arg form (Phase 3 callers) AND
    the new GFlowError-style kwargs.

    Legacy form: ``FlowApiError(status: int, body: str, *, route: str = "")``
    The ``body`` argument MUST be pre-redacted via ``_redact_for_log`` before
    construction (mandate per security review). It is truncated to 200
    chars and incorporated into ``detail``.
    """

    problem_type = "https://gflow-cli.dev/errors/api-error"
    title = "Flow API error"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # `bool` is a subclass of `int`, so `isinstance(True, int)` is True.
        # Exclude bools explicitly so an accidental FlowApiError(True, ...) takes
        # the new-style path (and surfaces as a TypeError downstream) instead of
        # silently being treated as legacy with status=1.
        if args and isinstance(args[0], int) and not isinstance(args[0], bool):
            status = args[0]
            body = args[1] if len(args) > 1 else ""
            route_kw = kwargs.pop("route", "")
            super().__init__(
                f"HTTP {status}: {body[:200]}",
                status=status,
                instance=kwargs.pop("instance", None),
                route=route_kw,
                remediation_hint=kwargs.pop("remediation_hint", None),
            )
            self.body = body
        else:
            super().__init__(*args, **kwargs)
            self.body = ""


class AuthExpiredError(FlowApiError):
    problem_type = "https://gflow-cli.dev/errors/auth-expired"
    title = "Authentication expired"
    _default_remediation = "Run `gflow auth login --profile <name>` to refresh the session."


class AisandboxAuthError(AuthExpiredError):
    """aisandbox-pa REST returned 401 even after a fresh SAPISIDHASH.

    Distinct from the generic AuthExpiredError so callers (and the scene
    feature) can catch the aisandbox-specific auth failure, while still
    mapping to exit code 3 via the EXIT_CODE_MAP isinstance walk (no own
    entry needed — it inherits AuthExpiredError's code).
    """

    problem_type = "https://gflow-cli.dev/errors/aisandbox-auth"
    title = "aisandbox-pa authentication failed"
    _default_remediation = (
        "SAPISID cookie missing, expired, or unreadable. "
        "Re-run `gflow auth login --profile <name>` and retry."
    )


class RateLimitError(FlowApiError):
    problem_type = "https://gflow-cli.dev/errors/rate-limit"
    title = "Rate limit or quota hit"
    _default_remediation = "Wait a few minutes; reduce GFLOW_CLI_CONCURRENCY if persistent."

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        route: str = "",
        remediation_hint: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(
            detail,
            status=status,
            instance=instance,
            route=route,
            remediation_hint=remediation_hint,
        )
        self.retry_after = retry_after


class ContentPolicyError(FlowApiError):
    """Flow returned 200 with empty media[]. ``status`` is intentionally
    omitted from to_problem_details() per RFC 9457 — ``status`` is the HTTP
    status of the problem, and 200 conflates with success. The literal
    upstream status (200) is recorded only via the ``error_raised`` log event
    as an ``upstream_status`` extension (see observability.py).

    Enforcement is at the class level (overrides to_problem_details) — relying
    on callers to omit ``status=`` would silently break the RFC 9457 contract
    the first time someone added it for symmetry with other error classes.
    """

    problem_type = "https://gflow-cli.dev/errors/content-policy"
    title = "Content policy rejection"
    _default_remediation = (
        "Flow rejected the prompt under its content policy. "
        "Soften wording or remove disallowed elements."
    )

    def to_problem_details(self) -> ProblemDetails:
        pd = super().to_problem_details()
        # RFC 9457 contract: an error must not carry a 2xx status.
        pd.pop("status", None)
        return pd


class NetworkError(FlowApiError):
    problem_type = "https://gflow-cli.dev/errors/network"
    title = "Network failure persisted across retries"
    _default_remediation = "Check connectivity and try again."


class WireFormatError(FlowApiError):
    """Carries discovery fields so ``grep error_class=WireFormatError`` in
    structured logs reveals what was unexpected, enabling new error class
    proposals. Discovery payload set at raise site via the ``discovery=`` kwarg."""

    problem_type = "https://gflow-cli.dev/errors/wire-format"
    title = "Unexpected response shape from Flow"
    _default_remediation = (
        "File a bug at https://github.com/ffroliva/gflow-cli/issues "
        "(do NOT include captured tokens or signed URLs)."
    )

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        route: str = "",
        remediation_hint: str | None = None,
        discovery: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            detail,
            status=status,
            instance=instance,
            route=route,
            remediation_hint=remediation_hint,
        )
        self.discovery = discovery or {}


class SceneConcatError(FlowApiError):
    """Raised when Flow's server-side scene concatenation job FAILS.

    Distinct from a poll timeout (which raises ``TransportTimeoutError``, exit 9):
    this is a terminal ``MEDIA_GENERATION_STATUS_FAILED`` / unexpected status from
    ``runVideoFxCheckConcatenationStatus`` (or an undecodable / non-MP4 payload).
    The error detail is built from the ``status`` ONLY — never the ~20MB inline
    ``encodedVideo``.
    """

    problem_type = "https://gflow-cli.dev/errors/scene-concat"
    title = "Scene concatenation failed"
    _default_remediation = (
        "Flow's server-side concatenation job did not succeed. Retry the "
        "compose; if it persists, check the clips are valid video workflows."
    )


class TransportTimeoutError(GFlowError):
    """Raised when a transport strategy hangs > 30s on a single API call."""

    problem_type = "https://gflow-cli.dev/errors/transport-timeout"
    title = "Transport strategy timed out"
    _default_remediation = (
        "A single API call exceeded the 30 s deadline. "
        "Check connectivity or reduce request complexity."
    )


class WafRejectionError(GFlowError):
    """Raised on HTTP 403 from Flow API; likely WAF / browser-fingerprint mismatch."""

    problem_type = "https://gflow-cli.dev/errors/waf-rejection"
    title = "WAF rejection (HTTP 403)"
    _default_remediation = (
        "Flow returned 403; the request was blocked by a WAF or fingerprint check. "
        "Re-authenticate or rotate the transport profile."
    )


class ConfigurationError(GFlowError):
    """Raised when configuration is invalid (e.g. unknown transport name)."""

    problem_type = "https://gflow-cli.dev/errors/configuration"
    title = "Configuration error"
    _default_remediation = (
        "Check that the transport name is registered via make_transport(). "
        "Run `gflow config list-transports` to see available strategies."
    )


class BrowserEngineUnavailableError(ConfigurationError):
    """Raised when GFLOW_CLI_BROWSER_ENGINE selects an engine that is unavailable.

    Two causes: the optional ``patchright`` package is not installed, or its
    browser driver is missing. Caught at the engine-resolver seam and re-raised
    here (never a raw ``ImportError``, which would be SHA-hashed to a generic
    exit 1). Distinct exit code 24 (not ConfigurationError's 11) so scripted
    callers can branch on "install the engine" versus a generic config mistake.
    The remediation hint differs per cause and is supplied at the raise site.
    """

    problem_type = "https://gflow-cli.dev/errors/browser-engine-unavailable"
    title = "Selected browser engine is unavailable"
    _default_remediation = (
        "The selected browser engine is unavailable. Install it with "
        "`pip install patchright`, or unset GFLOW_CLI_BROWSER_ENGINE to use the "
        "default playwright engine."
    )


class ModelModeIncompatibilityError(ConfigurationError):
    """Raised when the chosen video model is incompatible with the requested
    generation mode (issue #125).

    The canonical case: ``omni-flash`` does NOT support i2v interpolation
    (start or start+end frames). Flow's frontend silently drops the frame
    refs at submit time and routes the request to the T2V endpoint,
    charging a credit for a generation that ignores the supplied images
    entirely. This error is raised pre-submit by both the CLI (Click
    callback) and the transport (defense-in-depth for direct
    ``FlowApiClient`` callers that bypass the CLI), preventing the
    silent credit-burn.

    Distinct exit code 17 (not Click's exit 2, not generic exit 1) so
    scripted callers can branch on "I picked an incompatible
    model/mode" without parsing stderr.
    """

    problem_type = "https://gflow-cli.dev/errors/model-mode-incompatibility"
    title = "Model is incompatible with the requested generation mode"
    _default_remediation = (
        "The selected video model does not support this generation mode. "
        "For i2v with a start or end frame, use --model veo-lite (or "
        "veo-fast / veo-quality / veo-lite-lp). See issue #125."
    )


class VideoModelSelectionError(ConfigurationError):
    """Raised when the requested video model could not be selected in Flow's UI
    for an i2v generation (issue #125).

    The model picker option was not found (e.g. a selector drift / render race).
    For i2v this is FATAL rather than a silent fallback: leaving Flow on its
    default model (``omni-flash``) would drop the start/end frames and route to
    T2V, charging a credit for a text-only video. Raised pre-submit by the
    transport, so no credit is spent.

    Distinct exit code 18 so scripted callers can branch on "the model UI failed"
    (a retryable transport/selector issue) versus exit 17 "I picked an
    incompatible model" (a request mistake).
    """

    problem_type = "https://gflow-cli.dev/errors/video-model-selection"
    title = "Could not select the requested video model"
    _default_remediation = (
        "gflow could not select the requested model in Flow's editor (the model "
        "picker option was not found). This is usually transient — retry the "
        "command. If it persists, Flow's model-picker UI may have changed; please "
        "report it referencing issue #125."
    )


class UpscaleUnavailableError(GFlowError):
    """Raised when an image upscale target resolution is unavailable for the account
    (issue #171).

    The canonical case: a non-Ultra (e.g. Pro) account requests ``--scale 4k``.
    Flow's ``upsampleImage`` endpoint returns HTTP 403 for the tier gate, which is
    indistinguishable on the wire from a WAF/fingerprint 403. The transport
    disambiguates by context (the request was a 4K upscale, the session is valid,
    reCAPTCHA was accepted) and raises THIS error rather than ``WafRejectionError``.

    Distinct exit code 22 (not WAF's 10) so scripted callers can branch on
    "upgrade your subscription" versus "the request was blocked / rotate profile".
    The caller MUST NOT auto-retry a tier 403 — retrying only inflates per-profile
    WAF heat without ever succeeding.
    """

    problem_type = "https://gflow-cli.dev/errors/upscale-unavailable"
    title = "Image upscale unavailable for this account"
    _default_remediation = (
        "This upscale resolution is not available on your account. 4K upscaling "
        "requires a Flow Ultra subscription — use --scale 2k, or upgrade your plan. "
        "If you just upgraded, re-run `gflow auth login --profile <name>` to refresh "
        "the session."
    )


class UiSelectorDriftError(GFlowError):
    """Raised when a UI-automation selector cascade finds no matching element.

    Indicates that Flow's frontend has changed in a way that invalidates one
    of the selector probes (mode-switch trigger, mode tab, sub-mode tab, etc.).
    The ``detail`` names the probe label and includes the debug screenshot path
    when one was captured.

    This is a hard failure — gflow cannot safely proceed without the control —
    but it is *diagnosed*, not opaque: the user gets the probe name and the
    screenshot for inspection.  Exit code 23 lets scripted callers branch on
    "the UI changed and needs a selector update" versus generic error (1).
    """

    problem_type = "https://gflow-cli.dev/errors/ui-selector-drift"
    title = "Flow UI selector drift"
    _default_remediation = (
        "A Flow editor UI element could not be located — Google may have updated "
        "their frontend. Check for a newer gflow-cli release, then file a bug at "
        "https://github.com/ffroliva/gflow-cli/issues referencing the probe name "
        "and attaching the debug screenshot from this message, if one was captured "
        "(review it first — the viewport may show your account name/avatar; do NOT "
        "include tokens or signed URLs)."
    )


class SecurityError(GFlowError):
    """Raised when a security boundary is violated (e.g. profile_dir outside HOME)."""

    problem_type = "https://gflow-cli.dev/errors/security"
    title = "Security violation"
    _default_remediation = "Ensure all file paths are within the allowed GFLOW_CLI_HOME directory."


class AuthMissingError(GFlowError):
    """Raised when a profile lacks a usable session for the requested action.

    Covers both a wholly absent session and the issue-#15 case: a profile
    signed in to Google but not to the Flow app (no NextAuth session). The
    raising site supplies a message and `remediation_hint` describing which.
    """

    problem_type = "https://gflow-cli.dev/errors/auth-missing"
    title = "Authentication credential missing"
    _default_remediation = (
        "No usable Flow session was found in the profile. "
        "Run `gflow auth login --profile <name>` and complete the Flow sign-in."
    )


class AuthLoginTimeoutError(GFlowError):
    """Raised when the interactive login polling loop exceeds its deadline.

    Distinct from TransportTimeoutError (which covers API call timeouts).
    This error means the user/agent did not complete the sign-in flow within
    the allowed window.  Exit code 12 lets agents branch on timeout vs
    config vs security failures without parsing stderr.
    """

    problem_type = "https://gflow-cli.dev/errors/auth-login-timeout"
    title = "Login timed out"
    _default_remediation = (
        "The sign-in was not completed within the allowed time. "
        "Run `gflow auth login` again and complete sign-in promptly. "
        "Increase GFLOW_CLI_AUTH_LOGIN_TIMEOUT (seconds) if you need more time."
    )


class AuthBrowserRejectedError(GFlowError):
    """Raised when Google rejects the login browser before sign-in can complete."""

    problem_type = "https://gflow-cli.dev/errors/auth-browser-rejected"
    title = "Login browser rejected"
    _default_remediation = (
        "Google rejected Playwright's bundled Chromium as an insecure browser. "
        "Install Google Chrome and rerun `gflow auth login --browser chrome`, "
        "or set GFLOW_CLI_AUTH_BROWSER=chrome so future logins use real Chrome."
    )


class BrowserSessionClosedError(GFlowError):
    """Raised when the underlying Playwright page/context/browser is closed.

    Translated from Playwright's TargetClosedError at the FlowApiClient
    boundary so long-lived workers can catch a stable, library-owned class
    and decide to recreate the client (via its async context manager)
    instead of importing from ``playwright._impl._errors``.
    """

    problem_type = "https://gflow-cli.dev/errors/browser-session-closed"
    title = "Browser session closed"
    _default_remediation = (
        "The Playwright browser/page used by this FlowApiClient is no longer "
        "alive. Recreate the client via `async with FlowApiClient(...)` and "
        "retry the operation."
    )


class BatchPartialError(GFlowError):
    """Raised by `generate_images_batch` under fail-fast when one prompt failed
    after others already produced ready-to-download results.

    Carries `partial_results` (tuple of completed `BatchSubmissionResult`)
    so the orchestrator can still download the user's already-paid-for
    images before surfacing the underlying error.
    """

    problem_type = "https://gflow-cli.dev/errors/batch-partial"
    title = "Batch partially failed"

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        route: str = "",
        remediation_hint: str | None = None,
        partial_results: tuple[Any, ...] = (),
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            detail,
            status=status,
            instance=instance,
            route=route,
            remediation_hint=remediation_hint,
        )
        self.partial_results = partial_results
        self.cause = cause


class BatchIntegrityError(GFlowError):
    """Raised by the orchestrator after a batch returns when the on-disk file
    count does not match the expected count. Catches silent mis-delivery
    even when transport-layer status is reported as 'ok'.
    """

    problem_type = "https://gflow-cli.dev/errors/batch-integrity"
    title = "Batch integrity check failed"

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        route: str = "",
        remediation_hint: str | None = None,
        prompt_indices: tuple[int, ...] = (),
    ) -> None:
        super().__init__(
            detail,
            status=status,
            instance=instance,
            route=route,
            remediation_hint=remediation_hint,
        )
        self.prompt_indices = prompt_indices


class DataStoreError(GFlowError):
    """Raised when the local data layer cannot open, read, or write SQLite."""

    problem_type = "https://gflow-cli.dev/errors/data-store"
    title = "Data store error"
    _default_remediation = (
        "Check GFLOW_CLI_DB_PATH and filesystem permissions. "
        "If the DB was created by a newer gflow-cli, upgrade gflow-cli or "
        "point GFLOW_CLI_DB_PATH at a compatible database."
    )


class DataMigrationError(DataStoreError):
    """Raised when local SQLite schema migration cannot proceed safely."""

    problem_type = "https://gflow-cli.dev/errors/data-migration"
    title = "Data migration error"


class DataIntegrityError(DataStoreError):
    """Raised when repository writes violate expected local DB constraints."""

    problem_type = "https://gflow-cli.dev/errors/data-integrity"
    title = "Data integrity error"


class FrameExtractionError(GFlowError):
    """Raised when the video-chain last-frame extractor cannot produce a frame.

    Covers both the missing-optional-dependency case (PyAV / ``av`` not
    installed because the ``chain`` extra was skipped) and an undecodable /
    truncated input video. The remediation points at the install extra so an
    operator hitting the missing-dependency path can self-serve.
    """

    problem_type = "https://gflow-cli.dev/errors/frame-extraction"
    title = "Last-frame extraction failed"
    _default_remediation = (
        "Could not extract the last frame of the clip. Install the chain extra: "
        "pip install 'gflow-cli[chain]' (provides PyAV). If already installed, the "
        "input video may be truncated or undecodable."
    )


class ChainPartialError(GFlowError):
    """Raised when a sequential video chain fails mid-way after earlier links
    already produced ready-on-disk clips.

    Mirrors ``BatchPartialError`` but for the video chain: ``partial_results``
    carries the ``Path`` of each completed link so the already-paid-for clips
    are surfaced rather than lost. The default is an empty (but present) list —
    a chain that fails before its first link completes is still a valid partial
    with zero results, NEVER ``None``.
    """

    problem_type = "https://gflow-cli.dev/errors/chain-partial"
    title = "Video chain partially failed"
    _default_remediation = (
        "An earlier link in the chain succeeded but a later one failed. The "
        "completed clips are preserved; re-run with --resume-from to continue "
        "from the first failed link instead of regenerating the whole chain."
    )

    def __init__(
        self,
        detail: str = "",
        *,
        status: int | None = None,
        instance: str | None = None,
        route: str = "",
        remediation_hint: str | None = None,
        partial_results: list[Path] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            detail,
            status=status,
            instance=instance,
            route=route,
            remediation_hint=remediation_hint,
        )
        self.partial_results: list[Path] = partial_results if partial_results is not None else []
        self.cause = cause


class ChainManifestError(ConfigurationError):
    """Raised when a chain manifest file cannot be parsed into chain links.

    A configuration/input error (bad JSON, missing ``prompt``, unknown model
    alias, non-int duration, invalid aspect, or an empty manifest). The
    ``detail`` cites the offending line number where applicable. Inherits
    ``ConfigurationError``'s exit code (11) via the EXIT_CODE_MAP isinstance
    walk — no dedicated code is needed because, like other configuration
    mistakes, it is a "fix your input and re-run" failure.
    """

    problem_type = "https://gflow-cli.dev/errors/chain-manifest"
    title = "Chain manifest is invalid"
    _default_remediation = (
        "Fix the chain manifest: it is a JSONL file with one JSON object per "
        'line, each requiring a non-empty "prompt"; optional per-link overrides '
        'are "model", "duration" (int), and "aspect" (9:16 | 16:9 | 1:1). '
        "Blank lines and lines starting with # are ignored, but at least one "
        "valid link is required."
    )


# EXIT_CODE_MAP — most-specific class FIRST per isinstance walk semantics.
# Subclasses inherit their parent's exit code if they don't have their own
# entry. New entries MUST go BEFORE their parent class in this dict.
EXIT_CODE_MAP: dict[type[GFlowError], int] = {
    ChainPartialError: 21,
    FrameExtractionError: 20,
    DataMigrationError: 16,
    DataIntegrityError: 16,
    DataStoreError: 16,
    BrowserSessionClosedError: 15,
    AuthBrowserRejectedError: 14,
    AuthLoginTimeoutError: 12,
    SecurityError: 13,
    AuthMissingError: 8,
    TransportTimeoutError: 9,
    WafRejectionError: 10,
    # UpscaleUnavailableError (issue #171): tier-gated 4K upscale 403, DISTINCT
    # from WafRejectionError's 10 even though both are HTTP 403. Direct GFlowError
    # subclass, so unconstrained by the ordering invariant.
    UpscaleUnavailableError: 22,
    # UiSelectorDriftError (issue #183): Flow UI changed, selector probe failed.
    # Direct GFlowError subclass; exit 23 lets scripts distinguish "UI drifted"
    # from generic error (1) without parsing stderr.
    UiSelectorDriftError: 23,
    # ModelModeIncompatibilityError + VideoModelSelectionError BEFORE
    # ConfigurationError (their parent) so the isinstance walk lands on 17/18,
    # not 11. Per [[exit-code-map-ordering-invariant-test-pitfall]].
    ModelModeIncompatibilityError: 17,
    VideoModelSelectionError: 18,
    # BrowserEngineUnavailableError (Patchright engine opt-in): BEFORE
    # ConfigurationError (its parent) so the isinstance walk lands on 24, not 11.
    BrowserEngineUnavailableError: 24,
    ConfigurationError: 11,
    AuthExpiredError: 3,
    RateLimitError: 4,
    ContentPolicyError: 5,
    NetworkError: 6,
    WireFormatError: 7,
    SceneConcatError: 19,
    # FlowApiError omitted — falls through to default 1
}
