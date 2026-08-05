# Scenario: Add intra-batch reference/dependency ordering to gflow image batch (#317)

## Coverage Map
- Active dimensions: **D4** (Batch manifest & dependency order), **D6** (Data layer recording), **D7** (Error propagation), **D8** (Cross-platform paths), **D11** (Input validation), **D12** (Observability).
- Skipped dimensions: **D1** (Auth), **D2** (WAF/reCAPTCHA), **D3** (Selectors), **D5** (Concurrency), **D9** (Transport), **D10** (Headless context) — these rely on existing batch transport mechanisms.

## Scenario Table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D11 Input validation | `BatchPromptItem` specifies out-of-bounds `ref` index (e.g. `batch:5` in a 3-item batch) | High | Raise `ConfigurationError` or `BatchIntegrityError` before running batch | Unit |
| 2 | D4 Batch manifest | Circular dependency detected (e.g. prompt 0 ref prompt 1, prompt 1 ref prompt 0) | High | Detect cycle during DAG sort; raise `BatchIntegrityError` | Unit / BDD |
| 3 | D4 Batch manifest | Upstream prompt fails (e.g. prompt 0 fails generation) | Critical | Abort dependent prompt 1 cleanly with `BatchPartialError` status "dependency_failed" | Integration / BDD |
| 4 | D4 Batch manifest | Valid intra-batch reference (prompt 1 references output of prompt 0) | Critical | Prompt 0 executes, saves output media ID / local path, prompt 1 uses that media as reference entity | Integration / BDD |
| 5 | D8 Cross-platform | Reference specified as local file path on Windows (`C:\foo\bar.png`) vs relative path | Medium | Resolves path safely across OSes | Unit |
| 6 | D12 Observability | Structured log event emitted on batch dependency resolution | Low | Log carries `dependency_order` array and step indices | Unit |

## Must-Cover Before Merge (Critical + High)
1. Validation of `ref` / `reference_entity` values on `BatchPromptItem`.
2. Cycle detection and topological sort of prompt items before submission.
3. Upstream failure handling: dependency failure aborts dependent items cleanly without crashing.
4. Passing generated output reference entity to subsequent prompts in the mounted session.

## Suggested BDD Scenarios (`tests/features/image_batch_references.feature`)

```gherkin
Feature: Image Batch Intra-Batch References
  As a user of gflow image batch
  I want batch items to reference output images from prior batch items
  So that I can maintain visual character consistency across generated clips

  Scenario: Valid intra-batch reference ordering
    Given a batch file with 2 prompts where prompt 2 references "batch:0"
    When the image batch is validated and ordered
    Then prompt 0 is scheduled first and prompt 1 depends on prompt 0's output

  Scenario: Circular dependency detection
    Given a batch file with prompt 0 referencing "batch:1" and prompt 1 referencing "batch:0"
    When the image batch is validated
    Then a BatchIntegrityError is raised for circular dependency

  Scenario: Upstream prompt execution failure
    Given a batch with prompt 1 depending on prompt 0
    When prompt 0 fails during execution
    Then prompt 1 is marked skipped with status "dependency_failed"
```
