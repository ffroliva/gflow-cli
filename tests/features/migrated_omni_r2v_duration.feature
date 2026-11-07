@e2e @e2e_auth
Feature: Omni ingredients accept the model's explicit durations
  Scenario Outline: Apply an explicit Omni ingredients duration without submitting
    Given an existing migrated Flow project for the duration probe
    When Omni ingredients settings request <seconds> seconds
    Then the live duration radio is <seconds> seconds

    Examples:
      | seconds |
      | 4       |
      | 6       |
      | 8       |
      | 10      |
