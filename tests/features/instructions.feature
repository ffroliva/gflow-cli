Feature: gflow instructions management

  Scenario: Syncing instructions containing reference image IDs
    Given a project with no existing instruction cards
    When I run "gflow instructions add 'Crayon style' --text 'crayon drawing' --ref '44444444-4444-4444-4444-444444444444' --project proj-123"
    Then the exit code is 0
    And the project brief contains 1 card
    And the card "Crayon style" has 1 image reference

  Scenario: Toggling active/inactive states of relational cards
    Given a project with an active instruction card "Watercolor"
    When I run "gflow instructions disable 'Watercolor' --project proj-123"
    Then the exit code is 0
    And the card "Watercolor" is disabled

  Scenario: Parsing global and per-scene instructions in movie manifest
    Given a movie manifest with global and per-scene instructions
    When I read the movie manifest
    Then the manifest title is "Instructions Movie"
    And the global instructions contain 1 card "Cinematic Lighting"
    And the scene "scene-1" has 1 disable override "Cinematic Lighting"
    And the scene "scene-1" has 1 custom card override "Fog Atmosphere"
