# Feature Implementation Plan: Top Banner & Modal Overlay Dismissal (#369)

> **Status:** shipped 2026-07-27 (e847b0c) — merged to develop; boxes reconciled during the v0.45.0 release prep.

> **For agentic workers:** Run `/gflow:status --feature overlay-banner-dismissal` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Expand overlay detection and close button selectors in `src/gflow_cli/api/transports/ui_automation.py` to automatically dismiss top banners, alert bars, and announcement modals before Playwright automation interactions.

**Predict verdict:** GO — confidence 9.3/10

---

## Task 1 — Add Top Banner & Alert Selectors to `ui_automation.py`

**What:** Update `OVERLAY_DETECTOR_SELECTORS` and `OVERLAY_CLOSE_BUTTON_SELECTORS` in `ui_automation.py`.

**Files:**
- `src/gflow_cli/api/transports/ui_automation.py` — Add `TOP_BANNER_SELECTORS` and expand `OVERLAY_CLOSE_BUTTON_SELECTORS`
- `tests/api/transports/test_ui_automation_overlay.py` (or `test_ui_automation.py`) — Add unit tests for banner detection and dismissal

**Steps:**
- [x] Define `TOP_BANNER_SELECTORS = ("[role='banner']", "[role='alert']", "[role='dialog']", "div:has-text('What\\'s new')")`.
- [x] Update `_detect_overlay` to check `CHANGELOG_IFRAME_SELECTORS + WELCOME_SCREEN_SELECTORS + TOP_BANNER_SELECTORS`.
- [x] Add `button:has(i:text('clear'))`, `button:has(i.google-symbols:text('clear'))`, `[aria-label*='Got it' i]`, `button:has-text('Got it')` to `OVERLAY_CLOSE_BUTTON_SELECTORS`.
- [x] Unit test top banner detection and dismissal in `tests/test_overlay_banner_dismissal.py`.

---

## Task 2 — Verification & Quality Gates

**What:** Run unit test suite and full quality gates (`/gflow:check`).

**Files:**
- `tests/test_overlay_banner_dismissal.py` — New unit test suite for Issue #369
- `CHANGELOG.md` — Document top banner dismissal under `[Unreleased]`

**Steps:**
- [x] Run `uv run python -m pytest tests/test_overlay_banner_dismissal.py`.
- [x] Run full Impeccable Routine (`/gflow:check`).

---

## Definition of Done

- [x] All task steps checked off
- [x] Top banners, alerts, and modals detected and dismissed automatically
- [x] `/gflow:check` green (`ruff`, `pyright`, `pytest`)
- [x] `CHANGELOG.md` updated
