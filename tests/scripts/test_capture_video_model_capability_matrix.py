"""Tests for the live capability matrix collector.

The DOM scrape runs in the browser and cannot be exercised offline. What CAN be
checked offline is the thing that actually decides whether a capture is
trustworthy: that the collector's stray probe mirrors the transport cascades it
claims to replay.

That matters because the probe's result is a **kill condition** — empty stray
lists close issue #657 and task 7 of the duration plan without any code being
written. A probe that under-reports deletes work that was needed. The first cut
of this file asserted only that certain substrings appeared in the source, which
passed against unmodified `develop` and caught none of that.
"""

from __future__ import annotations

import inspect
import re

from gflow_cli.api.transports import ui_automation_video as transport
from scripts.dev import capture_video_model_capability_matrix as collector
from scripts.dev.capture_video_model_capability_matrix import _classify

ROLE_RE = re.compile(r"\[role='([a-z]+)'\]|(?<![\w'\-])(button)(?=:)")


def _roles(source: str) -> set[str]:
    """Every element role a cascade probes, from its source text."""
    return {a or b for a, b in ROLE_RE.findall(source)}


def _probe_roles(js_const: str) -> set[str]:
    """Roles listed in one of the probe's JS role constants."""
    block = re.search(rf"const {js_const} = \[(.*?)\];", _menu_state_src(), re.DOTALL)
    assert block, f"{js_const} not found in the probe"
    return {a or b for a, b in ROLE_RE.findall(block.group(1))}


def _menu_state_src() -> str:
    return inspect.getsource(collector._menu_state)


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


def test_duration_probe_covers_every_role_the_transport_probes() -> None:
    """A role the transport clicks but the probe skips is a silent false negative."""
    transport_roles = _roles(
        inspect.getsource(transport.VideoGenerationMixin._select_video_duration)
    )
    assert transport_roles, "failed to extract the transport's duration roles"
    assert transport_roles <= _probe_roles("DURATION_ROLES")


def test_count_probe_mirrors_the_transport_exactly() -> None:
    """Count is NOT the same cascade as duration — it probes only [role='tab'].

    Sharing one role list with duration both over-reported (flagging buttons the
    transport can never click, inventing work) and, with the label bug below,
    under-reported.
    """
    transport_roles = _roles(inspect.getsource(transport.VideoGenerationMixin._set_output_count))
    assert transport_roles == {"tab"}, f"transport count roles changed: {transport_roles}"
    assert _probe_roles("COUNT_ROLES") == {"tab"}


def test_count_probe_scans_both_affix_orders() -> None:
    """#404 renamed the count tabs `1x` -> `x1` and the transport probes BOTH.

    Scanning only `x{n}` misses a visible `2x` outside the popover — clickable by
    the transport, invisible to the probe. This is the exact bug the first cut
    shipped, and it is the under-reporting direction that wrongly closes #657.
    """
    assert 'labels = (f"x{n}", f"{n}x")' in inspect.getsource(
        transport.VideoGenerationMixin._set_output_count
    ), "transport affix handling changed — re-check the probe"
    src = _menu_state_src()
    assert "'x' + n" in src and "n + 'x'" in src, "probe must scan both affix orders"


def test_tab_scrape_never_falls_back_to_the_whole_page() -> None:
    """A capability claim must come from the popover, not from `document.body`.

    With the widened selector (#650), a popover that failed to open would scrape
    every button on the page and any stray "8s" would read as a duration tab --
    the instrument manufacturing the positive it exists to measure.
    """
    src = _menu_state_src()
    assert "const tabScope = menu;" in src
    assert "tabScope === null" in src, "tab scrape must yield [] when no menu is open"


def test_strays_reach_every_per_model_row() -> None:
    """The kill condition is "empty across EVERY model", so a baseline-only
    sample cannot answer it. Both the selected-model row and the picker-miss row
    must carry the probe output."""
    src = inspect.getsource(collector)
    for field in ("menu_present", "duration_strays", "count_strays"):
        # once in the JS payload, once per row shape (selected + picker-miss)
        assert src.count(f'"{field}"') >= 2, f"{field} does not reach the per-model rows"


def test_classify_handles_an_unopened_popover() -> None:
    """`tabScope = menu` yields [] when no menu opened; nothing may explode."""
    assert _classify([]) == {"duration": [], "count": [], "aspect": [], "other": []}
