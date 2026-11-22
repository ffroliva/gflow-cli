@e2e @e2e_auth
Feature: Pre-submit credit cap enforcement against Flow's live quote
  As an operator or autonomous agent running billed video commands
  I want gflow to read Flow's own pre-submit credit quote
  So that an unexpected model, duration, or count cannot spend past my budget

  Scenario: Migrated composer refuses before submit when Flow's quote exceeds the cap
    Given a live classic Flow project served by flow.google.com
    And a Playwright route backstop that aborts video generation submissions
    When the operator requests an omni-1.2 4-second 360p x1 video with max_credits 3
    Then the CLI returns CreditCapExceededError with exit code 45 and charged no
    And the quote is 4 and the max_credits value is 3
    And no video generation submission was attempted
    And the profile credit balance is unchanged

  Scenario: MCP gflow_generate_video refuses before submit when the quote exceeds the cap
    Given a live classic Flow project served by flow.google.com
    And a Playwright route backstop that aborts video generation submissions
    When MCP requests an omni-1.2 4-second 360p x1 video with max_credits 3
    Then the MCP tool returns CreditCapExceededError with exit code 45 and charged no
    And the quote is 4 and the max_credits value is 3
    And no video generation submission was attempted
    And the profile credit balance is unchanged

  @e2e_video
  Scenario: Migrated composer completes when the quote equals max_credits
    Given a live classic Flow project served by flow.google.com with paid video E2E enabled
    When the operator requests an omni-1.2 4-second 360p x1 video with max_credits 4
    Then the CLI completes and downloads a non-empty MP4
