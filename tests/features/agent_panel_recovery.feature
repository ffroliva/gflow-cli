@e2e @e2e_auth
Feature: The editor gate returns to the classic composer from Flow's agent mode
  Flow remembers agent mode and the open agent panel for the account. While the panel is
  open the Agent toggle is not in the page, so the gate must close the panel first, then
  turn agent mode off. Every scenario is $0: layout clicks, and an aborted submit.
  (#1374, #1384; spike docs/superpowers/spikes/2026-11-20-agent-mode-switch-and-panel-rendering.md)

  Scenario: The agent panel is open and Flow remembers it
    Given the project is left with agent mode on and the agent panel open
    When gflow prepares the editor
    Then the composer is the classic one
    And the classic composer is still served after a reload

  Scenario: Agent mode is on with the panel closed
    Given the project is left with agent mode on and the agent panel closed
    When gflow prepares the editor
    Then the composer is the classic one

  Scenario: A CLI video command reaches the submit from the agent panel
    Given the project is left with agent mode on and the agent panel open
    When the CLI client generates a text-to-video clip with the submit aborted
    Then a video submit was attempted
    And the classic composer is still served after a reload

  Scenario: The MCP door reaches the submit from the agent panel
    Given the project is left with agent mode on and the agent panel open
    When the MCP video tool generates a text-to-video clip with the submit aborted
    Then a video submit was attempted
