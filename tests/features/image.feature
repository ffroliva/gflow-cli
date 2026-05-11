Feature: Image generation

  Scenario: T2I single image
    Given the mocked FlowApiClient returns a successful image
    When I run "gflow image t2i a peaceful lake"
    Then the exit code is 0
    And one image file is created

  Scenario: Multi-image fan-out
    Given the mocked FlowApiClient returns successful images
    When I run "gflow image t2i mountains -n 4"
    Then the exit code is 0
    And 4 image files are created

  Scenario: Content policy rejection
    Given the mocked FlowApiClient raises ContentPolicyError
    When I run "gflow image t2i something rejected"
    Then the exit code is 5
    And the output contains "content policy"

  Scenario: Wire format error during image generation
    Given the mocked FlowApiClient raises WireFormatError
    When I run "gflow image t2i wire-fail"
    Then the exit code is 7
    And the output contains "File a bug"
