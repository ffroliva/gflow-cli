"""Unit tests for the branch-naming advisory in the repo-hygiene gate.

`_check_branch_name` is *advisory*: it returns warning strings for a
non-conventional branch but callers must never treat those as exit-1 errors
(see CLAUDE.md / AGENTS.md branch policy + the governance-enforcement plan).
"""

from __future__ import annotations

import pytest

from scripts.ci import check_repo_hygiene as hygiene

VALID_PREFIXES = ["feature", "bugfix", "hotfix", "chore", "docs", "test", "release"]


@pytest.mark.parametrize("prefix", VALID_PREFIXES)
def test_valid_prefix_passes(prefix: str) -> None:
    assert hygiene._check_branch_name(f"{prefix}/some-work") == []


def test_invalid_prefix_warns() -> None:
    out = hygiene._check_branch_name("myfix")
    assert len(out) == 1
    assert "myfix" in out[0]
    # Remediation must name the valid prefixes.
    assert "feature/" in out[0]


def test_detached_head_noops() -> None:
    # `git rev-parse --abbrev-ref HEAD` returns "HEAD" on a detached checkout
    # (the state GitHub Actions uses for pull_request) — must be silent.
    assert hygiene._check_branch_name("HEAD") == []


@pytest.mark.parametrize("branch", ["main", "develop"])
def test_protected_branches_noop(branch: str) -> None:
    assert hygiene._check_branch_name(branch) == []


def test_none_noops() -> None:
    # Unresolvable branch (e.g. git failure) → silent, never raises.
    assert hygiene._check_branch_name(None) == []


def test_claude_automation_branch_is_advisory_not_error() -> None:
    # Claude Code on the web forces claude/* branches; the check warns but the
    # warning is returned separately so main() never fails on it.
    out = hygiene._check_branch_name("claude/some-session")
    assert len(out) == 1
    assert "advisory" in out[0].lower()
