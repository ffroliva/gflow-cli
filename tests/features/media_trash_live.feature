@e2e @e2e_image
Feature: Trashing and restoring an image on a live profile spends zero credits (#1363)
  gflow-cli moves generated images to Flow's restorable Trash and restores them
  via the wire RPC pGCYOe, spending zero credits on flow.google.com.

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
