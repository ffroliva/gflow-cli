# SPDX-License-Identifier: MIT
"""A fork PR whose CI was never approved renders as fully green. Catch that.

GitHub gates `pull_request`-triggered workflows on fork PRs behind a maintainer's
"Approve and run" click. Until someone clicks it the run exists with
``conclusion="action_required"`` — and, decisively, **it does not appear in the PR's
`statusCheckRollup` at all**. Branch protection evaluates the rollup, and so does every
human glancing at the checks list, so the PR presents as green with zero test evidence.

Measured on this repository on 2026-09-15, three open fork PRs, identical labels and all
three cross-repository:

===  ===================================  =======================================
PR   `pull_request` workflow runs         what the checks UI showed
===  ===================================  =======================================
781  CI / Guard main base / Governance    16 checks, all green (a human approved)
793  same three, ``action_required``      2 checks, both green
787  none created at all                  2 checks, both green
===  ===================================  =======================================

#793 had previously shipped broken tests and a ruff-format failure past exactly this
gap, with nothing red anywhere. These tests pin the decision function against those
three real shapes.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ci"))

from check_fork_pr_ci import REQUIRED_WORKFLOWS, untrustworthy_workflows  # noqa: E402


def _run(name: str, event: str, conclusion: str | None, status: str = "completed") -> dict:
    return {"name": name, "event": event, "status": status, "conclusion": conclusion}


#: What always runs for a fork, approval or not — and what lulls the reader.
_TRIAGE = _run("External PR Triage", "pull_request_target", "success")


def test_an_approved_fork_pr_is_clean() -> None:
    """#781's shape: a human clicked Approve and run, so CI really ran."""
    runs = [_TRIAGE, _run("CI", "pull_request", "success")]
    assert untrustworthy_workflows(runs, {"CI"}) == {}


def test_ci_awaiting_approval_is_reported() -> None:
    """#793's shape: the run exists, is absent from the rollup, and proves nothing."""
    runs = [_TRIAGE, _run("CI", "pull_request", "action_required")]
    verdict = untrustworthy_workflows(runs, {"CI"})
    assert "CI" in verdict
    assert "approval" in verdict["CI"].lower(), verdict["CI"]


def test_ci_that_never_ran_is_reported() -> None:
    """#787's shape: no `pull_request` run exists for the head SHA at all."""
    verdict = untrustworthy_workflows([_TRIAGE], {"CI"})
    assert "CI" in verdict
    assert "no run" in verdict["CI"].lower(), verdict["CI"]


def test_a_passing_triage_alone_never_counts_as_coverage() -> None:
    """The exact false comfort: pull_request_target runs regardless of approval.

    Both failing PRs showed a green `External PR Triage`. If that were allowed to
    satisfy a required workflow, this whole check would restate the bug.
    """
    assert untrustworthy_workflows([_TRIAGE], {"CI"}) != {}


def test_an_outright_failure_is_not_our_business() -> None:
    """A red check is already visible; this gate is only for the invisible states."""
    runs = [_run("CI", "pull_request", "failure")]
    assert untrustworthy_workflows(runs, {"CI"}) == {}


def test_a_still_running_workflow_is_not_flagged() -> None:
    """In-progress CI is surfaced as pending by the rollup, so it is not invisible."""
    runs = [_run("CI", "pull_request", None, status="in_progress")]
    assert untrustworthy_workflows(runs, {"CI"}) == {}


def test_every_required_workflow_is_checked_not_just_the_first() -> None:
    runs = [_TRIAGE, _run("CI", "pull_request", "success")]
    verdict = untrustworthy_workflows(runs, {"CI", "Guard main base"})
    assert set(verdict) == {"Guard main base"}


def test_the_shipped_required_set_names_the_test_matrix() -> None:
    """A required set that omitted CI would make this gate decorative."""
    assert "CI" in REQUIRED_WORKFLOWS
