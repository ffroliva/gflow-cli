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

This module is the detection source of truth for the AGENTIC indicators:
``AGENTIC_INDICATOR_SELECTORS`` and ``AGENT_TUNE_INDICATOR_SELECTOR`` are
canonical here, and the UI transports (``ui_automation``,
``ui_automation_video``) import them rather than redefining them. The CLASSIC
crop tuple is the one exception: its canonical home is ``mode_control`` (the
leaf module — it may import nothing from ``drivers``, so the dependency points
factory→mode_control for that tuple only).
``tests/api/transports/test_selector_symmetry.py`` locks both directions.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api.transports._common import raise_if_migrated
from gflow_cli.api.transports.drivers.agentic import AgenticFlowUiDriver
from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver
from gflow_cli.api.transports.mode_control import CROP_SELECTORS
from gflow_cli.config import UiMode

if TYPE_CHECKING:
    from collections.abc import Iterable

    from playwright.async_api import Page

    from gflow_cli.api.transports.drivers.base import FlowUiDriver, SupportsSendPrompt

log = structlog.get_logger(__name__)

# Classic media panel — the locale-stable ``crop_*`` aspect/mode trigger (all 6
# ratio icons, ratio-invariant). Canonical tuple lives in ``mode_control`` (the
# leaf module) so both this detector and the mode controller probe the SAME
# panel — they had drifted (2 vs 6 icons) before the 2026-07-17 pin incident.
_CLASSIC_CROP_SELECTORS: tuple[str, ...] = CROP_SELECTORS

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
        # #639: re-read the host every tick. The loop already awaits between ticks,
        # so this is free on the host that works — and it is the first point at
        # which a post-goto redirect to flow.google.com can be seen at all, since
        # the caller's entry check ran before the redirect landed.
        raise_if_migrated(page, at="detect_ui_mode")
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
    ui_mode: UiMode = UiMode.AUTO,
    transport: SupportsSendPrompt | None = None,
) -> FlowUiDriver:
    """Probe the DOM and return the matching :class:`FlowUiDriver`.

    ``transport`` is the live transport (as the narrow
    :class:`SupportsSendPrompt` seam); it is injected into the classic driver at
    construction (``ClassicFlowUiDriver.send_prompt`` delegates to
    ``transport._send_prompt``) instead of being mutated onto the driver after
    the fact. Agentic ignores it. It is optional so the pure detection tests can
    call the factory without a transport.

    ``ui_mode`` (issue #299) is the caller's policy:
      * ``AUTO`` — bind whatever the composer renders.
      * ``CLASSIC`` — attempt to recover the classic composer (best-effort exit
        of the agentic chat), then, if the arm is STILL agentic, raise
        ``UiModeUnavailableError`` (exit 28) **before** any generation — zero
        credits. The DOM probe is the authority; the arm flaps per load, so a
        re-run often lands classic.
      * ``AGENTIC`` — switch the composer to agentic, verify, and raise
        ``UiModeUnavailableError`` if the arm can't be reached.

    Call per generation — the cohort flaps per page load, so a cached driver
    goes stale on the next navigation / batch item.
    """
    # #639: the migrated flow.google.com frontend renders none of the controls
    # gflow drives, so every probe below is doomed before it starts. Finding that
    # out the slow way costs ~54 s per attempt -- and because the rollout flaps and
    # callers retry on exit 36, that is paid on every attempt of a retry loop.
    #
    # This entry check catches the case where the page ALREADY sits on the migrated
    # origin. It is deliberately not the only one: the hop to flow.google.com is a
    # post-goto redirect nobody waits for, so on a fresh project navigation this
    # reads a pre-redirect URL and declines. The re-checks in detect_ui_mode and
    # _exit_agent_mode are what actually catch the field case.
    raise_if_migrated(page, at="get_ui_driver")

    # Prerequisite switch: when a specific arm is required and the current one
    # differs, attempt the DOM toggle for that direction (best-effort — the
    # server can pin the arm). The re-probe below VERIFIES whether it took.
    if ui_mode is UiMode.CLASSIC:
        from gflow_cli.api.transports.ui_automation_video import (
            VideoGenerationMixin,
        )
        from gflow_cli.errors import FlowAgentUiError, FlowHostMigratedError

        try:
            log.info("ui_driver.ui_mode.attempt_exit_agent")
            # allow_reload: this is the ONE sanctioned reload site — it runs
            # BEFORE any driver is bound and the re-probe below re-verifies the
            # cohort after any navigation (mode_control docstring caveat).
            await VideoGenerationMixin._exit_agent_mode(  # type: ignore[reportPrivateUsage]
                page, allow_reload=True
            )
        except FlowAgentUiError as exc:
            # Expected: a server-pinned agentic cohort cannot be exited
            # client-side. The verify check below turns this into a clean abort.
            log.info("ui_driver.ui_mode.cohort_natively_agentic", detail=str(exc))
        except FlowHostMigratedError:
            # #639: not a failed switch — the host cannot be driven at all. The
            # blanket handler below would demote it to a warning and carry on into
            # exactly the probing the bail exists to skip.
            raise
        except Exception as exc:
            log.warning("ui_driver.ui_mode.exit_agent_failed", error=str(exc))
    elif ui_mode is UiMode.AGENTIC:
        # #299 PR-B: delegate to the symmetric mode_control primitive (real
        # click + aria-pressed verification) instead of the transport's old
        # tune-ligature force-clicker. mode_control is a leaf — no cycle.
        from gflow_cli.api.transports.mode_control import ensure_agent_mode

        # #639: symmetric with the CLASSIC arm above. `ensure_agent_mode` opens with
        # an ~8 s composer-ready poll that `mode_control` cannot guard on its own (it
        # is a leaf and knows nothing about hosts), so without this the AGENTIC arm
        # reaches the same verdict ~8 s later than CLASSIC.
        # OUTSIDE the try on purpose: the blanket handler below would demote the
        # abort to a warning and carry on, which is the exact failure the CLASSIC arm
        # needs an `except FlowHostMigratedError: raise` carve-out to avoid.
        raise_if_migrated(page, at="ensure_agent_mode")
        try:
            log.info("ui_driver.ui_mode.attempt_force_agent")
            await ensure_agent_mode(page)
        except Exception as exc:  # noqa: BLE001 - best-effort switch, verified below
            log.warning("ui_driver.ui_mode.force_agent_failed", error=str(exc))

    # Verify: re-probe the DOM ground truth after any switch attempt.
    from gflow_cli.errors import UiModeUnavailableError

    mode = await detect_ui_mode(page, timeout_s=timeout_s, poll_interval_s=poll_interval_s)
    log.info("ui_driver.bound", mode=mode, ui_mode=ui_mode.value)
    if mode == "agentic":
        if ui_mode is UiMode.CLASSIC:
            raise UiModeUnavailableError(UiMode.CLASSIC)
        return AgenticFlowUiDriver()
    # classic rendered
    if ui_mode is UiMode.AGENTIC:
        raise UiModeUnavailableError(UiMode.AGENTIC)
    return ClassicFlowUiDriver(transport=transport)
