Feature: Video chain orchestration

  Background:
    Given a chain manifest with 3 links

  Scenario: Mid-chain failure preserves completed links and does not re-bill on resume
    Given the mocked chain aborts at link 2 with a WireFormatError
    When I run the chain
    Then the chain exit code is 21
    And the chain output mentions resuming
    And the partial result carries the earlier clip paths
    Given the recorder reports 1 completed link
    And the mocked chain completes the remaining links
    When I resume the chain
    Then the chain exit code is 0
    And the chain submitted only 2 links

  Scenario: A link routing to the text endpoint aborts the chain
    Given the mocked chain aborts at link 2 with a WireFormatError
    When I run the chain
    Then the chain exit code is 21
    And link 3 was never generated
    And the partial result carries the earlier clip paths

  Scenario: Dry-run reports cost without spending credits
    When I run the chain with --dry-run
    Then the chain exit code is 0
    And the output reports the credit cost for 3 links
    And no generation was submitted

  Scenario: A crash between download and extraction resumes at extraction
    Given the recorder reports 1 completed link whose seed frame is absent
    And the mocked chain completes the remaining links
    When I resume the chain
    Then the chain exit code is 0
    And the chain submitted only 2 links
    And the completed link was not regenerated
