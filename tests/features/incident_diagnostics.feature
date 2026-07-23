Feature: Private incident diagnostics

  Scenario: Capture failure preserves the operational error
    Given a Flow UI failure with exit code 31
    And the incident directory is read-only
    When the command handles the failure
    Then the command exits with code 31
    And no raw exception text is emitted
    And incident capture does not retry generation

  Scenario: A systemic batch failure is captured once
    Given a manifest with fifty rows
    And every row hits the same selector failure
    When the manifest runs with continue-on-error
    Then one incident bundle is staged for that fingerprint
    And the manifest records forty-nine suppressed occurrences
    And no more than three distinct bundles exist for the command

  Scenario: Profile contention reports evidence but never reclaims
    Given another process holds the selected profile lease
    When a generation command starts
    Then it exits with ProfileLockedError code 11 before Chrome launches
    And a metadata-only incident contains validated owner evidence
    And no lock file or process is deleted

  Scenario: Remote errors do not expose local incident paths
    Given an incident was captured under a home path containing a username
    When the failure is returned through MCP or HTTP
    Then the response contains an opaque incident id and status
    And it does not contain the absolute path or username

  Scenario: Cancellation leaves no browser or lease
    Given incident capture is staging DOM evidence
    When cancellation arrives during browser context close
    Then HAR state is possibly incomplete
    And the original cancellation propagates
    And the driver stops and the profile lease is released

  Scenario: Successful generation creates no incident
    Given incident capture is enabled
    When a generation completes successfully
    Then the media artifact is valid
    And no incident directory is created for the command
