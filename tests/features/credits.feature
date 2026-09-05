Feature: Flow credit visibility
  As a Flow user with one or more saved profiles
  I want to inspect current balances before generating
  So that automation can choose an account with enough credits

  Scenario: Read the selected profile balance as JSON
    Given a saved Flow profile with 12 credits
    When I run "gflow credits user --json"
    Then the credits command exits successfully
    And the credits JSON reports 12 credits

  Scenario: Preserve funded profiles when another account fails
    Given an all-profile balance response with one unavailable account
    When I run "gflow credits list --json"
    Then the credits command exits successfully
    And the credits JSON reports a partial result totaling 12 credits
