"""Terminal refusal error factory for Flow submit attempts (#1545 / Task 8)."""

from __future__ import annotations

from gflow_cli.errors import WafRejectionError

__all__ = ["waf_refusal_final"]


def waf_refusal_final(
    *,
    reason: str = "UNUSUAL_ACTIVITY",
    attempts: int = 2,
) -> WafRejectionError:
    """Terminal refusal error when all submit attempts are refused by Flow (#1545 / Task 8).

    Tells callers and agents not to retry into a refusal window (charged="no",
    retryable=False, next_action="none"). The remediation notes that both attempts
    were refused and nothing was charged, names `--browser-visibility headed`, and
    points to the 30-60 minute cooldown in docs/ACCOUNT_SAFETY.md.
    """
    attempt_str = "Both attempts were" if attempts == 2 else f"All {attempts} attempts were"
    remediation = (
        f"{attempt_str} refused and nothing was charged. "
        "Wait for the 30-60 minute cooldown described in docs/ACCOUNT_SAFETY.md "
        "before trying again, or retry with --browser-visibility headed."
    )
    detail = (
        f"Flow refused {attempts} submit attempts: {reason}"
        if reason
        else f"Flow refused {attempts} submit attempts"
    )
    return WafRejectionError(
        detail,
        remediation_hint=remediation,
        retryable=False,
        charged="no",
        next_action="none",
    )
