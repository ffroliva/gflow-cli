# Scenario: Global [style] Block with Named Variants

## Coverage map

| Dimension | Relevant? | Notes |
|---|---|---|
| D1 Auth & session | No | No auth changes |
| D2 WAF / reCAPTCHA | No | No new API calls or token minting |
| D3 Selector drift | No | No Playwright selectors |
| D4 Batch manifest & resume | **Yes** | Prompt-hash resume is the core complexity |
| D5 Concurrency & Page pool | No | Pure string composition |
| D6 Data layer | No | No schema migration (state file format is additive) |
| D7 Error propagation | **Yes** | New ConfigurationError paths for invalid style references |
| D8 Cross-platform paths | Low | Style strings are text, not paths |
| D9 Transport edge cases | No | No transport changes |
| D10 Headless vs headed | No | No Playwright changes |
| D11 Input validation | **Yes** | New fields need validation |
| D12 Observability | Low | No new structlog events needed |

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D4 Resume | User edits `[style]` after partial run; completed scenes keep old look | High | Prompt hash per scene detects change; only scenes with changed hash re-run | Unit |
| 2 | D4 Resume | User adds a new variant and assigns it to a previously-completed scene | High | Hash changes → scene re-runs with new style | Unit |
| 3 | D4 Resume | User deletes a variant that a completed scene references | Critical | ConfigurationError raised at manifest parse time (unknown variant) | Unit |
| 4 | D4 Resume | State file from pre-style-variant version loads correctly | Medium | Missing `style_applied` field defaults gracefully; no crash | Unit |
| 5 | D7 Error | Scene references `style = "nonexistent"` | High | ConfigurationError: "unknown style variant 'nonexistent'" | Unit |
| 6 | D7 Error | `[style.variants.X]` has non-string `suffix` | High | ConfigurationError at parse time | Unit |
| 7 | D11 Input | `style = "none"` on a scene opts out of all style | Medium | No prefix, no suffix, no variant suffix applied; composed prompt is bare | Unit |
| 8 | D11 Input | `style_suffix` with empty string | Low | Treated as no suffix (same as omitting) | Unit |
| 9 | D11 Input | `[style]` block with only `prefix`, no `suffix` | Low | Prefix applied, no suffix; valid configuration | Unit |
| 10 | D11 Input | `style = "variant_name"` where variant exists but has empty suffix | Low | Variant selected but no suffix text appended; valid | Unit |
| 11 | D4 Resume | Two runs with identical manifest produce identical hashes | Medium | No unnecessary re-runs | Unit |
| 12 | D4 Resume | Hash includes prefix + variant suffix + scene suffix (full composition) | High | Changing any style element changes the hash | Unit |
| 13 | D11 Input | Both `scene.variant` (character) and `scene.style_variant` (style) used together | Medium | Both resolve independently; no conflict | Unit |
| 14 | D7 Error | `style_variant` references a character variant name (user confusion) | Low | Distinct error: "use scene.variant for character variants, scene.style_variant for style variants" | Unit |
| 15 | D4 Resume | `-state.json` stores `style_hash` per scene alongside existing fields | Medium | Round-trips correctly; old state files without `style_hash` load as None (always re-run) | Unit |

## Must-cover before merge (Critical + High)

1. #1 — Prompt hash detects style changes on resume
2. #2 — New variant assignment triggers re-run
3. #3 — Deleted variant raises ConfigurationError
4. #5 — Unknown style variant raises ConfigurationError
5. #6 — Invalid variant field type raises ConfigurationError
6. #12 — Hash includes full composition (prefix + variant + scene suffix)

## Deferred (Medium + Low — log as issues, not blockers)

7. #4 — Old state file backward compatibility
8. #7 — `style = "none"` opt-out
9. #8-10 — Edge cases with empty strings
10. #13-14 — Character vs style variant naming clarity

## Suggested BDD scenarios

```gherkin
Feature: Style Variants
  Scenario: Compose prompt with base style suffix
    Given a movie.toml with [style] suffix "Cinematic, photorealistic."
    And a scene with action "walks on the beach"
    When the prompt is composed
    Then the prompt ends with "Cinematic, photorealistic."

  Scenario: Compose prompt with named variant
    Given a movie.toml with [style.variants.warm] suffix "Warm golden-hour grade."
    And a scene with style_variant "warm"
    When the prompt is composed
    Then the prompt ends with "Warm golden-hour grade."

  Scenario: Compose prompt with scene style_suffix
    Given a movie.toml with [style] suffix "Photorealistic."
    And a scene with style_suffix "sunset light"
    When the prompt is composed
    Then the prompt contains "Photorealistic."
    And the prompt contains "sunset light"

  Scenario: Style none opts out
    Given a movie.toml with [style] suffix "Cinematic."
    And a scene with style_variant "none"
    When the prompt is composed
    Then the prompt does not contain "Cinematic."

  Scenario: Unknown style variant raises error
    Given a movie.toml with no variants defined
    And a scene with style_variant "nonexistent"
    When the manifest is parsed
    Then a ConfigurationError is raised

  Scenario: Prompt hash changes when style suffix changes
    Given a completed scene with prompt hash "abc123"
    And the style suffix is changed from "A" to "B"
    When the resume check runs
    Then the scene is marked for re-run
```

## Known-issues cross-reference

No existing KNOWN_ISSUES entries are affected by this feature.
