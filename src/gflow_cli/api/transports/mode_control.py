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

# Classic media panel indicator — the ``crop_*`` mode-switch trigger.
_CROP_SELECTORS: tuple[str, ...] = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text-is('crop_16_9'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text-is('crop_9_16'))",
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


async def _crop_present(page: Page) -> bool:
    for sel in _CROP_SELECTORS:
        if await page.locator(sel).count() > 0:
            return True
    return False


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


async def ensure_media_mode(page: Page) -> bool:
    """Ensure the composer is in classic media mode; return ``True`` if it acted.

    State-aware and idempotent (a no-op when already in media mode). Closes the
    expanded chat sidebar (X) if open, then toggles the Agent pill OFF **only
    when** ``aria-pressed`` reads ``"true"`` — never a blind click. Bounded loop
    (sidebar → toggle → re-check). Best-effort: logs and returns if the media
    panel never returns, leaving the caller's own probe to fail loudly.
    """
    acted = False
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
            await toggle.click(force=True, timeout=_CLICK_TIMEOUT_MS)
            await page.wait_for_timeout(_SETTLE_MS)
            acted = True
            continue
        break  # nothing actionable and no crop_* — give up (caller probe fails loudly)
    if not await _crop_present(page):
        log.warning(
            "mode_control.ensure_media_incomplete",
            note="classic media panel not restored after mode-control attempts",
        )
    return acted
