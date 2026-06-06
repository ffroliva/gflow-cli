"""Unit tests for the advisory materiality + traceability classifier.

The classifier mirrors the reference orchestrator's *advisory* behaviour: it
classifies and recommends but NEVER blocks (main always returns 0).
"""

from __future__ import annotations

import pytest

from scripts.ci import check_materiality as mat


# --- classification --------------------------------------------------------
def test_auth_path_is_material() -> None:
    assert mat.is_material("src/gflow_cli/auth/real_chrome.py")


def test_transports_path_is_material() -> None:
    assert mat.is_material("src/gflow_cli/api/transports/ui_automation.py")


@pytest.mark.parametrize(
    "path",
    ["src/gflow_cli/data/repository.py", "src/gflow_cli/api/recaptcha.py"],
)
def test_data_and_recaptcha_material(path: str) -> None:
    assert mat.is_material(path)


def test_docs_only_is_routine() -> None:
    assert not mat.is_material("docs/USAGE.md")


def test_classify_splits_material_and_routine() -> None:
    material, routine = mat.classify(["src/gflow_cli/auth/x.py", "docs/USAGE.md", "README.md"])
    assert material == ["src/gflow_cli/auth/x.py"]
    assert routine == ["docs/USAGE.md", "README.md"]


# --- exit code (advisory) --------------------------------------------------
def test_exit_code_always_zero_with_material(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mat, "touched_paths", lambda base: ["src/gflow_cli/auth/x.py"])
    monkeypatch.setattr(mat, "_commit_messages", lambda base: "")
    assert mat.main(["--base", "origin/develop"]) == 0


def test_exit_code_always_zero_when_routine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mat, "touched_paths", lambda base: ["docs/x.md"])
    monkeypatch.setattr(mat, "_commit_messages", lambda base: "")
    assert mat.main([]) == 0


# --- remediation is tool-agnostic ------------------------------------------
def test_remediation_is_executable_by_non_claude() -> None:
    report = mat.build_report(["src/gflow_cli/auth/x.py"], [], mat.traceability_signals([]))
    # A Claude path AND a non-/gflow path AND a human path must all be present.
    assert "/gflow:predict" in report
    assert "skills/predict/SKILL.md" in report
    assert "human" in report.lower()


# --- traceability signals (report-only) ------------------------------------
def test_plan_reference_signal_from_path() -> None:
    sig = mat.traceability_signals(["docs/superpowers/plans/2026-01-01-x/PLAN.md"])
    assert sig["plan_referenced"] is True


def test_plan_reference_signal_from_commit_message() -> None:
    sig = mat.traceability_signals(["src/x.py"], commit_messages="see superpowers/plans/foo")
    assert sig["plan_referenced"] is True


def test_no_block_when_tests_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # src touched without tests → signal reported, but exit still 0.
    monkeypatch.setattr(mat, "touched_paths", lambda base: ["src/gflow_cli/auth/x.py"])
    monkeypatch.setattr(mat, "_commit_messages", lambda base: "")
    sig = mat.traceability_signals(["src/gflow_cli/auth/x.py"])
    assert sig["src_touched"] is True
    assert sig["tests_touched"] is False
    assert mat.main([]) == 0


# --- single-source sync check ----------------------------------------------
def test_material_list_sync_catches_drift() -> None:
    out = mat._check_material_list_sync("only mentions src/gflow_cli/auth/ and data/")
    assert out  # transports + recaptcha missing → warning
    assert "drifted" in out[0]


def test_material_list_sync_passes_on_real_skill() -> None:
    text = (mat.ROOT / mat.SKILL_REF).read_text(encoding="utf-8")
    assert mat._check_material_list_sync(text) == []
