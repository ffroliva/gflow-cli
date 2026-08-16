Feature: Media picker resolves catalog UUIDs through Flow display names
  A catalog UUID is stable identity, while Flow's browser picker is searched
  by the asset's recorded display name. The surfaced tile must still match the
  exact UUID, and the UUID path must not scan the unfiltered grid.

  Scenario: A catalog name surfaces the exact UUID without scrolling
    Given an image UUID reference named "Unique catalog caption"
    And picker search surfaces the exact UUID tile
    When the transport binds the image reference
    Then only the catalog display name is typed into picker search
    And the exact UUID tile is attached
    And no grid scroll is attempted

  Scenario: Duplicate display names are disambiguated by UUID
    Given an image UUID reference named "Shared catalog caption"
    And another picker tile has the same display name
    And picker search surfaces the exact UUID tile
    When the transport binds the image reference
    Then the target locator contains the requested UUID
    And the other same-name tile is not attached
    And no grid scroll is attempted

  Scenario: A named picker miss uses the recorded local fallback
    Given an image UUID reference named "Missing catalog image"
    And the exact UUID tile is absent from the picker
    And the catalog has a recorded local fallback
    When the transport binds the image reference
    Then only the catalog display name is typed into picker search
    And the recorded local file is uploaded
    And no grid scroll is attempted

  Scenario: A video frame uses its catalog name and exact UUID
    Given a video frame UUID named "Shared catalog caption"
    And picker search surfaces the exact UUID tile
    When the transport binds the video frame
    Then only the catalog display name is typed into picker search
    And the exact UUID tile is attached
    And no grid scroll is attempted
