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

  Scenario: Shell multi-prompt positional batch
    Given the mocked t2i batch runner writes one image per prompt
    When I run "gflow image t2i p1 p2 p3 --aspect 16:9 --model image4"
    Then the exit code is 0
    And 3 image files are created
    And every batch prompt used aspect "16:9" and model "image4"

  Scenario: Prompt file skips blanks and comments
    Given a prompt file with 3 valid prompts, 1 blank line, and 1 comment
    And the mocked t2i batch runner writes one image per prompt
    When I run "gflow image t2i --prompts-file prompts.txt"
    Then the exit code is 0
    And 3 image files are created

  Scenario: Multiple prompt sources are rejected
    Given a prompt file with 3 valid prompts, 1 blank line, and 1 comment
    When I run "gflow image t2i p1 --prompts-file prompts.txt"
    Then the exit code is 2
    And the output contains "mutually exclusive"

  Scenario: Stdin prompts use batch path
    Given the mocked t2i batch runner writes one image per prompt
    When I pipe 3 prompts into "gflow image t2i --stdin"
    Then the exit code is 0
    And 3 image files are created

  Scenario: Shell multi-prompt upper bound
    When I run "gflow image t2i" with 51 positional prompts
    Then the exit code is 2
    And the output contains "between 1 and 50"
