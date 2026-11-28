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
    points to the 30-60 minute cooldown in docs/ACCOUNT_SAFETY.md. One attempt means the
    browser relaunch for the retry failed, so only the first send reached Flow.
    """
    if attempts == 1:
        summary = (
            "The attempt was refused, the browser relaunch for the retry failed, "
            "and nothing was charged."
        )
    elif attempts == 2:
        summary = "Both attempts were refused and nothing was charged."
    else:
        summary = f"All {attempts} attempts were refused and nothing was charged."
    remediation = (
        f"{summary} "
        "Wait for the 30-60 minute cooldown described in docs/ACCOUNT_SAFETY.md "
        "before trying again, or retry with --browser-visibility headed."
    )
    noun = "attempt" if attempts == 1 else "attempts"
    detail = f"Flow refused {attempts} submit {noun}" + (f": {reason}" if reason else "")
    return WafRejectionError(
        detail,
        remediation_hint=remediation,
        retryable=False,
        charged="no",
        next_action="none",
    )
