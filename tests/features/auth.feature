Feature: Authentication
  As a Flow user
  I want to manage profiles
  So that I can authenticate against my Google account

  Scenario: List profiles when none exist
    Given the profile root is empty
    When I run "gflow auth"
    Then the exit code is 0
    And the output contains "No profiles found"

  Scenario: Status of a profile with a live Flow session
    Given a profile "experiments" exists
    And the Flow session probe reports authenticated
    When I run "gflow auth status --profile experiments"
    Then the exit code is 0
    And the output contains "experiments"

  Scenario: Status of a profile whose session is dead
    Given a profile "experiments" exists
    And the Flow session probe reports no session
    When I run "gflow auth status --profile experiments"
    Then the exit code is 1
    And the output contains "gflow auth login"

  Scenario: Use a profile
    Given a profile "experiments" exists
    When I run "gflow auth use experiments"
    Then the exit code is 0
    And the default profile is "experiments"

  Scenario: Auth-expired error during a Flow API call
    Given the mocked FlowApiClient raises AuthExpiredError
    When I run "gflow image t2i some prompt"
    Then the exit code is 3
    And the output contains "gflow auth login"
