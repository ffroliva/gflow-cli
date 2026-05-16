Feature: Auth Login via Real Chrome
  As a Flow user
  I want to choose my authentication browser
  So that I can successfully sign in even when bot detection is strict

  Scenario: Successful login using --browser chrome
    Given Chrome is installed on the system
    And the profile root is empty
    When I run "gflow auth login --browser chrome"
    Then the exit code is 0
    And the output contains "Launching real Chrome"

  Scenario: Successful login using --browser internal
    Given the profile root is empty
    When I run "gflow auth login --browser internal"
    Then the exit code is 0
    And the output contains "Launching internal Chromium"

  Scenario: Automatic selection logic (auto)
    Given Chrome is installed on the system
    And the profile root is empty
    When I run "gflow auth login --browser auto"
    Then the exit code is 0
    And the output contains "Launching real Chrome"

  Scenario: Failure when chrome is requested but missing
    Given Chrome is NOT installed on the system
    And the profile root is empty
    When I run "gflow auth login --browser chrome"
    Then the exit code is 11
    And the output contains "Chrome binary not found"
