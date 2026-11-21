"""The problem-details envelope ``gflow_auth_status`` returns for an outcome that is not a
dead session. A dead session is built from ``AuthExpiredError`` by the tool itself."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gflow_cli.auth.verification import FlowSessionOutcome
from gflow_cli.errors import NextAction, outcome_members

if TYPE_CHECKING:
    from gflow_cli.auth.verification import FlowSessionStatus

# outcome -> (type slug, title, HTTP status, next_action, remediation_hint)
_ENVELOPES: dict[FlowSessionOutcome, tuple[str, str, int, NextAction, str]] = {
    # #796: a local profile-state fault. Not retryable (the marker will not reappear on
    # its own) and not an expired session, so neither 503 nor 401 describes it: an agent
    # that retries or re-logins here learns nothing.
    FlowSessionOutcome.PROFILE_MARKER_MISSING: (
        "profile-marker-missing",
        "Profile is missing its browser-strategy marker",
        409,
        "reauth",
        "This profile has no `.gflow_browser_strategy` marker, so its "
        "cookies cannot be read. Re-run `gflow auth login --browser "
        "chrome` for this profile to rewrite it.",
    ),
    # #796: local file state like the marker case. A retry re-reads the same broken
    # file, so not retryable, and not a network 503.
    FlowSessionOutcome.COOKIE_STORE_UNREADABLE: (
        "cookie-store-unreadable",
        "Profile cookie store is unreadable",
        409,
        "reauth",
        "This profile's Chrome cookie database is corrupt or unreadable. "
        "Close any Chrome window using this profile, then re-run "
        "`gflow auth login` for it.",
    ),
    # #1400: the cookie read could not open the profile (Chrome holds it, or cannot
    # start). Local, so not the 503 network envelope; retryable once Chrome lets go.
    FlowSessionOutcome.PROFILE_UNAVAILABLE: (
        "profile-unavailable",
        "Profile cookies could not be read",
        409,
        "retry",
        "Chrome has this profile in use, or could not start. Close any Chrome "
        "window using this profile and check that Google Chrome is installed, "
        "then retry.",
    ),
    # A network/endpoint problem is not fixed by re-login, and the machine-readable
    # discriminators must say so too: labelling this 401/auth-expired sent
    # type-dispatching agents into an interactive re-login on every network blip.
    FlowSessionOutcome.VERIFICATION_ERROR: (
        "verification-error",
        "Flow session verification failed",
        503,
        "retry",
        "Could not verify the Flow session (network or endpoint "
        "problem). Check connectivity and retry; re-login is only "
        "needed if the session is actually dead.",
    ),
}


def auth_status_error(status: FlowSessionStatus) -> dict[str, Any] | None:
    """The envelope for ``status``, or ``None`` when it is a dead session."""
    spec = _ENVELOPES.get(status.outcome)
    if spec is None:
        return None
    slug, title, http_status, next_action, hint = spec
    return {
        "type": f"https://gflow-cli.dev/errors/{slug}",
        "title": title,
        "status": http_status,
        "detail": status.detail,
        "message": status.detail,
        **outcome_members("no", next_action),
        "remediation_hint": hint,
    }
