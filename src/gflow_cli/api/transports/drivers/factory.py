"""Runtime DOM probe → bind the matching FlowUiDriver strategy.

Detection is the only reliable cohort signal: the client-visible state
(localStorage, JS cookies) is byte-identical across cohorts, so there is no
pre-navigation flag to read (docs/AGENT_UI_RECON.md § "Gating mechanism"). The
rule, validated by live capture:

  * **classic** — the locale-stable ``crop_*`` media trigger is present.
  * **agentic** — ``crop_*`` is absent AND an agentic indicator ligature
    (``tune`` / ``apps_spark_2`` / ``article_spark`` / ``edit_square``) is present.
  * **default** — classic (the safe, established path) when neither matches
    (e.g. mid-load or an unrecognised shape).

The cohort flaps per page load, so callers must re-probe **per generation** —
never cache a driver across navigations.

This module is the detection source of truth: ``AGENTIC_INDICATOR_SELECTORS``
and ``AGENT_TUNE_INDICATOR_SELECTOR`` are canonical here, and the UI transports
(``ui_automation``, ``ui_automation_video``) import them rather than redefining
them (the transport depends on ``drivers``, not the reverse —
``tests/api/transports/test_selector_symmetry.py`` locks this).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api.transports.drivers.agentic import AgenticFlowUiDriver
from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver

if TYPE_CHECKING:
    from collections.abc import Iterable

    from playwright.async_api import Page

    from gflow_cli.api.transports.drivers.base import FlowUiDriver

log = structlog.get_logger(__name__)

# Classic media panel — the locale-stable ``crop_*`` aspect/mode trigger. Its
# presence means the composer is in classic media mode (all 6 ratio icons are
# enumerated so the probe is ratio-invariant).
_CLASSIC_CROP_SELECTORS: tuple[str, ...] = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_16_9'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_9_16'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_square'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_portrait'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_landscape'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_original'))",
)

# Agentic cohort indicators — Material Symbols ligatures unique to the chat UI.
# Locale-invariant (icon names, not UI text). Only consulted when no ``crop_*``
# trigger is present. Canonical: the UI transports derive their agentic probes
# from this tuple instead of carrying their own copies.
AGENT_TUNE_INDICATOR_SELECTOR = "i.google-symbols:text-is('tune')"

AGENTIC_INDICATOR_SELECTORS: tuple[str, ...] = (
    AGENT_TUNE_INDICATOR_SELECTOR,
    "i.google-symbols:text-is('apps_spark_2')",
    "i.google-symbols:text-is('article_spark')",
    "i.google-symbols:text-is('edit_square')",
)


async def _any_present(page: Page, selectors: Iterable[str]) -> bool:
    """True if any selector matches at least one element.

    A locator failure on one selector is swallowed so a transient DOM error
    never aborts detection — the next selector (and the safe default) still run.
    """
    for sel in selectors:
        try:
            if await page.locator(sel).count() > 0:
                return True
        # Best-effort probe: swallow any locator error so one bad selector never
        # aborts detection — the next selector (and the safe default) still run.
        except Exception:  # noqa: BLE001  # NOSONAR
            continue
    return False


# The composer renders a beat after the project page loads, so an instant probe
# races the render and wrongly defaults to classic (the agentic ``tune`` indicator
# was observed ~1.25 s after navigation in live e2e). Poll until a signal appears,
# then fall back to classic only if neither shows within the window.
_DETECT_TIMEOUT_S = 8.0
_DETECT_POLL_INTERVAL_S = 0.4


async def detect_ui_mode(
    page: Page,
    *,
    timeout_s: float | None = None,
    poll_interval_s: float | None = None,
) -> str:
    """Classify the live composer as ``"classic"`` or ``"agentic"``.

    Polls the DOM until a signal appears: classic wins whenever ``crop_*`` is
    present (encodes the recon rule that agentic requires the *absence* of the
    media trigger); agentic wins on an indicator ligature. Returns as soon as a
    signal is found. Falls back to classic only if neither appears within
    ``timeout_s`` (the classic path then surfaces a clean ``FlowAgentUiError`` if
    the cohort really is agentic but slow to render).

    ``timeout_s`` / ``poll_interval_s`` default to the module constants resolved
    at call time (``None`` sentinel) so tests can patch the constants to skip the
    poll window without touching production behaviour.
    """
    timeout_s = _DETECT_TIMEOUT_S if timeout_s is None else timeout_s
    poll_interval_s = _DETECT_POLL_INTERVAL_S if poll_interval_s is None else poll_interval_s
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        if await _any_present(page, _CLASSIC_CROP_SELECTORS):
            return "classic"
        if await _any_present(page, AGENTIC_INDICATOR_SELECTORS):
            return "agentic"
        if asyncio.get_event_loop().time() >= deadline:
            return "classic"
        await asyncio.sleep(poll_interval_s)


async def get_ui_driver(
    page: Page,
    *,
    timeout_s: float | None = None,
    poll_interval_s: float | None = None,
    prefer_classic: bool = False,
) -> FlowUiDriver:
    """Probe the DOM and return the matching :class:`FlowUiDriver`.

    Call per generation — the cohort flaps per page load, so a cached driver
    goes stale on the next navigation / batch item.
    """
    if prefer_classic:
        from gflow_cli.api.transports.ui_automation_video import (
            VideoGenerationMixin,
        )
        from gflow_cli.errors import FlowAgentUiError

        try:
            log.info("ui_driver.prefer_classic.attempt_exit_agent")
            await VideoGenerationMixin._exit_agent_mode(page)  # type: ignore[reportPrivateUsage]
        except FlowAgentUiError as exc:
            # Expected: the server-gated agentic ("tune") cohort cannot be exited
            # client-side. prefer_classic is best-effort (see config docstring), so
            # falling through to the agentic driver is normal, not a fault.
            log.info("ui_driver.prefer_classic.cohort_natively_agentic", detail=str(exc))
        except Exception as exc:
            log.warning("ui_driver.prefer_classic.exit_agent_failed", error=str(exc))

    mode = await detect_ui_mode(page, timeout_s=timeout_s, poll_interval_s=poll_interval_s)
    log.info("ui_driver.bound", mode=mode)
    if mode == "agentic":
        return AgenticFlowUiDriver()
    return ClassicFlowUiDriver()
