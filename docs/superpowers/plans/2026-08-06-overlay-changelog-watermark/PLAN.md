# Flow Overlay Changelog ("visible watermark toggle") Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature overlay-changelog-watermark` to find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Detect and dismiss Flow's new release-note modal (*"Introducing a visible watermark toggle"*) during UI automation to prevent prompt input blockage and timeout hangs.

**Architecture:** Extend overlay detector cascades (`TOP_BANNER_SELECTORS`) and close button cascades (`OVERLAY_CLOSE_BUTTON_SELECTORS`) in [`ui_automation.py`](file:///C:/development/github/gflow-cli/src/gflow_cli/api/transports/ui_automation.py#L439-L465). Test offline via Playwright synthetic DOM content injection.

**Predict verdict:** GO — confidence 9/10

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Medium | Selector over-broadness triggering false positives on character creation or media dialogs (#395) | Anchor detection strictly to `:has-text('View all changelogs')` and button text `'Get started'`, avoiding bare `[role='dialog']` |

---

## File structure

### Modified files
```
src/gflow_cli/api/transports/ui_automation.py
  Add `:has-text('View all changelogs')` to TOP_BANNER_SELECTORS and `button:has-text('Get started')` to OVERLAY_CLOSE_BUTTON_SELECTORS.
tests/api/transports/test_ui_automation.py
  Add synthetic DOM test verifying detection and dismissal of the Issue #403 modal.
KNOWN_ISSUES.md
  Add resolved entry for Issue #403 overlay modal.
CHANGELOG.md
  Add [Unreleased] entry for Issue #403 fix.
```

---

## Task 1 — Offline Synthetic DOM Test Scaffold

**What:** Add a unit test in `tests/api/transports/test_ui_automation.py` that injects the captured modal HTML into a mock/real Playwright page and asserts detection/dismissal fail prior to fix.

**Files:**
- `tests/api/transports/test_ui_automation.py`

**Steps:**
- [ ] Add `test_detect_and_dismiss_watermark_toggle_overlay` using mock/async Playwright Page with synthetic DOM representing the verbatim modal from Issue #403.
- [ ] Assert `_detect_overlay` returns `True` and `_dismiss_blocking_overlays` attempts to click `button:has-text('Get started')`.

---

## Task 2 — Core Selector Cascade Updates

**What:** Update selector tuples in `ui_automation.py`.

**Files:**
- `src/gflow_cli/api/transports/ui_automation.py`

**Steps:**
- [ ] Add `div:has-text('View all changelogs')` and `a:has-text('View all changelogs')` (or `*:has-text('View all changelogs')`) to `TOP_BANNER_SELECTORS`.
- [ ] Add `button:has-text('Get started')` to `OVERLAY_CLOSE_BUTTON_SELECTORS`.
- [ ] Verify unit tests pass.

---

## Task 3 — Quality Gates & Pre-Commit Validation

**What:** Run the Impeccable Routine (`/gflow:check`).

**Steps:**
- [ ] `uv run python scripts/ci/check_repo_hygiene.py`
- [ ] `uv run python scripts/ci/check_doc_links.py`
- [ ] `uv run ruff check src tests`
- [ ] `uv run ruff format --check src tests`
- [ ] `uv run pyright src`
- [ ] `uv run python -m pytest -q --cov=gflow_cli`

---

## Task 4 — Documentation & Changelog Update

**What:** Update `CHANGELOG.md` and `KNOWN_ISSUES.md`.

**Files:**
- `CHANGELOG.md`
- `KNOWN_ISSUES.md`

**Steps:**
- [ ] Add entry under `[Unreleased]` in `CHANGELOG.md`.
- [ ] Update `KNOWN_ISSUES.md` with resolved status for #403 modal.

---

## Definition of Done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] `KNOWN_ISSUES.md` updated
