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
