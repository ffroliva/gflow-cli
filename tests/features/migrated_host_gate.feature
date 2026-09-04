Feature: Flow's migrated origin is detected before the run pays for it
  As an operator whose account is inside Google's flow.google.com rollout
  I want gflow to name the migration as soon as the host is knowable
  So that a retry loop is not spent on a doomed attempt each time

  Scenario: the host flips after goto returns
    Given a project navigation that returns on labs.google
    And Flow redirects the page to flow.google.com before the first blocking wait
    When gflow probes the UI cohort
    Then it fails with FlowHostMigratedError and exit code 36
    And the error is not retryable
    And the error names flow.google.com rather than selector drift

  Scenario: the old host is untouched
    Given a project navigation that lands on labs.google and never redirects
    When gflow probes the UI cohort
    Then the classic cohort is bound
    And no additional navigation or wait was performed

  Scenario: an unreadable URL is not mistaken for the migrated origin
    Given a page whose url cannot be read as a string
    When gflow probes the UI cohort
    Then the classic cohort is bound
