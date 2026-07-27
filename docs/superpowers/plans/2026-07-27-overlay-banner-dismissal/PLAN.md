# Feature Implementation Plan: Top Banner & Modal Overlay Dismissal (#369)

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
- [ ] Define `TOP_BANNER_SELECTORS = ("[role='banner']", "[role='alert']", "[role='dialog']", "div:has-text('What\\'s new')")`.
- [ ] Update `_detect_overlay` to check `CHANGELOG_IFRAME_SELECTORS + WELCOME_SCREEN_SELECTORS + TOP_BANNER_SELECTORS`.
- [ ] Add `button:has(i:text('clear'))`, `button:has(i.google-symbols:text('clear'))`, `[aria-label*='Got it' i]`, `button:has-text('Got it')` to `OVERLAY_CLOSE_BUTTON_SELECTORS`.
- [ ] Unit test top banner detection and dismissal in `tests/test_overlay_banner_dismissal.py`.

---

## Task 2 — Verification & Quality Gates

**What:** Run unit test suite and full quality gates (`/gflow:check`).

**Files:**
- `tests/test_overlay_banner_dismissal.py` — New unit test suite for Issue #369
- `CHANGELOG.md` — Document top banner dismissal under `[Unreleased]`

**Steps:**
- [ ] Run `uv run python -m pytest tests/test_overlay_banner_dismissal.py`.
- [ ] Run full Impeccable Routine (`/gflow:check`).

---

## Definition of Done

- [ ] All task steps checked off
- [ ] Top banners, alerts, and modals detected and dismissed automatically
- [ ] `/gflow:check` green (`ruff`, `pyright`, `pytest`)
- [ ] `CHANGELOG.md` updated
