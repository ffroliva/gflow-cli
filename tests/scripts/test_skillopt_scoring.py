"""Scoring and resilience regression locks for the SkillOpt harness.

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

from types import SimpleNamespace
from typing import Any

import pytest

from scripts.dev.skillopt import harness
from scripts.dev.skillopt.harness import RolloutError, score_response


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


#: Minimal stand-in for Settings — run_live only reads the endpoint for its banner.
_SETTINGS = SimpleNamespace(llm_base_url="https://gateway.example/v1")

_TASKS: list[dict[str, Any]] = [
    {"id": "ok-1", "question": "q1", "tags": ["a"], "expected": {"must_include": ["yes"]}},
    {"id": "boom", "question": "q2", "tags": ["a"], "expected": {"must_include": ["yes"]}},
    {"id": "ok-2", "question": "q3", "tags": ["b"], "expected": {"must_include": ["yes"]}},
]


def test_a_failed_rollout_does_not_discard_the_scores_already_paid_for(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One task's failure must not cost the whole suite.

    The harness previously exited from inside the LLM call, unwinding past the
    summary and throwing away every completed rollout. On a metered free tier
    those are the expensive half of the run -- this happened twice in one
    session, losing 5 and then 14 scored tasks.
    """

    def fake_call(system: str, user: str, model: str | None, settings: Any) -> str:
        if "q2" in user:
            raise RolloutError("HTTP 429: quota exhausted")
        return "yes"

    monkeypatch.setattr(harness, "_call_llm", fake_call)
    harness.run_live(_TASKS, "skill body", "1.0", 0, "m", _SETTINGS)

    out = capsys.readouterr().out
    assert "ERROR: HTTP 429: quota exhausted" in out
    # The suite continued past the failure and still summarised.
    assert "SUMMARY: 2/2 passed" in out
    assert "1 of 3 task(s) did not complete and are excluded" in out


def test_an_errored_task_is_excluded_from_the_average_not_scored_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An ERROR is missing data. Averaging it as 0.0 would fake a regression."""

    def fake_call(system: str, user: str, model: str | None, settings: Any) -> str:
        if "q2" in user:
            raise RolloutError("transport died")
        return "yes"

    monkeypatch.setattr(harness, "_call_llm", fake_call)
    harness.run_live(_TASKS, "skill body", "1.0", 0, "m", _SETTINGS)

    out = capsys.readouterr().out
    assert "avg score 1.000" in out, "two perfect rollouts must average 1.0, not 0.667"


def test_every_task_failing_reports_no_rollout_rather_than_a_zero_score(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_call(system: str, user: str, model: str | None, settings: Any) -> str:
        raise RolloutError("endpoint down")

    monkeypatch.setattr(harness, "_call_llm", fake_call)
    harness.run_live(_TASKS, "skill body", "1.0", 0, "m", _SETTINGS)

    out = capsys.readouterr().out
    assert "no task completed a rollout" in out
    assert "avg score" not in out, "an all-error run has no average to report"
