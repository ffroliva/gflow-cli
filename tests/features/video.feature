Feature: Video generation

  Scenario: Single video t2v
    Given the mocked FlowApiClient returns a successful video
    When I run "gflow video t2v a hot air balloon"
    Then the exit code is 0
    And one video file is created

  Scenario: Batch with concurrency=4
    Given a manifest with 4 prompts
    And concurrency is set to 4
    When I run "gflow video batch manifest.tsv"
    Then the exit code is 0
    And 4 video files are created
    And the FlowApiClient was called concurrently

  Scenario: Rate-limit retry surfaces as success at the CLI boundary
    Given the mocked FlowApiClient returns a successful video
    When I run "gflow video t2v retry-test"
    Then the exit code is 0
    And one video file is created

  Scenario: Network failure after retries
    Given the mocked FlowApiClient raises NetworkError after 3 attempts
    When I run "gflow video t2v fail-test"
    Then the exit code is 6
    And the output contains "Check connectivity"
