Feature: gflow character show
  # NOTE: This file covers the error/collision path only.
  # Happy-path and --id lookup scenarios are in tests/cli/test_cli_character.py.

  Scenario: show name-collision exits 11 with disambiguation hint
    Given two characters named "Untitled Character" in the project
    When I run "gflow character show --project proj-1 --name Untitled Character"
    Then the exit code is 11
    And the output contains the colliding entity ids
    And the output contains "disambiguate"
