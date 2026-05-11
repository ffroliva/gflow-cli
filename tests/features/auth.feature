Feature: Authentication
  As a Flow user
  I want to manage profiles
  So that I can authenticate against my Google account

  Scenario: List profiles when none exist
    Given the profile root is empty
    When I run "gflow auth"
    Then the exit code is 0
    And the output contains "No profiles found"

  Scenario: Status of a profile
    Given a profile "experiments" exists
    When I run "gflow auth status --profile experiments"
    Then the exit code is 0
    And the output contains "experiments"

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
