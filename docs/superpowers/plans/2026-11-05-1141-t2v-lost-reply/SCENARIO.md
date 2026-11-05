# Scenario: finding a text-to-video clip after its submit reply is lost (#1141)

Evidence: [spike](../../spikes/2026-11-04-t2v-lost-submit-reply.md). `/gflow:predict`: CAUTION, 5 of 5.

## Coverage map

Active: D5 (two runs, one project), D7 (exit codes), D9 (transport), D12 (log events), D13 (MCP).
Skipped: D1, D2, D3, D10 (no auth, token, selector or display change), D4 (batch rows reach the
same call and need no ledger change), D6 (no new recorder event; ADR #2), D8, D11 (the prompt is
compared, not parsed).

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D9 | Reply lost, polls and listing name exactly one running match | High | Adopted, one submit, clip saved | Integration, E2E |
| 2 | D5 | Two same-prompt records active in the window | Critical | Exit 9, no adoption | Integration |
| 3 | D5 | Polls name one, the listing shows two | Critical | Exit 9, naming both | Integration |
| 4 | D5 | A same-prompt record older than the window is still running | Critical | Exit 9 at once, not adopted | Integration |
| 5 | D5 | An older same-prompt clip is terminal | Critical | Ignored; a done poll record is never a candidate | Integration |
| 6 | D9 | Listing does not answer | High | Exit 9 | Integration |
| 7 | D9 | Listing answers with no match | High | Exit 9 at once, no wait to the deadline | Integration |
| 8 | D9 | The clip finished before the listing read | High | A done listing record inside the window is adopted | Integration |
| 9 | D9 | Same prompt, different model | Critical | Not a match | Unit |
| 10 | D9 | Prompt is a substring of another record's prompt | Critical | Not a match: exact equality only | Unit |
| 11 | D9 | Submit body has no readable model key | High | Exit 9 as today | Integration |
| 12 | D7 | `count > 1`, i2v, r2v, extend | High | No prompt adoption; extend and the uploaded-id path are unchanged | Integration |
| 13 | D7 | Every refusal | High | `_outcome_unknown`, `retryable: false`, exit 9, no new code | Integration |
| 14 | D9 | Recovery | Critical | Submit clicked exactly once | Integration |
| 15 | D12 | Prompt text | High | Never logged: only ids and counts | Unit |
| 16 | D13 | MCP direct and queued | Medium | Same `run_video`, no new parameter, so no schema change; named in the PR | Check step 1b |

## Must-cover before merge

Rows 1 to 14. Row 1 needs the paid e2e, because only real Flow can show the listing naming a
running clip on t2v. That leg was not measured by the spike.

## Deferred

- Two separate processes on one project, and a lost reply during an overlap. Not measured; both
  end in a refusal or a window match, and neither can double-bill.
- A local clock running behind is undetectable. The listing check is what covers it.

## Suggested BDD scenarios

Offline in `tests/api/transports/test_migrated_composer.py`, beside the #1112 cases that share
`_observe_lost`. The browser leg extends `tests/e2e/`'s lost-reply e2e.

```gherkin
@e2e @e2e_video
Feature: A lost text-to-video submit reply is recovered without a second submit
  Scenario: the clip is found by its prompt and model
    Given a text-to-video run whose submit reply body is unreadable
    When Flow names one running clip with that prompt and model
    Then the clip is saved and submit was clicked once
```

## Known-issues cross-reference

Resolves #1141. Extends #1112's recovery; does not touch `run_extend`.
