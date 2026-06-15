"""Task 1 — unit tests for the FlowUiDriver factory + DOM-cohort detection.

The factory probes the live DOM to pick the matching driver strategy. The
detection rule is the one validated by live capture (docs/AGENT_UI_RECON.md):

  * classic   → the locale-stable ``crop_*`` media trigger is present.
  * agentic   → ``crop_*`` is absent AND an agentic indicator (``tune`` /
                ``apps_spark_2`` / ``article_spark`` / ``edit_square``) is present.
  * default   → classic (the safe, established path) when neither matches.

The cohort flaps per page load, so detection must run per generation — these
tests pin the pure-detection contract the per-generation binding relies on.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.drivers.agentic import AgenticFlowUiDriver
from gflow_cli.api.transports.drivers.base import FlowUiDriver
from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver
from gflow_cli.api.transports.drivers.factory import (
    _AGENTIC_INDICATOR_SELECTORS,
    _CLASSIC_CROP_SELECTORS,
    detect_ui_mode,
    get_ui_driver,
)

_CROP = _CLASSIC_CROP_SELECTORS[0]
_TUNE = _AGENTIC_INDICATOR_SELECTORS[0]


def _fake_page(present: set[str], *, raising: set[str] | None = None) -> MagicMock:
    """Page whose ``locator(sel).count()`` returns >0 only for ``present``.

    Selectors in ``raising`` raise from ``count()`` so the swallow-and-continue
    robustness path is exercised.
    """
    raising = raising or set()

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        if sel in raising:
            loc.count = AsyncMock(side_effect=RuntimeError("locator boom"))
        else:
            loc.count = AsyncMock(return_value=1 if sel in present else 0)
        return loc

    page = MagicMock()
    page.locator = MagicMock(side_effect=_locator)
    return page


# ---------------------------------------------------------------------------
# detect_ui_mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_classic_when_crop_present() -> None:
    assert await detect_ui_mode(_fake_page({_CROP})) == "classic"


@pytest.mark.asyncio
async def test_detect_agentic_when_crop_absent_and_indicator_present() -> None:
    assert await detect_ui_mode(_fake_page({_TUNE})) == "agentic"


@pytest.mark.asyncio
async def test_detect_classic_default_when_neither_present() -> None:
    # Empty DOM (mid-load, or an unrecognised shape) falls back to the safe path.
    # timeout_s=0 so the poll loop returns immediately instead of waiting the window.
    assert await detect_ui_mode(_fake_page(set()), timeout_s=0.0) == "classic"


@pytest.mark.asyncio
async def test_detect_agentic_after_delayed_render() -> None:
    # The composer renders a beat after navigation — the agentic indicator is
    # absent on the first probe and appears on a later poll. detect_ui_mode must
    # poll (not instant-default to classic). Regression guard for the e2e bug
    # where get_ui_driver raced the render and wrongly bound classic.
    calls = {"n": 0}

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        if sel == _TUNE:
            # Absent on the first two probes, present from the third.
            def _count() -> int:
                calls["n"] += 1
                return 0 if calls["n"] < 3 else 1

            loc.count = AsyncMock(side_effect=_count)
        else:
            loc.count = AsyncMock(return_value=0)
        return loc

    page = MagicMock()
    page.locator = MagicMock(side_effect=_locator)
    assert await detect_ui_mode(page, timeout_s=5.0, poll_interval_s=0.01) == "agentic"


@pytest.mark.asyncio
async def test_detect_classic_wins_when_both_present() -> None:
    # Encodes the recon rule: agentic requires the ABSENCE of crop_*. If both
    # are somehow present, crop_* short-circuits to classic.
    assert await detect_ui_mode(_fake_page({_CROP, _TUNE})) == "classic"


@pytest.mark.asyncio
async def test_detect_tolerates_locator_errors() -> None:
    # A transient locator failure must not abort detection — it falls through to
    # the next selector / the safe default.
    page = _fake_page({_TUNE}, raising={_CROP})
    assert await detect_ui_mode(page) == "agentic"


# ---------------------------------------------------------------------------
# get_ui_driver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ui_driver_returns_classic() -> None:
    driver = await get_ui_driver(_fake_page({_CROP}))
    assert isinstance(driver, ClassicFlowUiDriver)
    assert isinstance(driver, FlowUiDriver)
    assert driver.name == "classic"


@pytest.mark.asyncio
async def test_get_ui_driver_returns_agentic() -> None:
    driver = await get_ui_driver(_fake_page({_TUNE}))
    assert isinstance(driver, AgenticFlowUiDriver)
    assert isinstance(driver, FlowUiDriver)
    assert driver.name == "agentic"


@pytest.mark.asyncio
async def test_get_ui_driver_default_is_classic() -> None:
    driver = await get_ui_driver(_fake_page(set()), timeout_s=0.0)
    assert isinstance(driver, ClassicFlowUiDriver)
