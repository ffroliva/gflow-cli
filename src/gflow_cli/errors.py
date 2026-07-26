from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from gflow_cli.diagnostics import IncidentRef

__all__ = [
    "EXIT_CODE_MAP",
    "RETRYABLE_ERRORS",
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
    "FlowAgentUiError",
    "FlowApiError",
    "FlowAppError",
    "FrameExtractionError",
    "GFlowError",
    "MediaAttributionError",
    "MediaUploadRejectedError",
    "MentionIndexUnavailableError",
    "ModelModeIncompatibilityError",
    "NetworkError",
    "OwnerEvidence",
    "ProblemDetails",
    "QueueSchemaError",
    "RateLimitError",
    "SceneConcatError",
    "SecurityError",
    "TransportTimeoutError",
    "UiModeUnavailableError",
    "UiSelectorDriftError",
    "UpscaleUnavailableError",
    "VideoModelSelectionError",
    "WafRejectionError",
    "WireFormatError",
    "is_retryable",
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
    incident: dict[str, str]  # gflow extension — remote-safe {id, capture_status} ONLY


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
    #: Set post-raise by the incident capture boundary (diagnostics design).
    #: ``to_problem_details()`` exposes only the remote-safe {id, capture_status}
    #: projection; the local path/artifacts stay CLI-local (S21).
    incident_ref: IncidentRef | None = None

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
        if self.incident_ref is not None:
            out["incident"] = {
                "id": self.incident_ref.id,
                "capture_status": self.incident_ref.capture_status,
            }
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
    _default_remediation = (
        "Check request parameters and network connection, then retry the API call."
    )

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
    _default_remediation = (
        "Daily or per-minute model quota reached; retry with a different model or "
        "wait for quota reset"
    )

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
    """Flow rejected the request under its content policy.

    Two known raise sites:

    1. **HTTP 200 with empty ``media[]``** — the classic content-safety path
       (``_common.py``). ``status`` is omitted from ``to_problem_details()``
       per RFC 9457 — 200 conflates with success. The literal upstream
       status (200) is recorded only via the ``error_raised`` log event as
       an ``upstream_status`` extension (see observability.py).

    2. **HTTP 400 with a content-safety reason** — ``_raise_for_non_retryable``
       in ``client.py``. The body carries ``details[].reason`` =
       ``PUBLIC_ERROR_UNSAFE_GENERATION`` (or ``_CONTENT``,
       ``_FACE``, ``_IDENTITY``). ``status=400`` is set on the exception
       but stripped from ``to_problem_details()`` per the same RFC 9457
       contract.

    Enforcement is at the class level (overrides ``to_problem_details``) —
    relying on callers to omit ``status=`` would silently break the RFC 9457
    contract the first time someone added it for symmetry with other error
    classes.
    """

    problem_type = "https://gflow-cli.dev/errors/content-policy"
    title = "Content policy rejection"
    _default_remediation = "Reduce prompt text or describe <= 1 person per scene"

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
        "Check request payload parameters or retry with a simpler prompt text. "
        "File a bug at https://github.com/ffroliva/gflow-cli/issues with the discovery payload above."
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
        "Ensure video clip dimensions and codecs match before concatenation"
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


@dataclass(frozen=True, slots=True)
class OwnerEvidence:
    """Private diagnostic evidence about the recorded lease owner (§6.4 of the
    incident-diagnostics design). Identities are per-command HMACs — never the
    raw profile name or owner token. The kernel lock is authoritative; this
    metadata can be stale and never authorizes reclaim, unlink, or PID kill."""

    pid: int
    process_start_time: float
    profile_identity: str
    owner_token_identity: str


class ProfileLockedError(ConfigurationError):
    """Raised when the profile directory is held by another process.

    Two known causes, each raise site supplying its own remediation: a running
    ``gflow serve`` daemon (browser_manager), or another Chrome — typically a
    stale browser leaked by a crashed prior run — holding the dir at
    persistent-context launch (issue #293).

    ``owner_evidence`` is a PRIVATE typed attribute set by ``ProfileLease``
    contention paths for the incident recorder and local human formatter only.
    It is deliberately excluded from ``to_problem_details()`` and therefore
    from every MCP/HTTP/worker/structured-log surface.
    """

    problem_type = "https://gflow-cli.dev/errors/profile-locked"
    title = "Profile locked"
    owner_evidence: OwnerEvidence | None = None
    _default_remediation = (
        "A live process holds this profile: a gflow command, a `gflow serve` "
        "daemon, or a Chrome/python process using the profile dir. Close it and "
        "retry, or use a different profile name. If nothing is running, the "
        "kernel lock is already released — a leftover lock file never blocks "
        "acquisition, so just retry."
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


class FlowAgentUiError(GFlowError):
    """Raised when Google Flow's new Agentic UI cohort is detected at runtime.

    This cohort replaces the classic generation controls with a chat interface
    that is not supported by gflow-cli. Raising this error allows the CLI to
    fail cleanly with exit code 25 instead of timing out or raising drift errors.
    """

    problem_type = "https://gflow-cli.dev/errors/flow-agent-ui"
    title = "Google Flow Agentic UI detected"
    _default_remediation = (
        "Your account has been placed in Google Flow's new 'Agentic UI' A/B cohort, "
        "which removes the classic media generation controls. gflow-cli does not "
        "currently support driving this interface. Try using a different Chrome profile, "
        "or wait for a future update. If you need to share a bug report, review the "
        "diagnostic screenshot first — the viewport may show personal info (do NOT "
        "include tokens or credentials)."
    )


class FlowAppError(GFlowError):
    """Raised when Google Flow's web app itself crashed — a client-side exception
    (its React error boundary), not a gflow-cli issue. The editor never rendered,
    so no generation control exists to drive. **Transient and retryable** (exit
    code 31). Detected at the mode-switch raise site via the Flow error-page title,
    which otherwise surfaces as a misleading ``UiSelectorDriftError`` "file a bug".
    """

    problem_type = "https://gflow-cli.dev/errors/flow-app"
    title = "Google Flow web app error"
    _default_remediation = (
        "Google Flow's web app failed to load (a client-side exception on "
        "labs.google) — a transient Flow-side error, not a gflow-cli bug. Retry in a "
        "moment; if it persists, check https://labs.google/fx and try a fresh session."
    )


class UiModeUnavailableError(GFlowError):
    """Raised when the Flow UI arm a command REQUIRES (``--ui-mode`` /
    ``GFLOW_CLI_UI_MODE``, or inferred — e.g. ``-i`` instructions force agentic)
    could not be reached, after a best-effort switch + re-probe (issue #299).

    Distinct from ``FlowAgentUiError`` (exit 25 = "gflow cannot drive the
    agentic UI at all"): here gflow *can* drive both arms, but the caller needs
    a specific one and the server would not switch to it, so we abort **before**
    submitting — zero credits spent. The cohort is server-assigned per page load
    and flaps, so this failure is **retryable**: a re-run often lands the wanted
    arm. Exit code 28 lets scripts branch on "retry / switch profile / relax
    --ui-mode" vs a generic error.

    ``requested`` carries the ``UiMode`` that could not be reached.
    """

    problem_type = "https://gflow-cli.dev/errors/ui-mode-unavailable"
    title = "Requested Flow UI mode unavailable"

    def __init__(self, requested: object, *, remediation_hint: str | None = None) -> None:
        self.requested = requested
        arm = getattr(requested, "value", str(requested))
        other = "agentic" if arm == "classic" else "classic"
        self._default_remediation = (
            f"This command required the '{arm}' Flow UI but the arm could not be "
            f"reached on this profile. Aborted before submitting — no credits were "
            f"spent. The cohort flaps per page load, so RETRY (a re-run often lands "
            f"'{arm}'); or try a different --profile; or relax to --ui-mode "
            f"{other}/auto if the command allows it. Note: a server-side experiment "
            f"can pin the arm, in which case '{arm}' cannot be reached from the client."
        )
        super().__init__(
            f"Required Flow UI mode '{arm}' is unavailable on this profile.",
            remediation_hint=remediation_hint,
        )


class MediaAttributionError(GFlowError):
    """Raised when generated media cannot be reliably attributed to the request
    that produced it (issue #281).

    Canonical case: the agentic driver's ``await_images`` polls the DOM for
    new ``<img name=<uuid>>`` tiles. A lazily-rendered pre-existing project
    asset can appear in the DOM after generation starts and be mistaken for
    "new" media, or more new UUIDs can appear than were requested — either
    way there is no reliable signal for WHICH uuid(s) belong to this
    generation. Rather than guess by slicing an unordered UUID set (the
    2026-07-10 production incident: a pre-existing logo was silently
    downloaded and reported as a fresh generation), the driver raises this
    error and lets the caller re-run.

    Fail-fast over silent-wrong: an error the user sees beats a wrong
    artifact reported as success (precedent: the ``--model`` silent no-op,
    PR #48).
    """

    problem_type = "https://gflow-cli.dev/errors/media-attribution"
    title = "Generated media could not be attributed"
    _default_remediation = (
        "gflow could not reliably attribute the generated media to this request. "
        "Re-run the generation; a dedicated project with fewer pre-existing "
        "assets avoids lazy-render ambiguity (issue #281)."
    )


class MediaUploadRejectedError(GFlowError):
    """Raised when Flow's upload endpoint refuses a media file (issue #287).

    Canonical case: the ``uploadImage`` XHR for an i2v frame returns a 4xx —
    some byte-identical-format siblings upload fine while one file's metadata
    segment upsets the endpoint. Previously this surfaced as a bare
    ``RuntimeError`` → generic "Unexpected error." (exit 1) with no hint that
    the *input image* was refused, costing a diagnosis round-trip.
    """

    problem_type = "https://gflow-cli.dev/errors/media-upload-rejected"
    title = "Flow rejected a media upload"
    _default_remediation = (
        "Flow's upload endpoint refused the file, so the generation was aborted "
        "before spending credits. Try re-encoding the image to strip metadata "
        "(e.g. `ffmpeg -i in.jpg -q:v 2 -map_metadata -1 out.jpg`), or reference "
        "the asset by its media UUID if it already exists in the project."
    )


class MentionIndexUnavailableError(GFlowError):
    """Raised when a prompt contains an ``@mention`` but a catalog source
    needed to resolve it (character entities or media assets) is unavailable.

    Distinct from an empty source: a source that loads successfully with zero
    rows is NOT an outage -- resolution proceeds and reports the mention as
    unknown via the normal ``resolve_mentions()`` path. This error is raised
    only when the source loader itself failed (a Flow API error for
    characters, a data-store error for media), so an outage is never silently
    indistinguishable from "no matching asset" (fail closed instead of
    degrading to an empty index).

    ``detail`` names WHICH source failed ("character" or "media") but never
    includes prompt text or catalog content. The original failure is
    preserved as ``__cause__`` for diagnostics.
    """

    problem_type = "https://gflow-cli.dev/errors/mention-index-unavailable"
    title = "Mention index unavailable"
    _default_remediation = (
        "gflow could not load the asset catalog needed to resolve an @mention. "
        "Check network connectivity (character source) or GFLOW_CLI_DB_PATH / "
        "filesystem permissions (media source), then retry."
    )


class QueueSchemaError(GFlowError):
    """Raised when a worker-queue task payload's schema is invalid or unknown
    (Task C2 — versioned queue codec, design spec §3).

    Covers: an unrecognized ``schema_version`` (anything other than the
    implicit legacy 0 or the current 1), an unknown ``task_type``
    discriminator, or a payload that fails structural validation (missing
    required field, invalid enum, out-of-range count, malformed path) when
    mapped onto the existing typed ``GenerateImageRequest`` /
    ``GenerateVideoRequest`` DTOs.

    Raised by ``worker/codec.py`` BEFORE Playwright starts — no browser
    launch, no credit spend. An unknown version is a stable, typed failure;
    it is never interpreted optimistically. ``detail`` is redacted and never
    echoes prompt text.
    """

    problem_type = "https://gflow-cli.dev/errors/queue-schema"
    title = "Worker queue payload schema is invalid"
    _default_remediation = (
        "The queued task's payload could not be decoded. This usually means "
        "gflow-cli was downgraded after a newer version enqueued the task, or "
        "the payload was hand-edited. Re-enqueue the task with a compatible "
        "gflow-cli version."
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
    _default_remediation = (
        "One or more prompts in the batch failed; check individual prompt errors "
        "or retry the failed items."
    )

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
    _default_remediation = (
        "The generated image count did not match expectations; retry the batch operation."
    )

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
    _default_remediation = "Check database file permissions or run 'gflow data errors prune'"


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
        "Verify input video file is readable and non-corrupt. Ensure "
        "gflow-cli[chain] dependencies (PyAV) are installed."
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
    FlowAgentUiError: 25,
    FlowAppError: 31,
    # UiModeUnavailableError (issue #299): a command's required arm (--ui-mode /
    # inferred) couldn't be reached after a best-effort switch. Direct GFlowError
    # subclass — retryable policy abort, distinct from FlowAgentUiError (25).
    UiModeUnavailableError: 28,
    # MediaAttributionError (issue #281): generated media could not be
    # reliably attributed (agentic DOM-scrape ambiguity, or a downstream
    # already-recorded check). Direct GFlowError subclass; exit 26 lets
    # scripts distinguish "wrong/ambiguous media" from a generic error (1).
    MediaAttributionError: 26,
    # MediaUploadRejectedError (issue #287): Flow's upload endpoint refused the
    # input file (uploadImage 4xx). Direct GFlowError subclass; exit 27 lets
    # scripts branch on "re-encode the input image" vs generic error (1).
    MediaUploadRejectedError: 27,
    # MentionIndexUnavailableError: an @mention was present but the catalog
    # source needed to resolve it (character or media) failed to load.
    # Direct GFlowError subclass; exit 29 lets scripts distinguish "the
    # catalog is unreachable" from an unknown-mention ConfigurationError (11).
    MentionIndexUnavailableError: 29,
    # QueueSchemaError (Task C2): a worker-queue payload's schema_version is
    # unknown, or its fields fail validation against the typed request DTOs.
    # Direct GFlowError subclass; exit 30 lets scripts distinguish "the queue
    # row is malformed/from an incompatible version" from generic error (1).
    QueueSchemaError: 30,
    ConfigurationError: 11,
    AuthExpiredError: 3,
    RateLimitError: 4,
    ContentPolicyError: 5,
    NetworkError: 6,
    WireFormatError: 7,
    SceneConcatError: 19,
    # FlowApiError omitted — falls through to default 1
}

# Transient failures the caller can retry without operator intervention — WAF
# bounce, rate-limit/quota, transport timeout, network blip, a dropped browser
# session, a Flow web-app crash (31), or an agentic-cohort flap (25). Everything
# else (auth/content-policy/config/security) is terminal: retrying the identical
# request will fail the same way. Single source of truth for the CLI --json, MCP,
# and worker error envelopes — never fork a private copy of this list.
RETRYABLE_ERRORS: tuple[type[GFlowError], ...] = (
    WafRejectionError,
    RateLimitError,
    TransportTimeoutError,
    NetworkError,
    BrowserSessionClosedError,
    FlowAppError,
    FlowAgentUiError,
)


def is_retryable(exc: GFlowError) -> bool:
    """Shared retry classification consumed by every machine-readable error surface."""
    return isinstance(exc, RETRYABLE_ERRORS)
