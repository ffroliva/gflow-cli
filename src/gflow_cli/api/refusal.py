"""Terminal refusal error factory for Flow submit attempts (#1545 / Task 8)."""

from __future__ import annotations

from gflow_cli.errors import WafRejectionError

__all__ = ["waf_refusal_final"]


def waf_refusal_final(*, reason: str = "UNUSUAL_ACTIVITY") -> WafRejectionError:
    """Terminal refusal error when both submit attempts are refused by Flow (#1545 / Task 8).

    Tells callers and agents not to retry into a refusal window (charged="no",
    retryable=False, next_action="none"). The remediation notes that both attempts
    were refused and nothing was charged, names `--browser-visibility headed`, and
    points to the 30-60 minute cooldown in docs/ACCOUNT_SAFETY.md. A relaunch that fails
    between the attempts is ``RelaunchFailed``, not this error.
    """
    remediation = (
        "Both attempts were refused and nothing was charged. "
        "Wait for the 30-60 minute cooldown described in docs/ACCOUNT_SAFETY.md "
        "before trying again, or retry with --browser-visibility headed."
    )
    detail = "Flow refused 2 submit attempts" + (f": {reason}" if reason else "")
    return WafRejectionError(
        detail,
        remediation_hint=remediation,
        retryable=False,
        charged="no",
        next_action="none",
    )
