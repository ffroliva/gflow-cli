"""Which tool envelopes describe a failure (#1453, #1464).

One definition serves two readers: the server's ``isError`` flag on a plain
``tools/call`` (``GflowMCPServer.call_tool``) and the telemetry outcome a tool
call records (``tools.outcome_of``). Keeping them on one set means a status the
telemetry counts as a failure is never reported to the client as a success.
"""

from __future__ import annotations

from typing import Any, cast

from gflow_cli.auth.verification import FlowSessionOutcome

_TOOL_FAILURES = frozenset({"error", "failed", "rate_limited", "invalid_tools"})

# ``gflow_auth_status`` returns the probe's outcome as its status. Every outcome but
# ``authenticated`` is a session the probe could not verify, which ``gflow auth status``
# exits 1 for. ``throttled`` is its refusal to probe an account Flow is rate-limiting.
_AUTH_FAILURES = frozenset(
    {o.value for o in FlowSessionOutcome if o is not FlowSessionOutcome.AUTHENTICATED}
    | {"throttled"}
)

FAILURE_STATUSES: frozenset[str] = _TOOL_FAILURES | _AUTH_FAILURES
"""Envelope ``status`` values that describe a failed tool call."""


def is_failure(envelope: object) -> bool:
    """True when ``envelope`` is a tool envelope whose ``status`` is a failure."""
    if not isinstance(envelope, dict):
        return False
    # A tool may return any JSON: a non-string ``status`` (an object, say) is not an
    # envelope status, and testing it against the set would raise on an unhashable value.
    status = cast("dict[str, Any]", envelope).get("status")
    return isinstance(status, str) and status in FAILURE_STATUSES
