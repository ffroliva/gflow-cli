# Scenario: Local video assembly

## Coverage map
Active: D7 errors; D8 paths; D11 validation; D12 observability; D13 MCP parity. Skip D1 auth, D2 WAF, D3 selectors, D4 resume, D5 concurrency, D6 datastore, D9 transport, D10 browser: local media processing only.

## Scenario table
| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D11 | Mixed resolution/frame-rate clips | High | Normalize to first clip's geometry and a documented output frame rate | Unit |
| 2 | D11 | Clip has no audio | High | Preserve video; treat clip audio as silence | Unit |
| 3 | D7 | PyAV extra absent | High | Existing typed error and install hint | Integration |
| 4 | D7 | Corrupt/unreadable clip or audio input | High | RFC 9457 typed failure; no partial output left | Integration |
| 5 | D8 | Unicode/spaced paths and output parent missing | Medium | Paths work; clear typed failure for invalid destination | Integration |
| 6 | D11 | Volume outside [0,1] or no clips | High | Click rejects before processing | Unit |
| 7 | D11 | Output equals an input / exists | High | Refuse overwrite and aliasing | Integration |
| 8 | D13 | MCP direct and queued invocation | High | All options and clip IDs survive; local operation allowed under no-spend | Integration |
| 9 | D12 | Success/error JSON | Medium | Stable structured result and Problem Details error | Integration |

## Must-cover before merge (Critical + High)
1. Mixed media normalization and silent missing clip audio.
2. Invalid input, absent optional dependency, output collision and cleanup.
3. CLI and MCP direct/queued parity, including no-spend.

## Deferred (Medium + Low — log as issues, not blockers)
1. Unicode paths and structured JSON edge coverage.

## Suggested BDD scenarios
```gherkin
Feature: Local video assembly
  Scenario: Join varied clips and mix tracks locally
    Given clips with different dimensions and frame rates, one without audio
    When I assemble them with music and voiceover
    Then the output is normalized, playable, and includes the requested mix

  Scenario: Reject an output that would overwrite an input
    Given an output path equal to a source clip
    When I assemble the clips
    Then assembly fails without modifying the source
```

## Known-issues cross-reference
No related KNOWN_ISSUES entry identified.
