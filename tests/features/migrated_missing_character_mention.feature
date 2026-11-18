@e2e @e2e_image
Feature: An @mention of a character the project lacks is refused before submit (#1333)
  On an account Flow serves from flow.google.com an `@Name` the catalog cannot resolve is
  attached by name through the composer's `@` picker. When no picker option is titled
  that name, the run ends in a typed refusal naming the character, with nothing sent to
  Flow: `ReferenceNotFoundError` (exit 32, `charged: no`), never a raw browser timeout.
  Needs a project on flow.google.com: GFLOW_CLI_E2E_PROJECT. Costs nothing, since no
  generation is submitted.

  Scenario: The CLI refuses a missing character with exit 32
    Given a project on flow.google.com
    When the CLI generates "@NoSuchName1333 by a window"
    Then the command exits 32 naming "NoSuchName1333", with no image written

  Scenario: The MCP image tool refuses the same character with a typed error
    Given a project on flow.google.com
    When the MCP image tool generates "@NoSuchName1333 by a window"
    Then the task fails as reference-not-found naming "NoSuchName1333", charged no
