from __future__ import annotations

from gflow_cli.flow_selectors.grading import Grade, Outcome
from scripts.probe.run_probe import render_report


def test_report_names_the_drifted_selector() -> None:
    body = render_report([Outcome("editor.composer.input", Grade.MISS, None)])
    assert "editor.composer.input" in body
    assert "DRIFT" in body


def test_fallback_names_which_candidate_held() -> None:
    """crop_control has six candidates; "a later one held" is not actionable
    without knowing how far down the cascade the page has drifted."""
    body = render_report([Outcome("editor.crop_control", Grade.FALLBACK, 3)])
    assert "FALLBACK[3]" in body
    assert "DRIFT" not in body


def test_ambiguous_is_reported_as_a_failure() -> None:
    body = render_report([Outcome("editor.agent_toggle", Grade.AMBIGUOUS, 0)])
    assert "AMBIGUOUS" in body
    assert "1 need" in body


def test_alternate_state_gate_has_a_scoped_and_an_unscoped_candidate() -> None:
    """R8. Two guards in one test:

    - the gate must not rely on the edit_square scoping alone — that was #493's
      single point of failure, and a drifted scope would silently disable it
    - the candidates must be DISTINCT. SIDEBAR_CLOSE_SELECTOR and
      AGENT_CHAT_PANEL_CLOSE_SELECTOR are byte-equal aliases, so an earlier
      draft listed one selector twice and certified a label it could not emit.
    """
    from scripts.probe.run_probe import _ALTERNATE_STATE_CANDIDATES

    assert len(set(_ALTERNATE_STATE_CANDIDATES)) == len(_ALTERNATE_STATE_CANDIDATES)
    assert any("edit_square" in c for c in _ALTERNATE_STATE_CANDIDATES)
    assert any("edit_square" not in c for c in _ALTERNATE_STATE_CANDIDATES)


def test_every_grade_has_a_report_label() -> None:
    """A new Grade member must fail HERE, not as a KeyError inside the live
    probe, where it would surface as infrastructure noise instead of a red
    unit test."""
    from scripts.probe.run_probe import _LABEL

    assert set(_LABEL) == set(Grade)


def test_expected_absent_is_visible_but_not_a_failure() -> None:
    """A mode-scoped entry absent on the other arm must still appear, or the
    report silently shrinks and nobody notices coverage was skipped."""
    body = render_report([Outcome("editor.crop_control", Grade.EXPECTED_ABSENT, None)])
    assert "editor.crop_control" in body
    assert "0 need" in body
