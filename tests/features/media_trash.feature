Feature: Move a generated media item to Flow's Trash and restore it (#1363)
  gflow-cli moves generated images, videos, or scenes to Flow's restorable Trash
  using the wire RPC pGCYOe, requiring explicit confirmation before trashing,
  and provides a corresponding restore command to recover trashed items at zero credit cost.

  Scenario: Trashing a media item with explicit confirmation sends pGCYOe wire mutation
    Given a cataloged media item with ID "5a383d6c-2207-42d3-8d62-4cd82ff804ac" in project "proj-uuid"
    When the user runs "gflow media trash 5a383d6c-2207-42d3-8d62-4cd82ff804ac --yes --json"
    Then the command exits with code 0
    And a batchexecute mutation is sent for RPC "pGCYOe" with media ID "5a383d6c-2207-42d3-8d62-4cd82ff804ac"
    And the JSON output reports status "trashed" and project "proj-uuid"

  Scenario: Trashing without confirmation flag in a non-interactive environment fails fast
    Given a cataloged media item with ID "5a383d6c-2207-42d3-8d62-4cd82ff804ac"
    And the execution environment is non-interactive
    When the user runs "gflow media trash 5a383d6c-2207-42d3-8d62-4cd82ff804ac"
    Then the command exits with code 2
    And no network request is sent to Flow

  Scenario: Trashing an ambiguous media identifier across profiles requires --profile
    Given an asset ID "5a383d6c-2207-42d3-8d62-4cd82ff804ac" matching multiple profiles "profile-a" and "profile-b"
    When the user runs "gflow media trash 5a383d6c-2207-42d3-8d62-4cd82ff804ac --yes"
    Then the command exits with code 16
    And the error detail requests specifying "--profile"

  Scenario: Restoring a trashed media item sends pGCYOe restore mutation
    Given a trashed media item with ID "5a383d6c-2207-42d3-8d62-4cd82ff804ac" in project "proj-uuid"
    When the user runs "gflow media restore 5a383d6c-2207-42d3-8d62-4cd82ff804ac --json"
    Then the command exits with code 0
    And a batchexecute mutation is sent for RPC "pGCYOe" with collection_id reset
    And the JSON output reports status "restored" and project "proj-uuid"

  Scenario: MCP media_trash tool refuses invocation without confirm flag
    Given an MCP client connected to gflow-cli
    When the agent calls MCP tool "media_trash" with media_id "5a383d6c-2207-42d3-8d62-4cd82ff804ac" and confirm false
    Then the tool call returns an error requiring explicit confirmation
    And no mutation is sent to Flow

  @e2e @e2e_image
  Scenario: Trashing and restoring an image on a live profile spends zero credits (#1363)
    Given the e2e profile is signed in to Flow
    And an existing generated image asset is cataloged for the project
    When I run "gflow media trash <media_id> --yes --json"
    Then the command exits with code 0
    And the JSON output reports status "trashed"
    And zero credits were charged
    When I run "gflow media restore <media_id> --json"
    Then the command exits with code 0
    And the JSON output reports status "restored"
    And the image is verified back in the project
