Feature: Credit quote parsing, fail-closed enforcement, and per-command budget deduction
  As a gflow-cli user or MCP agent
  I want deterministic pre-submit credit-cap checks
  So that caps fail closed on unknown quotes and bound total command spend

  Scenario Outline: Settings-pane credit quote parser extracts the integer across locales
    Given a credit label containing "<label_text>"
    When gflow parses the credit quote
    Then the parsed quote is <expected_quote>

    Examples:
      | label_text                    | expected_quote |
      | Generating will use 4 credits | 4              |
      | A geração vai usar 12 créditos| 12             |
      | Generating will use 0 credits | 0              |

  Scenario: Missing quote fails closed
    Given an unreadable credit quote and a cap of 10
    When gflow checks the cap
    Then the request is refused without charge

  Scenario: Exit code is stable in both modes
    Given a quote of 8 against a cap of 4
    When the credit cap error is formatted in both exit code modes
    Then both exit codes are 45

  Scenario: Video chain deducts quotes from its command budget
    Given a command budget of 6 and a link quote of 4
    When the completed link is recorded and the next quote is 4
    Then the second link is refused against the remaining budget

  Scenario: MCP video codec preserves max_credits
    Given a queued video request with max_credits 12
    When the worker decodes the request
    Then the request cap is 12
