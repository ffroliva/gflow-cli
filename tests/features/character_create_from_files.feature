@e2e @e2e_character
Feature: Create a character from local images on flow.google.com (#639)
  On an account Flow serves from flow.google.com the labs character routes are retired,
  so gflow builds a character through the character editor: the portrait upload creates
  it, then the name, personality, body image and voice are set, each write confirmed from
  the app's own reply. Nothing is generated.
  Spike: docs/superpowers/spikes/2026-10-16-migrated-character-lifecycle-wire.md.

  Opt-in: GFLOW_CLI_E2E_RUN_CHARACTER=1 with GFLOW_CLI_E2E_PROFILE. Each scenario creates
  a fresh project with `gflow project create` (free) and leaves it, and its character,
  behind as a throwaway.
  Cost: a project create, uploads, edits and reads; no generation, no credits, no quota.

  Scenario: The CLI creates a character from a portrait and a body image, then reads it back
    Given a throwaway project
    When the CLI creates "Portable" from a portrait and a body image with voice "aoede" and personality "calm, precise"
    Then the command succeeds
    And listing the project shows "Portable" with voice "Aoede", 2 images and personality "calm, precise"

  Scenario: The MCP create tool builds the same character
    Given a throwaway project
    When the MCP create tool builds "Portable" from a portrait and a body image with voice "aoede" and personality "calm, precise"
    Then the tool succeeds
    And listing the project shows "Portable" with voice "Aoede", 2 images and personality "calm, precise"
