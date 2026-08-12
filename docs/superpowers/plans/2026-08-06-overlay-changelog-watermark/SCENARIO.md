# Scenario: Issue #403 — Flow Overlay Changelog ("visible watermark toggle")

## Coverage Map
- **Active Dimensions:**
  - **D3 (Selector cascade drift):** The new release modal *"Introducing a visible watermark toggle"* renders inline without an iframe, displaying a chrome link `"View all changelogs"` and primary button `"Get started"`.
  - **D7 (Error propagation & exit codes):** Ensure detection returns `True` and dismisses via button click instead of falling through to Escape or timing out with `FlowAgentUiError`.
  - **D10 (Headless/Headed environment):** Offline synthetic DOM verification using mock Playwright page evaluate/content injection.
- **Skipped Dimensions:** D1, D2, D4, D5, D6, D8, D9, D11, D12 (unaffected by UI overlay selector cascade addition).

---

## Scenario Table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D3 Selector drift | Inline modal renders `"Introducing a visible watermark toggle"` with chrome link `"View all changelogs"` | Critical | `_detect_overlay` evaluates `TOP_BANNER_SELECTORS` and returns `True` | BDD / Unit |
| 2 | D3 Selector drift | Modal contains primary action button `"Get started"` without a standard `✕` close button | Critical | `_dismiss_blocking_overlays` matches `button:has-text('Get started')` and clicks it | BDD / Unit |
| 3 | D7 Error propagation | Modal is present over prompt box when starting generation | High | Overlay is detected and dismissed cleanly before prompt input, avoiding UI timeout | Integration |

---

## Must-Cover Before Merge (Critical + High)
1. Add `:has-text('View all changelogs')` to `TOP_BANNER_SELECTORS` in [`ui_automation.py`](file:///C:/development/github/gflow-cli/src/gflow_cli/api/transports/ui_automation.py#L439-L442).
2. Add `button:has-text('Get started')` to `OVERLAY_CLOSE_BUTTON_SELECTORS` in [`ui_automation.py`](file:///C:/development/github/gflow-cli/src/gflow_cli/api/transports/ui_automation.py#L447-L465).
3. Add offline BDD scenario in `tests/features/overlay_changelog.feature` (or unit test in `tests/api/transports/test_ui_automation.py`) with synthetic DOM HTML representing the verbatim modal.

---

## Suggested BDD Scenario (`tests/features/overlay_changelog.feature`)

```gherkin
Feature: Flow Overlay Changelog Dismissal
  Scenario: Detect and dismiss the visible watermark toggle changelog modal
    Given a Playwright page with the "visible watermark toggle" overlay rendered inline:
      """html
      <div role="dialog">
        <h2>Introducing a visible watermark toggle</h2>
        <p>We've introduced a new setting...</p>
        <a href="/changelogs">View all changelogs</a>
        <button>Get started</button>
      </div>
      """
    When overlay detection is executed
    Then the overlay should be detected as present
    And dismissing blocking overlays should click the "Get started" button
```
