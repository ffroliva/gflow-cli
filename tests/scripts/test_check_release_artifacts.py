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
    else:
        (tmp_path / "docs" / "INDEX.md").write_text("nothing", encoding="utf-8")
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
