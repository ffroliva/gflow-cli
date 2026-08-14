"""Agentic-indicator selector symmetry — factory.py is the single source of truth.

The 4 core agentic ligature probes (``tune`` / ``apps_spark_2`` / ``article_spark``
/ ``edit_square``) were historically triplicated across ``drivers/factory.py``,
``ui_automation_video.AGENTIC_UI_INDICATORS`` and
``ui_automation.AGENT_TUNE_INDICATOR_SELECTOR`` — and had already drifted apart
once. These tests lock the consolidation: both transports must derive their
selectors from the canonical tuple in ``drivers/factory.py`` (the detection
source of truth), never redefine them.
"""

from __future__ import annotations

from gflow_cli.api.transports import ui_automation, ui_automation_video
from gflow_cli.api.transports.drivers.factory import (
    AGENT_TUNE_INDICATOR_SELECTOR,
    AGENTIC_INDICATOR_SELECTORS,
)


def test_canonical_indicators_are_subset_of_video_ui_indicators() -> None:
    # ui_automation_video extends the canonical tuple (composer pill + chat-panel
    # close) but must never drop or diverge from a canonical entry.
    assert set(AGENTIC_INDICATOR_SELECTORS) <= set(ui_automation_video.AGENTIC_UI_INDICATORS)


def test_video_extends_canonical_with_exactly_pill_and_panel_close() -> None:
    extras = set(ui_automation_video.AGENTIC_UI_INDICATORS) - set(AGENTIC_INDICATOR_SELECTORS)
    assert extras == {
        ui_automation_video.COMPOSER_AGENT_TOGGLE_SELECTOR,
        ui_automation_video.AGENT_CHAT_PANEL_CLOSE_SELECTOR,
    }


def test_tune_selector_is_a_canonical_indicator() -> None:
    assert AGENT_TUNE_INDICATOR_SELECTOR in AGENTIC_INDICATOR_SELECTORS


def test_ui_automation_carries_no_tune_selector_copy() -> None:
    # #299 PR-B deleted ui_automation's only tune-selector consumer
    # (_force_agent_mode) and its re-import. The drift hazard the old identity
    # lock guarded is now structural: the module must not grow a local copy.
    assert not hasattr(ui_automation, "AGENT_TUNE_INDICATOR_SELECTOR")


def test_factory_crop_detector_is_mode_control_canonical() -> None:
    # The crop tuples had drifted (mode_control probed 2 icons, factory 6) —
    # the 2026-07-17 pin incident's controller/verify asymmetry. Canonical lives
    # in mode_control (the leaf); factory must import it, never redefine.
    from gflow_cli.api.transports import mode_control
    from gflow_cli.api.transports.drivers import factory

    assert factory._CLASSIC_CROP_SELECTORS is mode_control.CROP_SELECTORS  # noqa: SLF001
    assert len(mode_control.CROP_SELECTORS) == 6
