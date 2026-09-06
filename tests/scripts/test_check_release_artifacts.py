"""check_release_artifacts: the /gflow:release documentation-artifact guard."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ci"))
from check_release_artifacts import find_violations  # noqa: E402


def _repo(tmp_path: Path, version: str = "1.2.3", *, complete: bool = True) -> Path:
    (tmp_path / "src" / "gflow_cli").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{version}"\n', encoding="utf-8"
    )
    (tmp_path / "src" / "gflow_cli" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    footer = (
        f"[Unreleased]: https://x/compare/v{version}...HEAD\n"
        f"[{version}]: https://x/compare/v1.2.2...v{version}\n"
    )
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [Unreleased]\n\n## [{version}] — 2026-01-01\n\n- x\n\n{footer}",
        encoding="utf-8",
    )
    if complete:
        (tmp_path / "docs" / f"LIVE_VERIFICATION_v{version}.md").write_text(
            "evidence", encoding="utf-8"
        )
        (tmp_path / "docs" / "INDEX.md").write_text(
            f"[LIVE_VERIFICATION_v{version}](LIVE_VERIFICATION_v{version}.md)", encoding="utf-8"
        )
        (tmp_path / "docs" / "PROJECT_STATUS.md").write_text(
            f"# Project Status\n\n## Current release\n\n**v{version} — alpha.** shipped.\n",
            encoding="utf-8",
        )
    else:
        (tmp_path / "docs" / "INDEX.md").write_text("nothing", encoding="utf-8")
        (tmp_path / "docs" / "PROJECT_STATUS.md").write_text(
            "# Project Status\n\n## Current release\n\n**v0.0.1 — alpha.** stale.\n",
            encoding="utf-8",
        )
    return tmp_path


@pytest.mark.unit
def test_complete_release_passes(tmp_path: Path) -> None:
    assert find_violations(_repo(tmp_path)) == []


@pytest.mark.unit
def test_missing_live_verification_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, complete=False)
    violations = find_violations(root)
    assert any("LIVE_VERIFICATION_v1.2.3.md missing" in v for v in violations)


@pytest.mark.unit
def test_stale_project_status_current_release_fails(tmp_path: Path) -> None:
    """PROJECT_STATUS.md's "Current release" must name the version being cut.

    That file claims in its own header to be "Updated on every signed tag", and
    nothing enforced it: v0.64.0 was cut with the section still announcing
    v0.63.0 as current, caught only by a human doc-review council. A release
    that ships announcing the wrong version as current is a release-blocking
    documentation defect, so it belongs in the mechanical gate.
    """
    root = _repo(tmp_path)
    (root / "docs" / "PROJECT_STATUS.md").write_text(
        "# Project Status\n\n## Current release\n\n**v1.2.2 — alpha.** the PREVIOUS release.\n",
        encoding="utf-8",
    )
    violations = find_violations(root)
    assert any("PROJECT_STATUS.md" in v for v in violations), violations


@pytest.mark.unit
def test_missing_project_status_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "docs" / "PROJECT_STATUS.md").unlink()
    violations = find_violations(root)
    assert any("PROJECT_STATUS.md" in v for v in violations), violations


@pytest.mark.unit
def test_project_status_version_elsewhere_does_not_satisfy_the_gate(tmp_path: Path) -> None:
    """The version must appear in the "Current release" section specifically.

    Every past release is still named further down the file, so a plain
    whole-file substring search would pass on a completely stale header — the
    exact failure this check exists to catch.
    """
    root = _repo(tmp_path)
    (root / "docs" / "PROJECT_STATUS.md").write_text(
        "# Project Status\n\n## Current release\n\n**v1.2.2 — alpha.** stale header.\n\n"
        "<details><summary>older</summary>\n\n**v1.2.3 — alpha.** buried mention.\n\n</details>\n",
        encoding="utf-8",
    )
    violations = find_violations(root)
    assert any("PROJECT_STATUS.md" in v for v in violations), violations


@pytest.mark.unit
def test_version_mismatch_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "src" / "gflow_cli" / "__init__.py").write_text(
        '__version__ = "9.9.9"\n', encoding="utf-8"
    )
    violations = find_violations(root)
    assert any("__version__" in v for v in violations)


@pytest.mark.unit
def test_stale_footer_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    (root / "CHANGELOG.md").write_text(
        changelog.replace("compare/v1.2.3...HEAD", "compare/v1.2.2...HEAD"), encoding="utf-8"
    )
    violations = find_violations(root)
    assert any("Unreleased" in v for v in violations)


@pytest.mark.unit
def test_index_reference_required(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "docs" / "INDEX.md").write_text("no reference here", encoding="utf-8")
    violations = find_violations(root)
    assert any("INDEX.md" in v for v in violations)


# --- Iron Law: a "Not verified" entry must name an external blocker -----------
#
# Fixtures below are the real v0.69.0 text. Both items were listed as not-verified
# and both were verified within the hour once challenged — neither was blocked.


def _with_ledger(tmp_path: Path, body: str, version: str = "1.2.3") -> Path:
    root = _repo(tmp_path, version)
    (root / "docs" / f"LIVE_VERIFICATION_v{version}.md").write_text(body, encoding="utf-8")
    return root


def test_not_verified_entry_without_a_blocker_fails(tmp_path: Path) -> None:
    """The exact excuse shipped in v0.69.0: shared code offered as a reason not to run."""
    root = _with_ledger(
        tmp_path,
        "# Ledger\n\n## Not verified (recorded, not omitted)\n\n"
        "- The MCP twin of the credits path was exercised offline only; the CLI and MCP\n"
        "  surfaces share `services/credits.py`, so the untested delta is the MCP adapter.\n",
    )
    violations = find_violations(root)
    assert any("blocker" in v.lower() for v in violations), violations


def test_not_verified_entry_naming_a_blocker_passes(tmp_path: Path) -> None:
    """A real external blocker is the one permitted exception."""
    root = _with_ledger(
        tmp_path,
        "# Ledger\n\n## Not verified (recorded, not omitted)\n\n"
        "- 5 of the 19 benchmark tasks are unscored, blocked on the provider's quota of\n"
        "  20 requests per day per model — not on anything in the code.\n",
    )
    assert not any("blocker" in v.lower() for v in find_violations(root))


def test_a_ledger_with_no_not_verified_section_is_fine(tmp_path: Path) -> None:
    root = _with_ledger(tmp_path, "# Ledger\n\n## Runs\n\n- everything ran.\n")
    assert not any("blocker" in v.lower() for v in find_violations(root))


@pytest.mark.parametrize(
    "excuse",
    [
        "covered by unit tests",
        "it is a thin adapter over verified code",
        "offline-tested only",
        "did not get to it this cycle",
    ],
)
def test_known_non_blockers_are_rejected(tmp_path: Path, excuse: str) -> None:
    root = _with_ledger(
        tmp_path,
        f"# Ledger\n\n## Not verified (recorded, not omitted)\n\n- The X path — {excuse}.\n",
    )
    assert any("blocker" in v.lower() for v in find_violations(root)), excuse


def test_untracked_debt_is_rejected(tmp_path: Path) -> None:
    """ "We didn't run it" is not a blocker — parking it silently is the failure mode."""
    root = _with_ledger(
        tmp_path,
        "# Ledger\n\n## Not verified\n\n- DEBT — portrait 9:16 and count > 1 were not run.\n",
    )
    assert any("blocker" in v.lower() for v in find_violations(root))


def test_debt_with_a_tracked_issue_passes(tmp_path: Path) -> None:
    """Debt is acceptable when it is accountable: an issue someone can close."""
    root = _with_ledger(
        tmp_path,
        "# Ledger\n\n## Not verified\n\n"
        "- DEBT — portrait 9:16 and count > 1 were not run. Tracked in #1234.\n",
    )
    assert not any("blocker" in v.lower() for v in find_violations(root))
