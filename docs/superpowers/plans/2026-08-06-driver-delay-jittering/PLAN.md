# Driver Delay Jittering & Humanization Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature driver-delay-jittering` to find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Humanize UI automation timing by replacing rigid Playwright `wait_for_timeout(N)` calls with randomized timing jitter (`_jitter_ms`), mitigating automated bot signature detection without impacting batch throughput. Prompt copy/pasting remains atomic.

**Architecture:** Implement pure `_jitter_ms` helper and `_wait_jitter(page, base_ms)` transport method in [`ui_automation.py`](file:///C:/development/github/gflow-cli/src/gflow_cli/api/transports/ui_automation.py).

**Predict verdict:** GO — confidence 9.8/10

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Low | Negative or fractional millisecond values passed to Playwright | Clamp `_jitter_ms` to `max(0, int(round(jittered)))` |

---

## File structure

### Modified files
```
src/gflow_cli/api/transports/ui_automation.py
  Add _jitter_ms helper function and _wait_jitter method; update wait_for_timeout call sites.
tests/api/transports/test_ui_automation.py
  Add unit tests for _jitter_ms bounds and variance.
KNOWN_ISSUES.md
  Add resolved entry for Issue #315 delay humanization.
CHANGELOG.md
  Add [Unreleased] entry for Issue #315 enhancement.
```

---

## Task 1 — Unit Test Scaffold for Delay Jittering

**What:** Add unit tests in `tests/api/transports/test_ui_automation.py` covering boundary values (`0ms`), variance bounds, and `_wait_jitter` Playwright call delegation.

**Files:**
- `tests/api/transports/test_ui_automation.py`

**Steps:**
- [ ] Add `test_jitter_ms_zero_returns_zero` asserting `_jitter_ms(0) == 0`.
- [ ] Add `test_jitter_ms_variance_bounds` asserting 100 samples of `_jitter_ms(1000, 0.25)` fall in `[750, 1250]`.
- [ ] Add `test_wait_jitter_delegates_to_page_wait_for_timeout` asserting integer millis passed to `page.wait_for_timeout`.

---

## Task 2 — Core Implementation in `ui_automation.py`

**What:** Implement `_jitter_ms` and `_wait_jitter`, and refactor static `wait_for_timeout` calls in `ui_automation.py`.

**Files:**
- `src/gflow_cli/api/transports/ui_automation.py`

**Steps:**
- [ ] Implement `_jitter_ms(base_ms: int, variance: float = 0.25) -> int`.
- [ ] Implement `_wait_jitter(page: Page, base_ms: int, variance: float = 0.25) -> None`.
- [ ] Update static `page.wait_for_timeout` calls in editor navigation, modal dismissals, and submission flows to use `_wait_jitter`.
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
- [ ] `uv run python -m pytest -q tests/api/transports/test_ui_automation.py`

---

## Task 4 — Documentation & Changelog Update

**What:** Update `CHANGELOG.md` and `KNOWN_ISSUES.md`.

**Files:**
- `CHANGELOG.md`

**Steps:**
- [ ] Add entry under `[Unreleased]` in `CHANGELOG.md`.

---

## Definition of Done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
