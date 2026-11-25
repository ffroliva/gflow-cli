"""Which tool envelopes describe a failure (#1453, #1464).

One definition serves two readers: the server's ``isError`` flag on a plain
``tools/call`` (``GflowMCPServer.call_tool``) and the telemetry outcome a tool
call records (``tools.outcome_of``). Keeping them on one set means a status the
telemetry counts as a failure is never reported to the client as a success.
"""

from __future__ import annotations

from typing import Any, cast

FAILURE_STATUSES: frozenset[str] = frozenset({"error", "failed", "rate_limited", "invalid_tools"})
"""Envelope ``status`` values that describe a failed tool call."""


def is_failure(envelope: object) -> bool:
    """True when ``envelope`` is a tool envelope whose ``status`` is a failure."""
    if not isinstance(envelope, dict):
        return False
    # A tool may return any JSON: a non-string ``status`` (an object, say) is not an
    # envelope status, and testing it against the set would raise on an unhashable value.
    status = cast("dict[str, Any]", envelope).get("status")
    return isinstance(status, str) and status in FAILURE_STATUSES
