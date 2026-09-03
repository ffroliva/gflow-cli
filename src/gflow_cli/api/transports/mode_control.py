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

from gflow_cli.api.transports._common import raise_if_migrated

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from playwright.async_api import Locator, Page

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

# Last-resort sidebar close, used ONLY from the demonstrably stuck state (issue
# #493): no ``crop_*`` trigger AND no Agent pill. Reproduced live 2026-08-14 —
# expanding the chat sidebar removes the classic composer entirely, which is
# exactly the reported fingerprint ("no crop_* settings button" + "the Agente
# pill matches neither selector").
#
# The primary selector above is scoped to the sidebar's ``edit_square`` ("new
# session") affordance. That scoping is a single point of failure: a cohort
# whose sidebar lacks that ligature never finds the X, so the sidebar never
# closes, the composer never returns, and the run dies with exit 23.
#
# Deliberately unscoped — safe *only* because the guard state has no composer
# left to mis-click: the classic panel and the pill are both gone.
SIDEBAR_CLOSE_FALLBACK_SELECTOR = "button:has(i.google-symbols:text-is('close'))"

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
# The sanctioned reload must be bounded: a bare ``page.reload()`` rides
# Playwright's 30s default navigation timeout OUTSIDE every budget above
# (#299 PR-B, code-review finding).
_RELOAD_TIMEOUT_MS = 15_000


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


async def _wait_until(
    page: Page, probe: Callable[[Page], Awaitable[bool]], timeout_ms: int
) -> bool:
    """Poll ``probe(page)`` until true or ``timeout_ms`` elapses (logical time).

    Uses ``page.wait_for_timeout`` for the pacing so test fakes stay
    deterministic (their no-op wait makes the loop spin through instantly).

    #639: the host is re-read every tick. This is the longest wait on the doomed
    migrated-origin path — three call sites poll ``_composer_present`` for 8 s,
    and on ``flow.google.com`` it can never become true — so without this an
    ``--ui-mode agentic`` run, or a redirect that lands *during*
    ``ensure_media_mode``, pays the whole window before anything notices the
    origin changed. The callers' own guards are point-in-time snapshots taken
    before this loop starts; they cannot see a flip that happens inside it.
    """
    waited = 0
    while True:
        raise_if_migrated(page, at="mode_control")
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
    # The editor renders a beat after navigation — probing the blank shell reads
    # as "nothing actionable" and the whole recovery no-ops in milliseconds
    # WITHOUT ever reaching the toggle (live-verified on the pinned arm,
    # docs/LIVE_VERIFICATION_v0.38.1.md). Absorb the render race first.
    await _wait_until(page, _composer_present, _COMPOSER_READY_TIMEOUT_MS)
    acted = False
    persisted_off = False  # a REAL (unforced) toggle click succeeded → server pref persisted
    for round_no in range(2):
        stepped, persisted = await _run_dismiss_steps(page)
        acted = acted or stepped
        persisted_off = persisted_off or persisted
        if await _crop_present(page):
            return acted
        # Absorb a slow in-place mount before giving up or navigating (the old
        # code delegated this tolerance to the callers' 4s trigger probes).
        if acted and await _wait_until(page, _crop_present, _CROP_GRACE_TIMEOUT_MS):
            return acted
        if round_no == 0 and allow_reload and persisted_off:
            await _reload_for_persisted_pref(page)
        else:
            break  # no reload sanctioned — keep the old single-round give-up
    log.warning(
        "mode_control.ensure_media_incomplete",
        note="classic media panel not restored after mode-control attempts",
    )
    return acted


async def _try_fallback_sidebar_close(page: Page) -> bool:
    """Close the chat sidebar when the scoped selector missed (issue #493).

    Only reachable from the stuck state: no ``crop_*`` (checked by the caller's
    loop head) and no Agent pill (checked at the call site). With the classic
    composer gone there is nothing else a ``close`` button could belong to, so
    an unscoped match is safe here and nowhere else.

    Returns whether a click landed.
    """
    x = page.locator(SIDEBAR_CLOSE_FALLBACK_SELECTOR).first
    if await x.count() == 0:
        return False
    try:
        await x.click(force=True, timeout=_CLICK_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001 - best-effort rescue
        log.warning("mode_control.fallback_sidebar_close_failed", error=str(exc)[:120])
        return False
    await page.wait_for_timeout(_SETTLE_MS)
    log.info("mode_control.fallback_sidebar_close", note="scoped selector missed (#493)")
    return True


async def _run_dismiss_steps(page: Page) -> tuple[bool, bool]:
    """One bounded pass of (sidebar → toggle → re-check) steps.

    Returns ``(acted, persisted_off)``: whether anything was clicked, and
    whether a REAL (unforced) toggle click landed (→ server pref persisted).
    """
    acted = False
    persisted_off = False
    for _ in range(_MAX_STEPS):
        if await _crop_present(page):
            break
        # The expanded sidebar suppresses the in-composer toggle → close it first.
        sidebar_x = page.locator(SIDEBAR_CLOSE_SELECTOR).first
        if await sidebar_x.count() > 0:
            await sidebar_x.click(force=True, timeout=_CLICK_TIMEOUT_MS)
            await page.wait_for_timeout(_SETTLE_MS)
            acted = True
            continue
        toggle = page.locator(AGENT_TOGGLE_SELECTOR).first
        if await toggle.count() == 0 and await _try_fallback_sidebar_close(page):
            acted = True
            continue
        if await toggle.count() > 0 and await toggle.get_attribute("aria-pressed") == "true":
            persisted_off = await _click_toggle_off(toggle) or persisted_off
            await page.wait_for_timeout(_SETTLE_MS)
            acted = True
            continue
        break  # nothing actionable and no crop_* — give up (caller probe fails loudly)
    return acted, persisted_off


async def _click_toggle_off(toggle: Locator) -> bool:
    """Click the Agent pill OFF; return ``True`` only for a REAL (unforced) click.

    A REAL click (actionability-checked), never force-first: a forced click can
    flip the DOM node without firing the React handler that persists
    ``isAgentModeToggled=false`` server-side (the 2026-07-17 both-accounts pin)
    — force remains only as a last-resort fallback, and only after re-reading
    ``aria-pressed``: Playwright can raise AFTER the click events dispatched
    (post-click instability), and a blind force click on a now-OFF toggle
    re-enables agent mode and re-persists it server-side.
    """
    try:
        await toggle.click(timeout=_CLICK_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001  # NOSONAR
        log.warning("mode_control.toggle_click_fallback_force", error=str(exc))
        if await toggle.get_attribute("aria-pressed") == "true":
            await toggle.click(force=True, timeout=_CLICK_TIMEOUT_MS)
        return False
    return True


async def _reload_for_persisted_pref(page: Page) -> None:
    """Reload once after a real toggle-off failed to mount the classic panel.

    A fresh load both re-rolls the server's per-load arm AND mounts the
    now-persisted ``isAgentModeToggled=false`` preference; afterwards wait for a
    composer signal (either arm) so the next probes and the caller's cohort
    re-detect see a settled page, not the post-``load`` shell.
    """
    log.info("mode_control.reload_retry", note="toggle clicked, panel absent — reloading")
    await page.reload(timeout=_RELOAD_TIMEOUT_MS)
    await _wait_until(page, _composer_present, _COMPOSER_READY_TIMEOUT_MS)


async def _agent_surface_present(page: Page) -> bool:
    """Agent-mode evidence: the open chat sidebar (it IS the agent surface) or
    an ``aria-pressed="true"`` toggle. Never the ``tune``/``apps_spark_2``
    ligatures (module-docstring false-positive rule)."""
    try:
        if await page.locator(SIDEBAR_CLOSE_SELECTOR).first.count() > 0:
            return True
    except Exception:  # noqa: BLE001  # NOSONAR
        pass
    toggle = page.locator(AGENT_TOGGLE_SELECTOR).first
    try:
        if await toggle.count() > 0:
            return await toggle.get_attribute("aria-pressed") == "true"
    except Exception:  # noqa: BLE001  # NOSONAR
        pass
    return False


async def ensure_agent_mode(page: Page) -> bool:
    """Ensure the composer is in agent mode; return ``True`` if it acted.

    The symmetric sibling of :func:`ensure_media_mode` (#299 PR-B), replacing
    the transport's old ``_force_agent_mode`` — which verified via the ``tune``
    ligature (a documented false-positive source, see the module docstring) and
    clicked ``force=True`` unconditionally. Here ``aria-pressed`` is the
    verification signal, and the click is REAL first, force only after
    re-reading ``aria-pressed`` (the mirrored hazard: Playwright can raise
    AFTER the click dispatched, and a blind force retry on a now-ON toggle
    would turn agent mode back OFF).

    Deliberately NO reload rescue in this direction: the classic direction has
    an independent mount signal (the ``crop_*`` panel) proving a persisted
    pref failed to mount; the agent direction's only DOM evidence IS
    ``aria-pressed``, so "flipped but not served" is indistinguishable here —
    the factory's ligature-based verify owns that call and raises exit 28.

    Unknown cohorts (no crop, no toggle, no sidebar — the #493 shape) no-op
    with a warning and never enter a click loop.
    """
    await _wait_until(page, _composer_present, _COMPOSER_READY_TIMEOUT_MS)
    if await _agent_surface_present(page):
        return False
    acted = False
    for _ in range(2):
        toggle = page.locator(AGENT_TOGGLE_SELECTOR).first
        if await toggle.count() == 0:
            break
        if await toggle.get_attribute("aria-pressed") != "false":
            break
        await _click_toggle_on(toggle)
        acted = True
        await page.wait_for_timeout(_SETTLE_MS)
        if await _agent_surface_present(page):
            return acted
    if not await _agent_surface_present(page):
        log.warning(
            "mode_control.ensure_agent_incomplete",
            note="agent surface not reached after mode-control attempts",
        )
    return acted


async def _click_toggle_on(toggle: Locator) -> None:
    """Click the Agent pill ON — real click first; force only after re-reading
    ``aria-pressed`` (same post-dispatch-instability hazard as
    :func:`_click_toggle_off`, mirrored)."""
    try:
        await toggle.click(timeout=_CLICK_TIMEOUT_MS)
    except Exception as exc:  # noqa: BLE001  # NOSONAR
        log.warning("mode_control.toggle_on_fallback_force", error=str(exc))
        if await toggle.get_attribute("aria-pressed") == "false":
            await toggle.click(force=True, timeout=_CLICK_TIMEOUT_MS)
