Feature: Image upscale

  Scenario: 2K upscale with an explicit project
    Given the mocked upscale writes a file
    When I run "gflow image upscale 3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14 --scale 2k --project ffb768fb-cf2d-48b7-a135-92978667c37d"
    Then the exit code is 0
    And one upscaled file is created

  Scenario: Project is resolved from the local catalog
    Given the mocked upscale writes a file
    And the catalog resolves the project
    When I run "gflow image upscale 3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14 --scale 2k"
    Then the exit code is 0
    And one upscaled file is created

  Scenario: 4K on a non-Ultra account
    Given the mocked upscale raises UpscaleUnavailableError
    When I run "gflow image upscale 3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14 --scale 4k --project ffb768fb-cf2d-48b7-a135-92978667c37d"
    Then the exit code is 22

  Scenario: Project cannot be resolved
    Given the catalog has no record
    When I run "gflow image upscale 3a56bb5e-92a2-44f4-9992-3c6a9bf0cd14 --scale 2k"
    Then the exit code is 2
    And the upscale output contains "--project"

  Scenario: Malformed media id is rejected before any work
    When I run "gflow image upscale not-a-uuid --scale 2k"
    Then the exit code is 2
