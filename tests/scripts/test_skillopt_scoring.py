"""Scoring regression lock for the SkillOpt harness.

``score_response`` accumulates a float — a base ratio, minus 0.3 per forbidden
term, plus 0.1 per partial-credit term — and the harness then grades the result
against a hard ``>= 0.8`` PASS threshold. Binary floating point makes the exact
boundary unreachable from the obvious direction:

    1.0 - 0.3 + 0.1 == 0.7999999999999999

so a task that scores exactly the threshold was graded PARTIAL while printing
"0.80", giving a reader no way to see why. Observed live grading a real rollout.

Rounding to 4 decimals is more precision than any scoring rule here produces
(the coarsest input is 0.1) and leaves the threshold comparison exact.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HARNESS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "dev" / "skillopt"
sys.path.insert(0, str(_HARNESS_DIR))

from harness import score_response  # noqa: E402 — path is set up immediately above


def test_threshold_case_is_not_lost_to_float_accumulation() -> None:
    """One forbidden hit plus one bonus on a perfect base lands exactly on PASS."""
    score, _ = score_response(
        "use omni-flash; veo-quality has a cap of 0",
        {
            "must_include": ["omni-flash"],
            "must_not_include": ["veo-quality"],
            "partial_credit": ["cap"],
        },
    )
    assert score == 0.8
    assert score >= 0.8, "a score printed as 0.80 must grade as PASS"


def test_all_requirements_met_scores_one() -> None:
    score, reasons = score_response(
        "gflow project create --name X --json",
        {"must_include": ["gflow project create"], "must_not_include": ["placeholder"]},
    )
    assert score == 1.0
    assert reasons == []


def test_missing_requirement_is_reported_and_scored_down() -> None:
    score, reasons = score_response(
        "veo-lite",
        {"must_include": ["omni-flash"], "must_not_include": []},
    )
    assert score == 0.0
    assert reasons == ["MISS: ['omni-flash']"]


@pytest.mark.parametrize("forbidden_count", [1, 2, 3, 4])
def test_penalty_is_capped_so_a_correct_answer_cannot_be_buried(
    forbidden_count: int,
) -> None:
    """The 0.9 penalty cap keeps a fully-correct answer above zero."""
    bad = " ".join(f"bad{i}" for i in range(forbidden_count))
    score, _ = score_response(
        f"omni-flash {bad}",
        {
            "must_include": ["omni-flash"],
            "must_not_include": [f"bad{i}" for i in range(forbidden_count)],
        },
    )
    assert score >= 0.1 - 1e-9
    assert score == round(score, 4)
