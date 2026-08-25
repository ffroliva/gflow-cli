from __future__ import annotations

import pytest

from gflow_cli.flow_selectors.model import Selector, Surface


def test_selector_requires_at_least_one_candidate() -> None:
    with pytest.raises(ValueError, match="at least one candidate"):
        Selector(key="editor.x", surface="editor", candidates=())


def test_selector_key_must_be_dotted_lower_snake() -> None:
    with pytest.raises(ValueError, match="dotted lower_snake"):
        Selector(key="Editor Composer", surface="editor", candidates=("div",))


def test_surface_pins_a_viewport_at_or_above_the_breakpoint() -> None:
    """ui_automation.py:117-124 — below 1920x1080 crosses Flow's responsive
    breakpoint and drifts the selectors. A probe that forgets this reports
    false drift, so the model refuses to let it be forgotten."""
    with pytest.raises(ValueError, match="breakpoint"):
        Surface(key="editor", url_template="/x", viewport=(1280, 720))


def test_surface_accepts_the_production_viewport() -> None:
    assert Surface(key="editor", url_template="/x", viewport=(1920, 1080)).viewport == (
        1920,
        1080,
    )


def test_a_wide_but_short_viewport_is_still_rejected() -> None:
    """Tuple comparison would ACCEPT (2560, 720): lexicographically 2560 > 1920
    ends the comparison before height is considered. Verified by execution."""
    with pytest.raises(ValueError, match="breakpoint"):
        Surface(key="editor", url_template="/x", viewport=(2560, 720))
