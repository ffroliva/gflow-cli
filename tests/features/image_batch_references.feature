Feature: Image Batch Intra-Batch References
  As a user of gflow image batch
  I want batch items to reference output images from prior batch items
  So that I can maintain visual character consistency across generated clips

  Scenario: Valid intra-batch reference ordering
    Given a batch file with 2 prompts where prompt 1 references "batch:0"
    When the image batch dependencies are resolved
    Then prompt 0 is scheduled first and prompt 1 depends on prompt 0's output

  Scenario: Circular dependency detection
    Given a batch file with prompt 0 referencing "batch:1" and prompt 1 referencing "batch:0"
    When the image batch dependencies are resolved
    Then a BatchIntegrityError is raised for circular dependency

  Scenario: Upstream prompt execution failure
    Given a batch with prompt 1 depending on prompt 0
    When prompt 0 fails during execution
    Then prompt 1 is marked skipped with status "dependency_failed"
