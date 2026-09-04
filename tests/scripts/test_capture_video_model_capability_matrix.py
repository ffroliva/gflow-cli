"""Tests for the live capability matrix collector.

The DOM scrape itself runs in the browser and cannot be exercised offline, so
the two invariants that decide whether a capture can be TRUSTED are pinned here
as source guards instead: the tab scrape must not fall back to the whole page,
and the stray probe must report what it found.
"""

import inspect

from scripts.dev import capture_video_model_capability_matrix as collector
from scripts.dev.capture_video_model_capability_matrix import _classify


def test_classify_detects_interactive_duration_labels() -> None:
    result = _classify(
        [
            {"label": "4s"},
            {"label": "6s"},
            {"label": "8s"},
            {"label": "x1"},
            {"label": "16:9"},
        ]
    )
    assert result["duration"] == ["4s", "6s", "8s"]
    assert result["count"] == ["x1"]
    assert result["aspect"] == ["16:9"]


def test_tab_scrape_never_falls_back_to_the_whole_page() -> None:
    """A capability claim must come from the popover, not from `document.body`.

    With the widened selector, a popover that failed to open would otherwise
    scrape every button on the page, and any stray "8s" would read as a
    duration tab -- the instrument manufacturing the positive it is supposed to
    measure. `menu_present` records which happened.
    """
    src = inspect.getsource(collector._menu_state)
    assert "const tabScope = menu;" in src
    assert "tabScope === null" in src, "tab scrape must yield [] when no menu is open"
    assert "menu_present:" in src, "a capture must say whether the popover was open"


def test_stray_probe_reports_both_cascades() -> None:
    """The $0 kill-condition probe for the duration and count cascades.

    Both are unscoped `.first` probes over the same five roles, so both need the
    same evidence before anyone writes scoping or read-back code.
    """
    src = inspect.getsource(collector._menu_state)
    assert "duration_strays:" in src
    assert "count_strays:" in src
    assert "menu.contains(el)" in src, "strays are matches OUTSIDE the open menu"
