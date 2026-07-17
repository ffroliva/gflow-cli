"""Robust agentic↔classic composer mode control.

Flow's composer carries an **Agent toggle** — a ``button[aria-pressed]`` whose
child ``span.content`` holds the (localised) label. ``aria-pressed`` is the
source of truth for the mode:

* ``aria-pressed="false"`` → **classic media mode** — the ``crop_*`` settings
  trigger (:data:`MODE_SWITCH_TRIGGER_SELECTORS`) is present.
* ``aria-pressed="true"``  → **agent mode** — the media panel is gone; an
  ``expand_content`` button appears, and expanding it opens a right-side chat
  sidebar (the classic composer disappears), closed via its ``close`` (X).

This module reads and drives that state in a **locale-invariant** way (via
``aria-pressed`` + the stable ``span.content`` class and Material-Symbols
ligatures — never UI text). It deliberately does **not** consult the ``tune`` /
``apps_spark_2`` ligatures: ``apps_spark_2`` is the "Tools" nav item, present in
BOTH modes, so treating it as an agentic signal is a false positive (the cause
of spurious "forced agentic — not recoverable" aborts).

Validated live 2026-07-17 (``scripts/dev/spike_mode_roundtrip.py``): a full
classic → agent-on → sidebar → close → toggle-off → classic round-trip, with
``aria-pressed`` and ``crop_*`` asserted at every step.

Leaf module: imports only stdlib + structlog (+ Playwright ``Page`` under
``TYPE_CHECKING``), so every transport/driver can reuse it without import cycles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)

# The Agent toggle. ``aria-pressed`` = the mode; ``span.content`` is the stable
# label wrapper (the class is semantic, not a hashed styled-components name).
AGENT_TOGGLE_SELECTOR = "button[aria-pressed]:has(span.content)"

# Classic media panel indicator — the ``crop_*`` mode-switch trigger. Canonical
# for the whole codebase (all 6 ratio icons, ratio-invariant): ``drivers/factory``
# imports THIS tuple — this module stays a leaf, so the dependency points here.
# ``tests/api/transports/test_selector_symmetry.py`` locks the identity.
CROP_SELECTORS: tuple[str, ...] = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_16_9'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_9_16'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_square'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_portrait'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_landscape'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_original'))",
)

# The expanded chat sidebar's close (X), scoped to the sidebar (which also
# carries the ``edit_square`` "new session" affordance) so it never matches an
# unrelated close button elsewhere on the page.
SIDEBAR_CLOSE_SELECTOR = (
    "div:has(button:has(i.google-symbols:text-is('edit_square'))) "
    "button:has(i.google-symbols:text-is('close'))"
)

Mode = Literal["media", "agent", "unknown"]

_SETTLE_MS = 1200
_MAX_STEPS = 4
_CLICK_TIMEOUT_MS = 4000
# Slow in-place panel mounts were historically absorbed by the CALLERS' own
# 4s trigger-probe cascade — the pre-reload grace keeps that tolerance so a
# panel that mounts in 1.3-4s never triggers a needless navigation.
_CROP_GRACE_TIMEOUT_MS = 4000
# Post-reload the SPA mounts the composer well after ``load`` (the agentic
# indicator was observed ~1.25s after navigation; slow loads take longer) —
# poll for ANY composer signal instead of trusting a fixed settle.
_COMPOSER_READY_TIMEOUT_MS = 8000
_POLL_INTERVAL_MS = 250


async def _crop_present(page: Page) -> bool:
    for sel in CROP_SELECTORS:
        # Best-effort probe (parity with the factory detector): a transient
        # locator error on one selector must not abort the whole probe.
        try:
            if await page.locator(sel).count() > 0:
                return True
        except Exception:  # noqa: BLE001  # NOSONAR
            continue
    return False


async def _composer_present(page: Page) -> bool:
    """Any composer signal — crop panel (classic), Agent toggle, or sidebar."""
    if await _crop_present(page):
        return True
    for sel in (AGENT_TOGGLE_SELECTOR, SIDEBAR_CLOSE_SELECTOR):
        try:
            if await page.locator(sel).count() > 0:
                return True
        except Exception:  # noqa: BLE001  # NOSONAR
            continue
    return False


async def _wait_until(page: Page, probe, timeout_ms: int) -> bool:  # type: ignore[no-untyped-def]
    """Poll ``probe(page)`` until true or ``timeout_ms`` elapses (logical time).

    Uses ``page.wait_for_timeout`` for the pacing so test fakes stay
    deterministic (their no-op wait makes the loop spin through instantly).
    """
    waited = 0
    while True:
        if await probe(page):
            return True
        if waited >= timeout_ms:
            return False
        await page.wait_for_timeout(_POLL_INTERVAL_MS)
        waited += _POLL_INTERVAL_MS


async def read_mode(page: Page) -> Mode:
    """Return the current composer mode.

    ``crop_*`` present → ``"media"``. Otherwise the Agent toggle's
    ``aria-pressed`` decides (``true`` → ``"agent"``, ``false`` → ``"media"``).
    ``"unknown"`` only when neither signal is available (e.g. the editor has not
    rendered yet — callers should wait for render before trusting this).
    """
    if await _crop_present(page):
        return "media"
    toggle = page.locator(AGENT_TOGGLE_SELECTOR).first
    if await toggle.count() > 0:
        pressed = await toggle.get_attribute("aria-pressed")
        if pressed == "true":
            return "agent"
        if pressed == "false":
            return "media"
    return "unknown"


async def ensure_media_mode(page: Page, *, allow_reload: bool = False) -> bool:
    """Ensure the composer is in classic media mode; return ``True`` if it acted.

    State-aware and idempotent (a no-op when already in media mode). Closes the
    expanded chat sidebar (X) if open, then toggles the Agent pill OFF **only
    when** ``aria-pressed`` reads ``"true"`` — never a blind click. Bounded loop
    (sidebar → toggle → re-check), plus a grace poll for slow in-place panel
    mounts. Best-effort: logs and returns if the media panel never returns,
    leaving the caller's own probe to fail loudly.

    ``allow_reload=True`` additionally sanctions ONE ``page.reload()`` when a
    real (unforced) toggle click landed but the panel never mounted — the
    2026-07-17 pinned-arm shape: the click persists ``isAgentModeToggled=false``
    server-side and the fresh load both re-rolls the per-load cohort arm and
    mounts the persisted preference. **A reload is a navigation with page-wide
    side effects** (it can re-roll the arm and resurface dismissed overlays), so
    only callers that re-verify the cohort AFTER this returns may opt in — in
    practice ``drivers/factory.get_ui_driver``, which owns the switch→verify
    cycle and runs BEFORE a driver is bound. Mid-flow callers (image/video mode
    switches after driver binding) must keep the default: their cohort is bound
    for the flow's lifetime and must not be re-rolled underneath them.
    """
    acted = False
    persisted_off = False  # a REAL (unforced) toggle click succeeded → server pref persisted
    for round_no in range(2):
        for _ in range(_MAX_STEPS):
            if await _crop_present(page):
                return acted
            # The expanded sidebar suppresses the in-composer toggle → close it first.
            sidebar_x = page.locator(SIDEBAR_CLOSE_SELECTOR).first
            if await sidebar_x.count() > 0:
                await sidebar_x.click(force=True, timeout=_CLICK_TIMEOUT_MS)
                await page.wait_for_timeout(_SETTLE_MS)
                acted = True
                continue
            toggle = page.locator(AGENT_TOGGLE_SELECTOR).first
            if await toggle.count() > 0 and await toggle.get_attribute("aria-pressed") == "true":
                # A REAL click (actionability-checked), never force-first: a forced
                # click can flip the DOM node without firing the React handler that
                # persists ``isAgentModeToggled=false`` server-side (the 2026-07-17
                # both-accounts pin) — force remains only as a last-resort fallback.
                try:
                    await toggle.click(timeout=_CLICK_TIMEOUT_MS)
                    persisted_off = True
                except Exception as exc:  # noqa: BLE001 - fall back, verified below
                    log.warning("mode_control.toggle_click_fallback_force", error=str(exc))
                    # Playwright can raise AFTER the click events dispatched
                    # (post-click instability). Re-read the pill state first: a
                    # blind force click on a now-OFF toggle re-enables agent
                    # mode and re-persists it server-side.
                    if await toggle.get_attribute("aria-pressed") == "true":
                        await toggle.click(force=True, timeout=_CLICK_TIMEOUT_MS)
                await page.wait_for_timeout(_SETTLE_MS)
                acted = True
                continue
            break  # nothing actionable and no crop_* — give up (caller probe fails loudly)
        # Absorb a slow in-place mount before giving up or navigating (the old
        # code delegated this tolerance to the callers' 4s trigger probes).
        if acted and await _wait_until(page, _crop_present, _CROP_GRACE_TIMEOUT_MS):
            return acted
        if round_no == 0 and allow_reload and persisted_off:
            # Real toggle click landed but the classic panel never mounted in
            # place: reload. A fresh load both re-rolls the server's per-load
            # arm AND mounts the now-persisted ``isAgentModeToggled=false``
            # preference. Opt-in only (see the docstring's navigation caveat).
            log.info("mode_control.reload_retry", note="toggle clicked, panel absent — reloading")
            await page.reload()
            # SPA re-mount: wait for a composer signal (either arm) so the
            # round-2 probes and the caller's cohort re-detect see a settled
            # page, not the post-``load`` shell.
            await _wait_until(page, _composer_present, _COMPOSER_READY_TIMEOUT_MS)
        else:
            break  # no reload sanctioned — keep the old single-round give-up
    if not await _crop_present(page):
        log.warning(
            "mode_control.ensure_media_incomplete",
            note="classic media panel not restored after mode-control attempts",
        )
    return acted
