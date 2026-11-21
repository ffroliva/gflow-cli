@e2e @e2e_image
Feature: Trashing and restoring an image on a live profile spends zero credits (#1363)
  gflow-cli moves generated images to Flow's restorable Trash and restores them
  via the wire RPC pGCYOe, spending zero credits on flow.google.com.

  Scenario: Trashing and restoring an image via CLI on a live profile spends zero credits (#1363)
    Given the e2e profile is signed in to Flow
    And an existing generated image asset is cataloged for the project
    When I run "gflow media trash <media_id> --yes --json"
    Then the command exits with code 0
    And the JSON output reports status "trashed"
    And Flow's project listing reports the media archived
    And zero credits were charged
    When I run "gflow media restore <media_id> --json"
    Then the command exits with code 0
    And the JSON output reports status "restored"
    And Flow's project listing reports the media active
    And zero credits were charged

  Scenario: Trashing and restoring an image via MCP on a live profile spends zero credits (#1363)
    Given the e2e profile is signed in to Flow
    And an existing generated image asset is cataloged for the project
    When I invoke MCP tool "gflow_media_trash" with confirm true
    Then the MCP response reports status "trashed"
    And Flow's project listing reports the media archived
    And zero credits were charged
    When I invoke MCP tool "gflow_media_restore"
    Then the MCP response reports status "restored"
    And Flow's project listing reports the media active
    And zero credits were charged
