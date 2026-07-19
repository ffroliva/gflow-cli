Feature: Asset tagging (@ mentions) in prompt text

  Scenario: unresolved mention aborts before any credited generation
    Given a project with no assets
    When I run gflow image t2i "@nonexistent walking" --project proj-123
    Then the command should fail with exit code 11
    And the output should contain "Unknown mention"
    And the output should list no available assets

  Scenario: ambiguous mention lists candidate ids
    Given a project with two characters named "Zoro"
    When I run gflow image t2i "@Zoro walking" --project proj-123
    Then the command should fail with exit code 11
    And the output should contain "Ambiguous mention"
    And the output should list the candidate ids

  Scenario: mention count over model reference cap fails pre-submit
    Given a project with five characters
    When I run gflow image t2i "@Zoro0 @Zoro1 @Zoro2 @Zoro3 walking" --project proj-123 --model imagen4
    Then the command should fail with exit code 11
    And the output should contain "reference cap"

  Scenario: @me is refused with the region-gating hint
    Given a project with some assets
    When I run gflow image t2i "@me walking" --project proj-123
    Then the command should fail with exit code 11
    And the output should contain "avatar likeness is region-gated"

  Scenario: media mention on a video command is refused as Phase 3
    Given a project with a media asset named "logo"
    When I run gflow video t2v "@logo walking" --project proj-123
    Then the command should fail with exit code 11
    And the output should contain "media mentions on the video path are Phase 3"

  Scenario: entity and media mentions stage references on the image path
    Given a project with character "Zoro" and media "logo"
    When I run gflow image t2i "@Zoro hands @logo the sword" --project proj-123
    Then the command should succeed
    And the image prompt should be de-tagged to "Zoro hands logo the sword"
