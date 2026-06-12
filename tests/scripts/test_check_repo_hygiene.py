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


# ---------------------------------------------------------------------------
# Root-doc allowlist (project-health audit, 2026-06): stray top-level *.md / *.py
# files are blocked so shipped-PR reviews and session markers can't accumulate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stray",
    ["PR162_MOVIE_CHARACTER_REVIEW.md", ".continue-here.md", "NOTES.py", "scratch.md"],
)
def test_stray_root_doc_is_flagged(stray: str) -> None:
    out = hygiene._check_root_docs([stray])
    assert len(out) == 1
    assert stray in out[0]
    assert "ROOT_DOC_ALLOWLIST" in out[0]


@pytest.mark.parametrize("allowed", sorted(hygiene.ROOT_DOC_ALLOWLIST))
def test_allowlisted_root_doc_passes(allowed: str) -> None:
    assert hygiene._check_root_docs([allowed]) == []


def test_nested_docs_are_never_flagged() -> None:
    # Files under any directory are out of scope — only the repo root is policed.
    nested = [
        "docs/USAGE.md",
        "docs/superpowers/plans/2026-06-12-issue-174-library-ui-attach/PLAN.md",
        "src/gflow_cli/cli.py",
        "tests/conftest.py",
    ]
    assert hygiene._check_root_docs(nested) == []


def test_non_doc_root_files_are_ignored() -> None:
    # Only *.md / *.py at root are policed; config/data files are out of scope.
    others = ["pyproject.toml", "uv.lock", "docker-compose.yml", "llms.txt", "LICENSE"]
    assert hygiene._check_root_docs(others) == []


def test_real_tree_passes_root_doc_check() -> None:
    # The live tracked tree must already satisfy the allowlist (guards against a
    # regression landing a stray root doc that the gate would then start failing on).
    assert hygiene._check_root_docs(hygiene._git_ls_files()) == []
