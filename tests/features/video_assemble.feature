Feature: Local video assembly
  Scenario: Join clips locally and report the result
    Given two short local clips with different frame sizes
    When I assemble the clips into one output file
    Then assembly succeeds and reports two clips

  Scenario: Refuse to overwrite a source clip
    Given one short local clip
    When I assemble the clip over itself
    Then assembly fails without changing the source
