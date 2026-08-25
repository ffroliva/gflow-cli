from __future__ import annotations

import pytest

from gflow_cli.config import UiMode
from gflow_cli.flow_selectors.grading import Grade, grade
from gflow_cli.flow_selectors.model import Selector

SEL = Selector(
    key="editor.composer.input",
    surface="editor",
    candidates=("div[data-slate-editor]", "div[role=textbox]"),
)


def test_first_candidate_is_a_hit() -> None:
    assert grade(SEL, 0, match_count=1, observed_mode=UiMode.CLASSIC).grade is Grade.HIT


def test_later_candidate_is_fallback_not_failure() -> None:
    out = grade(SEL, 1, match_count=1, observed_mode=UiMode.CLASSIC)
    assert out.grade is Grade.FALLBACK
    assert out.is_failure is False


def test_multiple_matches_on_a_unique_selector_is_ambiguous() -> None:
    """Drivers call .first, so a second match means gflow clicks the WRONG
    element while a count-based check reports success. SIDEBAR_CLOSE_FALLBACK
    is deliberately unscoped and is the standing candidate for this."""
    unique = Selector(
        key="editor.sidebar.close",
        surface="editor",
        candidates=("button",),
        expect_unique=True,
    )
    out = grade(unique, 0, match_count=2, observed_mode=UiMode.CLASSIC)
    assert out.grade is Grade.AMBIGUOUS
    assert out.is_failure is True


def test_nothing_resolving_is_drift() -> None:
    assert grade(SEL, None, match_count=0, observed_mode=UiMode.CLASSIC).is_failure is True


def test_wrong_mode_makes_a_miss_expected() -> None:
    classic = Selector(
        key="editor.crop_control",
        surface="editor",
        candidates=("button",),
        mode=UiMode.CLASSIC,
    )
    out = grade(classic, None, match_count=0, observed_mode=UiMode.AGENTIC)
    assert out.grade is Grade.EXPECTED_ABSENT
    assert out.is_failure is False


def test_grade_cannot_be_called_without_a_mode() -> None:
    """The false-DRIFT guarantee is enforced by the signature, not a sentinel
    grade: omitting observed_mode is a TypeError, not a silent misgrade."""
    classic = Selector(
        key="editor.crop_control",
        surface="editor",
        candidates=("button",),
        mode=UiMode.CLASSIC,
    )
    with pytest.raises(TypeError):
        grade(classic, None, match_count=0)  # type: ignore[call-arg]
