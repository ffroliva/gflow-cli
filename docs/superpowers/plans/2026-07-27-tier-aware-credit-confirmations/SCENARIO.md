# Scenario: Tier-aware credit confirmations

## Coverage map

This change is intentionally confined to human-facing CLI planning and confirmation text.
It does not alter generation requests, browser automation, authentication, persistence, or
the JSON result schema.

Relevant dimensions:

- **D4 — Batch manifest & resume:** completed chain links and movie scenes must remain excluded;
  stale movie scenes must remain pending.
- **D7 — Error propagation & exit codes:** declining the existing chain confirmation must still
  abort before any browser, client, tool, or submission work.
- **D8 — Cross-platform paths/output:** replacement text must be plain ASCII and render in
  PowerShell, cmd, and POSIX terminals.
- **D11 — Input validation & boundary values:** zero, one, and many pending operations must retain
  existing validation and resume behavior.
- **D12 — Observability & output contracts:** human output changes, but JSON envelopes, structured
  logs, flags, exit codes, and MCP schemas must not.

Skipped dimensions:

- **D1/D2/D3/D5/D6/D9/D10:** no session, WAF/reCAPTCHA, selector, Page-pool, data-layer,
  transport, or headed-browser behavior changes.

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D4/D12 | Fresh three-link chain dry-run | High | Prints three pending video operations, variable-cost/Flow-authority guidance, no numeric credit estimate, and submits nothing | Unit + BDD |
| 2 | D4/D12 | Resume a three-link chain with two completed links | High | Counts only one pending operation and does not imply completed links will be charged again | Unit + BDD |
| 3 | D7 | Operator declines the existing chain confirmation | Critical | Aborts before client construction, tool application, browser startup, or submission | Unit |
| 4 | D7/D12 | Operator passes `--yes` | Medium | Bypasses only the existing prompt; validation, resume filtering, and JSON result shape remain unchanged | Unit |
| 5 | D4/D11 | Movie dry-run mixes completed, stale, and new scenes | High | Completed scenes show skipped; stale and new scenes count as pending video operations; no numeric credit estimate; no API calls | Unit |
| 6 | D12 | Normal movie run remains non-interactive | High | No new confirmation prompt or flag is introduced; existing execution ordering is unchanged | Unit / code review |
| 7 | D12 | Chain succeeds with `--json --yes` | High | Machine-readable success envelope is unchanged and contains no new pricing fields or prose | Unit |
| 8 | D4/D11 | Resumed chain has zero remaining links | High | Existing early return remains; no confirmation or client construction occurs | Unit |
| 9 | D8/D11 | Exactly one versus multiple pending operations | Low | Text remains grammatical and ASCII-safe without changing the count | Unit |
| 10 | D12 | A submitted video later fails | Medium | Guidance says operations may consume credits under Flow policy; it does not promise success-based billing | Docs / review |
| 11 | D12 | Historical evidence names an observed past credit amount | Low | Historical live-verification and incident records remain unchanged | Search / review |

## Must-cover before merge (Critical + High)

1. Declined chain confirmation performs no client, tool, browser, or submission work.
2. Fresh and resumed chain plans report only pending operation counts and no numeric credit charge.
3. Movie plans derive pending work from the existing completed/stale predicate.
4. Dry-run remains zero-I/O for both commands.
5. Chain JSON success/error envelopes remain unchanged.
6. Zero-remaining resume behavior remains an early no-op.

## Deferred (Medium + Low — log as issues, not blockers)

1. A dynamic pricing or entitlement API is deliberately deferred until Google exposes a verified,
   stable source; no speculative abstraction is added.
2. Adding a new `gflow movie run` confirmation is a separate compatibility decision. Movie remains
   non-interactive and operators use `--dry-run` for preflight.
3. Historical documents retain the prices observed at the time of their recorded live run.

## Suggested BDD scenarios

```gherkin
Feature: Tier-aware video operation guidance

  Scenario: Dry-run reports pending chain work without guessing credits
    Given a chain manifest with 3 links
    When I run the chain with --dry-run
    Then the output reports 3 pending video operations
    And the output directs me to check the current cost in Flow
    And the output contains no numeric credit estimate
    And no generation was submitted

  Scenario: Resume counts only unfinished chain links
    Given a chain manifest with 3 links
    And the recorder reports 2 completed links
    When I resume the chain with --dry-run
    Then the output reports 1 pending video operation
    And the output contains no numeric credit estimate
```

Movie completed/stale/new accounting is covered directly at the Click boundary because no movie
BDD feature exists and duplicating the substantial movie fixtures would add test-only machinery.

## Known-issues cross-reference

- Issue #125's chain route-abort and partial-result behavior is unchanged.
- Chain resume continuity limitations remain unchanged; only the truthful count shown before
  pending work changes.
- The quota-display backlog remains open. This change explicitly avoids presenting local operation
  counts as an account-credit balance or exact charge.
