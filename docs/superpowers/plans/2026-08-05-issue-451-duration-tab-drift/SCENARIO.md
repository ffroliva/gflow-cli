# Scenario: i2v duration tab UI selector drift (#451)

## Coverage Map
- Active dimensions: **D5** (Transport & UI Automation), **D7** (Error propagation & fail-closed invariants), **D8** (CLI UX).
- Skipped dimensions: **D1**, **D2**, **D3**, **D4**, **D6**, **D9**, **D10**, **D11**, **D12**.

## Scenario Table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D5 Transport | `duration_tab` probe matches modern Flow UI duration controls (`role='tab'`, `role='button'`, `button`, `role='option'`) | High | Duration control selected successfully | Unit / Integration |
| 2 | D7 Error propagation | `duration_tab` probe fails closed when duration control is completely absent | High | Raises `UiSelectorDriftError` with `probe=duration_tab` (#288 invariant) | Unit |

## Must-Cover Before Merge
1. Selector cascade in `_select_video_duration` probes `[role='tab']`, `button`, `[role='button']`, `[role='option']`, `[role='menuitem']`.
2. Fail-closed behavior remains enforced when no selector matches.

## Suggested BDD Scenarios (`tests/features/duration_tab_drift.feature`)

```gherkin
Feature: Video Duration Selector Cascade
  As a user running video generations with --duration
  I want the transport to locate duration controls across Flow UI variations
  So that explicit clip lengths are accurately set without silent fallbacks

  Scenario: Match duration control via tab or button selector
    Given a Playwright page with a duration button "6s"
    When selecting video duration 6
    Then the duration control is found and clicked

  Scenario: Fail closed when duration control is not found
    Given a Playwright page with no duration controls
    When selecting video duration 4
    Then a UiSelectorDriftError is raised for duration_tab
```
