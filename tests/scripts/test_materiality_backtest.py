"""Unit tests for the materiality backtest harness (pure functions only)."""

from __future__ import annotations

from scripts.dev import materiality_backtest as bt


# --- substantive-diff detection --------------------------------------------
def test_code_change_is_substantive() -> None:
    diff = "--- a/x.py\n+++ b/x.py\n+    return token.refresh()\n-    return None\n"
    assert bt.is_substantive_diff(diff) is True


def test_comment_only_is_not_substantive() -> None:
    diff = "--- a/x.py\n+++ b/x.py\n+# clarify the retry semantics\n-# old note\n"
    assert bt.is_substantive_diff(diff) is False


def test_blank_and_rename_only_is_not_substantive() -> None:
    diff = "similarity index 100%\nrename from a/old.py\nrename to a/new.py\n+\n-\n"
    assert bt.is_substantive_diff(diff) is False


def test_mixed_comment_and_code_is_substantive() -> None:
    diff = "+# a comment\n+    x = compute()\n"
    assert bt.is_substantive_diff(diff) is True


# --- commit-kind classification --------------------------------------------
def test_classify_fix() -> None:
    assert bt.classify_commit_kind("fix(auth): refresh expired token") == "fix"


def test_classify_revert() -> None:
    assert bt.classify_commit_kind('Revert "feat: add transport"') == "revert"


def test_classify_feature_and_other() -> None:
    assert bt.classify_commit_kind("feat: new scene command") == "feature"
    assert bt.classify_commit_kind("chore: bump deps") == "other"
    assert bt.classify_commit_kind("docs: tidy readme") == "other"


# --- aggregation (pure) ----------------------------------------------------
def _rec(sha: str, kind: str, material: bool, substantive: bool) -> bt.Record:
    return bt.Record(sha, f"{kind}: subj", kind, material, substantive)


def test_summarize_false_positive_and_coverage() -> None:
    records = [
        _rec("a", "feature", True, True),  # material, real change
        _rec("b", "chore", True, False),  # material, trivial -> false positive
        _rec("c", "fix", True, True),  # fix in material -> covered
        _rec("d", "fix", False, False),  # fix outside material -> gap
        _rec("e", "other", False, False),  # routine
    ]
    m = bt.summarize(records)
    assert m["total"] == 5
    assert m["material_count"] == 3
    assert m["false_positive_count"] == 1  # only 'b'
    assert m["false_positive_pct"] == round(100 / 3, 1)
    assert m["fix_count"] == 2  # c, d
    assert m["fix_in_material_count"] == 1  # c
    assert m["coverage_pct"] == 50.0
    assert [r.sha for r in m["coverage_gap_examples"]] == ["d"]


def test_summarize_empty_is_safe() -> None:
    m = bt.summarize([])
    assert m["total"] == 0
    assert m["false_positive_pct"] == 0.0
    assert m["coverage_pct"] == 0.0


def test_build_report_contains_both_axes() -> None:
    report = bt.build_report(bt.summarize([_rec("a", "fix", True, True)]), "HEAD")
    assert "Axis 1" in report and "Axis 2" in report
    assert "false-positive rate" in report.lower()
