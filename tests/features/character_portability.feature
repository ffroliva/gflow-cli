@e2e @e2e_character
Feature: Portable characters on flow.google.com (#639)
  On an account Flow serves from flow.google.com the labs character routes are retired,
  so gflow reads a project through the app's own `Zzl0ze` reply and builds a character
  through the character editor, confirming each write from the app's own reply. A
  character is built from local files (the editor's Upload binds them, no generation),
  exported to a plain folder, and imported into another project or another account.
  Spike: docs/superpowers/spikes/2026-10-16-migrated-character-lifecycle-wire.md.

  Opt-in: GFLOW_CLI_E2E_RUN_CHARACTER=1. Profile A is GFLOW_CLI_E2E_PROFILE; the
  cross-account scenario also needs GFLOW_CLI_E2E_PROFILE_B. Every project is created
  fresh by `gflow project create` (free) and left behind as a throwaway.
  Cost: project creates, uploads, edits and reads — no generation, no credits, no quota.

  Scenario: Create a character from local images with a voice, then read it back
    Given a throwaway project on profile A
    When the CLI creates "Portable" from a portrait and a body image with voice "aoede" and personality "calm, precise"
    Then the command succeeds
    And listing the project shows "Portable" with voice "Aoede", 2 images and personality "calm, precise"

  Scenario: The MCP list tool reads the same character
    Given a throwaway project on profile A holding the character "Portable"
    When the MCP character list tool reads the project
    Then it lists "Portable" with voice "Aoede" and 2 images

  Scenario: Export to a folder, then import into another project on the same account
    Given a throwaway project on profile A holding the character "Portable"
    And a second throwaway project on profile A
    When the CLI exports "Portable" to a bundle folder
    And the CLI imports that bundle into the second project
    Then the bundle holds a manifest, a portrait and a body image, and no URL or account id
    And listing the second project shows "Portable" with voice "Aoede", 2 images and personality "calm, precise"

  Scenario: Import into another account
    Given a throwaway project on profile A holding the character "Portable"
    And a throwaway project on profile B
    When the CLI exports "Portable" to a bundle folder
    And the CLI imports that bundle into the project on profile B
    Then listing that project on profile B shows "Portable" with voice "Aoede", 2 images and personality "calm, precise"

  Scenario: The MCP tools export and import the same character
    Given a throwaway project on profile A holding the character "Portable"
    And a second throwaway project on profile A
    When the MCP export tool writes "Portable" to a bundle folder
    And the MCP import tool imports that bundle into the second project
    Then listing the second project shows "Portable" with voice "Aoede", 2 images and personality "calm, precise"

  Scenario: The MCP create tool builds a character from local images
    Given a throwaway project on profile A
    When the MCP create tool builds "Portable" from a portrait and a body image with voice "aoede" and personality "calm, precise"
    Then listing the project shows "Portable" with voice "Aoede", 2 images and personality "calm, precise"

  @e2e_image
  Scenario: Generate images directly from a character folder on another account
    Given a throwaway project on profile A holding the character "Portable"
    And a throwaway project on profile B
    When the CLI exports "Portable" to a bundle folder
    And the CLI generates an image on profile B from "Portable reading a book" with that character folder and no project
    Then the image is written and the project it created lists "Portable" once
    When the CLI generates an image on profile B from "Portable waving" with that character folder in that project
    Then the image is written and the project it created lists "Portable" once

  @e2e_image
  Scenario: The MCP image tool generates from a character folder through the queue
    Given a throwaway project on profile A holding the character "Portable"
    And a second throwaway project on profile A
    When the CLI exports "Portable" to a bundle folder
    And the MCP image tool generates "Portable waving" in the second project with that character folder
    Then the queued image is written and the second project lists "Portable" once
