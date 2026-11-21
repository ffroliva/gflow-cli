"""Pure credit quote parsing and per-command budget enforcement."""

from __future__ import annotations

import re
from collections.abc import Sequence

from gflow_cli.errors import CreditCapExceededError

_INTEGER = re.compile(r"(?<!\d)\d+(?!\d)")


def parse_credit_quote(label: str) -> int | None:
    """Return the sole integer token in Flow's credit label, or None if ambiguous."""
    matches = _INTEGER.findall(label)
    return int(matches[0]) if len(matches) == 1 else None


def enforce_credit_cap(
    *, quoted: int | None, max_credits: int | None, route: str = ""
) -> int | None:
    """Fail closed for an unreadable quote when a cap is active."""
    if max_credits is None:
        return quoted
    if quoted is None or quoted > max_credits:
        raise CreditCapExceededError(quoted=quoted, max_credits=max_credits, route=route)
    return quoted


def resolve_max_credits(cli_value: int | None, configured: int | None) -> int | None:
    """Use an explicit CLI cap when supplied; otherwise use the environment setting."""
    return cli_value if cli_value is not None else configured


def preserve_credit_cap_partials(
    exc: BaseException, completed_media_ids: Sequence[str]
) -> list[str]:
    """Attach prior and current billed media ids to a credit-cap failure."""
    prior = list(completed_media_ids)
    current = [
        media_id for media_id in getattr(exc, "completed_media_ids", []) if media_id not in prior
    ]
    all_ids = prior + current
    if isinstance(exc, CreditCapExceededError) and all_ids:
        if exc.charged != "maybe":
            exc.charged = "maybe" if current else "yes"
        exc.next_action = "reconcile"
        exc.retryable = False
        exc.completed_media_ids = all_ids
    return current


class CreditBudget:
    """Mutable remaining credits for a multi-submit command."""

    def __init__(self, max_credits: int | None) -> None:
        self.remaining = max_credits

    @property
    def max_credits(self) -> int | None:
        """Cap to pass to the next submission."""
        return self.remaining

    def record_spend(self, quoted: int | None) -> None:
        """Deduct a completed submission's quote; unknown cost fails closed."""
        if self.remaining is None:
            return
        if quoted is None or quoted > self.remaining:
            limit = self.remaining
            self.remaining = 0 if quoted is None else max(0, self.remaining - quoted)
            error = CreditCapExceededError(quoted=quoted, max_credits=limit)
            error.charged, error.next_action = "maybe", "reconcile"
            error.retryable = False
            raise error
        self.remaining -= quoted
