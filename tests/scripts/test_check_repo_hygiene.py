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


# ---------------------------------------------------------------------------
# Version agreement (chore/ci-hardening):
# pyproject == __init__ == plugin.json == uv.lock
# ---------------------------------------------------------------------------


def _version_tree(tmp_path, pyproject: str, init: str, plugin: str, lock: str | None = None):
    import json as _json

    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{pyproject}"\n', encoding="utf-8"
    )
    (tmp_path / "src" / "gflow_cli").mkdir(parents=True)
    (tmp_path / "src" / "gflow_cli" / "__init__.py").write_text(
        f'__version__ = "{init}"\n', encoding="utf-8"
    )
    (tmp_path / ".codex-plugin").mkdir()
    (tmp_path / ".codex-plugin" / "plugin.json").write_text(
        _json.dumps({"version": plugin}), encoding="utf-8"
    )
    # Realistic uv.lock: the editable package block sits among other packages
    # whose own `version = "..."` lines must not be picked up by the gate.
    (tmp_path / "uv.lock").write_text(
        "[[package]]\n"
        'name = "click"\n'
        'version = "8.9.9"\n'
        "\n"
        "[[package]]\n"
        'name = "gflow-cli"\n'
        f'version = "{pyproject if lock is None else lock}"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    return tmp_path


def test_version_agreement_passes_when_identical(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hygiene, "ROOT", _version_tree(tmp_path, "1.2.3", "1.2.3", "1.2.3"))
    assert hygiene._check_version_agreement() == []


def test_version_agreement_fails_on_any_disagreement(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hygiene, "ROOT", _version_tree(tmp_path, "1.2.3", "1.2.3", "1.2.4"))
    errors = hygiene._check_version_agreement()
    assert len(errors) == 1
    assert "disagreement" in errors[0]
    assert "1.2.4" in errors[0]


def test_version_agreement_fails_when_only_uv_lock_drifts(tmp_path, monkeypatch) -> None:
    """A bump that forgets `uv lock` is the drift this gate was added for."""
    monkeypatch.setattr(
        hygiene, "ROOT", _version_tree(tmp_path, "1.2.3", "1.2.3", "1.2.3", lock="1.2.2")
    )
    errors = hygiene._check_version_agreement()
    assert len(errors) == 1
    assert "uv.lock=1.2.2" in errors[0]


def test_version_agreement_ignores_other_packages_in_uv_lock(tmp_path, monkeypatch) -> None:
    """The gate must anchor on gflow-cli, not the first `version =` it sees."""
    root = _version_tree(tmp_path, "1.2.3", "1.2.3", "1.2.3")
    assert '"8.9.9"' in (root / "uv.lock").read_text(encoding="utf-8")
    monkeypatch.setattr(hygiene, "ROOT", root)
    assert hygiene._check_version_agreement() == []


def test_version_agreement_flags_uv_lock_without_own_package_block(tmp_path, monkeypatch) -> None:
    """A uv.lock missing the editable entry must fail loudly, not pass silently."""
    root = _version_tree(tmp_path, "1.2.3", "1.2.3", "1.2.3")
    (root / "uv.lock").write_text(
        '[[package]]\nname = "click"\nversion = "8.9.9"\n', encoding="utf-8"
    )
    monkeypatch.setattr(hygiene, "ROOT", root)
    errors = hygiene._check_version_agreement()
    assert len(errors) == 1
    assert "uv.lock" in errors[0]


def test_version_agreement_reports_missing_source_gracefully(tmp_path, monkeypatch) -> None:
    """A missing file is a readable one-line error, never a traceback."""
    monkeypatch.setattr(hygiene, "ROOT", tmp_path)  # empty tree
    errors = hygiene._check_version_agreement()
    assert len(errors) == 1
    assert "could not read" in errors[0]


def test_version_agreement_real_tree_agrees() -> None:
    """The actual repo must always be in agreement (the release gate)."""
    assert hygiene._check_version_agreement() == []
