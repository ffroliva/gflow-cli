# Locale-Agnostic Selectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor UI selectors in `UiAutomationTransport` to be locale-invariant, ensuring compatibility with all Google account languages.

**Architecture:** Use ARIA attributes, Radix UI tokens, and Material Symbol ligatures to identify DOM elements. Remove dependencies on human-readable text strings.

**Tech Stack:** Python 3.11+, Playwright, pytest.

---

### Task 0: Multi-Locale Discovery

**Files:**
- Create: `scripts/dev/capture_locale_invariants.py`

- [ ] **Step 1: Implement Discovery Script**
Create a script that launches the browser in different locales (en-US, pt-BR, es-ES) and dumps the Radix/ARIA attributes of the main editor.
```python
# scripts/dev/capture_locale_invariants.py
# Use Playwright locale='es-ES' etc.
```

- [ ] **Step 2: Run Discovery**
Run for 3 locales and compare the JSON dumps. Identify the absolute invariants (e.g., `aria-controls*='IMAGE'`).

### Task 1: Refactor Image-Mode Selectors

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation.py`
- Test: `tests/api/transports/test_ui_automation.py`

- [ ] **Step 1: Update IMAGE_TAB_IN_MENU_SELECTORS**
Refactor based on Task 0 findings.
```python
IMAGE_TAB_IN_MENU_SELECTORS = (
    "[role='menu'] [role='tab'][aria-controls*='IMAGE']",
    "[role='tab'][aria-controls*='IMAGE']",
    "[role='menu'] [role='tab']:has(i:text('image'))",
)
```

- [ ] **Step 2: Update NEW_PROJECT_SELECTORS**
Refactor to use icon ligatures and ARIA labels.
```python
NEW_PROJECT_SELECTORS = (
    "button:has(i.google-symbols:text('add_2'))",
    "button:has(i:text('add_2'))",
    "[aria-label*='Project' i]",
)
```

- [ ] **Step 3: Update ONBOARDING_SELECTORS**
Broaden patterns for onboarding bypass.

- [ ] **Step 4: Run unit tests**
Run: `uv run python -m pytest tests/api/transports/test_ui_automation.py`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/gflow_cli/api/transports/ui_automation.py
git commit -m "refactor: locale-agnostic selectors for image mode"
```

### Task 2: Refactor Video-Mode Selectors

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`
- Test: `tests/api/transports/test_ui_automation_video.py`

- [ ] **Step 1: Update MODE_SWITCH_TRIGGER_SELECTORS**

- [ ] **Step 2: Update VIDEO_TAB_IN_MENU_SELECTORS**

- [ ] **Step 3: Update Aspect and Count selectors**
Refactor `VIDEO_ASPECT_TAB_SELECTORS` and `COUNT_ONE_SELECTORS` to use ARIA tokens.

- [ ] **Step 4: Run unit tests**
Run: `uv run python -m pytest tests/api/transports/test_ui_automation_video.py`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add src/gflow_cli/api/transports/ui_automation_video.py
git commit -m "refactor: locale-agnostic selectors for video mode"
```

### Task 3: Rigorous E2E & Verification

- [ ] **Step 1: Verify English (en-US)**
Run: `uv run python scripts/smoke_image.py`

- [ ] **Step 2: Verify Portuguese (pt-BR)**
Temporarily force locale to `pt-BR` in `UiAutomationTransport.setup` and run smoke tests.

- [ ] **Step 3: Verify Spanish (es-ES)**
Force locale to `es-ES` and run smoke tests.

- [ ] **Step 4: Final Documentation**
Update `PLAN.md` and `CHANGELOG.md`.

- [ ] **Step 5: Commit**
```bash
git add PLAN.md CHANGELOG.md
git commit -m "docs: finalize locale-agnostic refactor"
```
