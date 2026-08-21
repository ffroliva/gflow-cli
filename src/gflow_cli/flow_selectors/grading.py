"""Pure grading. No browser, no IO."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gflow_cli.config import UiMode
from gflow_cli.flow_selectors.model import Selector


class Grade(Enum):
    HIT = "hit"
    FALLBACK = "fallback"  # a later candidate held — warn, do not fail
    AMBIGUOUS = "ambiguous"  # >1 match; drivers use .first, so this misclicks
    MISS = "miss"
    EXPECTED_ABSENT = "expected_absent"


@dataclass(frozen=True)
class Outcome:
    selector_key: str
    grade: Grade
    resolved_index: int | None

    @property
    def is_failure(self) -> bool:
        return self.grade in (Grade.MISS, Grade.AMBIGUOUS)


def grade(
    selector: Selector,
    resolved_index: int | None,
    match_count: int,
    observed_mode: UiMode,
) -> Outcome:
    """``observed_mode`` is REQUIRED, deliberately.

    Defaulting it to None let a mode-scoped selector be graded with no context,
    which produced a guaranteed false DRIFT on every agentic capture. Requiring
    it moves that guarantee to the type level — the caller cannot forget.
    """
    if resolved_index is not None:
        if selector.expect_unique and match_count > 1:
            return Outcome(selector.key, Grade.AMBIGUOUS, resolved_index)
        g = Grade.HIT if resolved_index == 0 else Grade.FALLBACK
        return Outcome(selector.key, g, resolved_index)

    if selector.mode is not None and selector.mode != observed_mode:
        return Outcome(selector.key, Grade.EXPECTED_ABSENT, None)
    return Outcome(selector.key, Grade.MISS, None)
