# i2v Duration Tab UI Selector Drift Implementation Plan (#451)

> **For agentic workers:** Run `/gflow:status --feature issue-451-duration-tab-drift` to find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Expand the `duration_tab` selector cascade in `src/gflow_cli/api/transports/ui_automation_video.py` to match modern Google Flow editor duration control elements (`[role='tab']`, `button`, `[role='button']`, `[role='option']`, `[role='menuitem']`) while maintaining fail-closed safety (#288).

**Architecture:** Update `_select_video_duration` in `ui_automation_video.py` to pass an expanded selector list to `_probe_selector_cascade`. Add unit and BDD tests.

**Predict verdict:** GO (confidence 10/10)

---

## File structure

### New files
```
tests/test_duration_tab_drift.py
  Unit tests for duration_tab selector cascade probing & fail-closed behavior
tests/features/duration_tab_drift.feature
  BDD feature specification for duration selector cascade
```

### Modified files
```
src/gflow_cli/api/transports/ui_automation_video.py
  Expand duration_tab selector cascade in _select_video_duration
CHANGELOG.md
  Add entry under [Unreleased]
```

---

## Task 1 — Unit & BDD Test Scaffold (test scaffold)

**What:** Create unit and BDD tests covering the expanded `duration_tab` selector cascade.

**Files:**
- `tests/test_duration_tab_drift.py`
- `tests/features/duration_tab_drift.feature`

**Steps:**
- [ ] Write unit tests in `test_duration_tab_drift.py` asserting `_select_video_duration` matches buttons, options, and tabs
- [ ] Write unit test asserting fail-closed `UiSelectorDriftError` on probe miss
- [ ] Write BDD feature file for duration selector cascade

---

## Task 2 — Duration Selector Cascade Expansion Implementation

**What:** Update `_select_video_duration` in `src/gflow_cli/api/transports/ui_automation_video.py`.

**Files:**
- `src/gflow_cli/api/transports/ui_automation_video.py`

**Steps:**
- [ ] Expand `duration_tab` probe selectors to include `[role='tab']`, `button`, `[role='button']`, `[role='option']`, `[role='menuitem']` and text variants (`{seconds}s`, `{seconds} seconds`, `{seconds}s.`)

---

## Task 3 — Quality Gates & Verification

**What:** Verify all 7 local quality gates pass.

**Files:**
- `CHANGELOG.md`

**Steps:**
- [ ] Add entry under `[Unreleased]` in `CHANGELOG.md`
- [ ] Run `/gflow:check` to ensure clean local quality gates

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green
- [ ] `CHANGELOG.md` updated
- [ ] Fail-closed behavior (#288) preserved
