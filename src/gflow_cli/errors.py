from __future__ import annotations

from typing import Any, TypedDict

__all__ = [
    "ProblemDetails",
    "GFlowError",
    "FlowApiError",
    "AuthExpiredError",
    "RateLimitError",
    "ContentPolicyError",
    "NetworkError",
    "WireFormatError",
    "TransportTimeoutError",
    "WafRejectionError",
    "ConfigurationError",
    "SecurityError",
    "AuthMissingError",
    "AuthLoginTimeoutError",
    "EXIT_CODE_MAP",
]


class ProblemDetails(TypedDict, total=False):
    """RFC 9457 Problem Details JSON shape (https://datatracker.ietf.org/doc/html/rfc9457).
    Two gflow extensions: `remediation_hint` and `route`."""

    type: str  # required  # noqa: A003 — RFC 9457 wire field name; cannot rename
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


# EXIT_CODE_MAP — most-specific class FIRST per isinstance walk semantics.
# Subclasses inherit their parent's exit code if they don't have their own
# entry. New entries MUST go BEFORE their parent class in this dict.
EXIT_CODE_MAP: dict[type[GFlowError], int] = {
    AuthLoginTimeoutError: 12,
    SecurityError: 13,
    AuthMissingError: 8,
    TransportTimeoutError: 9,
    WafRejectionError: 10,
    ConfigurationError: 11,
    AuthExpiredError: 3,
    RateLimitError: 4,
    ContentPolicyError: 5,
    NetworkError: 6,
    WireFormatError: 7,
    # FlowApiError omitted — falls through to default 1
}
