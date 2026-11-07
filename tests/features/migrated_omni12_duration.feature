@e2e @e2e_auth
Feature: Explicit Omni 1.2 binds every integer duration without generation
  Scenario Outline: Bind an Omni 1.2 ingredient duration
    Given an existing migrated Flow project for the duration probe
    When Omni 1.2 ingredients settings request <seconds> seconds
    Then the live duration slider is <seconds> seconds

    Examples:
      | seconds |
      | 3 |
      | 4 |
      | 5 |
      | 6 |
      | 7 |
      | 8 |
      | 9 |
      | 10 |
      | 11 |
      | 12 |
      | 13 |
      | 14 |
      | 15 |
