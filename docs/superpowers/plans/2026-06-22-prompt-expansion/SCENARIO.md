# Scenario: Prompt Expansion ("Creative Director" mode)

## Coverage map
We evaluate the prompt expansion feature across the relevant failure dimensions.

| Dimension | Status | Notes |
|---|---|---|
| **D1 — Auth** | Skipped | Uses a separate Google AI Studio API key, not Playwright sessions. |
| **D2 — WAF** | Skipped | Standard REST API requests to Gemini are not subject to Flow's browser anti-bot filters. |
| **D3 — Selector Cascade** | Skipped | No UI automation required for prompt expansion. |
| **D4 — Batch manifest** | **Active** | Ensures prompt expansion plays nicely with batch runs without exhausting rate limits. |
| **D5 — Concurrency** | Skipped | Handled linearly during CLI preprocessing. |
| **D6 — Data layer** | **Active** | Verify that expanded prompts are recorded in the history log alongside the original prompts. |
| **D7 — Error propagation** | **Active** | Ensure graceful degradation when the API key is missing or rate-limited. |
| **D8 — Cross-platform** | Skipped | Minimal path interactions. |
| **D9 — Transport** | **Active** | Handles invalid JSON or unexpected schema in the Gemini API response. |
| **D10 — Headless/Headed** | Skipped | The API call is headless and environment-independent. |
| **D11 — Input validation** | **Active** | Guard against prompts that are too long or result in an expanded prompt exceeding Flow's limits. |
| **D12 — Observability** | **Active** | Structured log events to track if a prompt was expanded and the latency involved. |

---

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D7 Error | `GFLOW_CLI_GEMINI_API_KEY` is missing or empty | Medium | Log `INFO` warning and proceed using the raw prompt; exit code 0. | Unit / Integration |
| 2 | D7 Error | API key is invalid (HTTP 401/403) | Medium | Log warning and fall back to raw prompt; exit code 0. | Integration |
| 3 | D7 Error | Gemini API is rate-limited (HTTP 429) | Medium | Exponential backoff retry up to 3 times, then fall back to raw prompt. | Integration |
| 4 | D4 Batch | Running `gflow image t2i` batch with `--expand` | High | Expand prompts sequentially with rate-limit safety pauses to prevent HTTP 429. | Integration |
| 5 | D6 Data | Image/Video recorded in SQLite catalog | High | Catalog DB stores both `original_prompt` and `expanded_prompt` for full audit trail. | Unit |
| 6 | D9 Transport| Gemini returns truncated or invalid JSON payload | Low | Log warning, discard invalid expansion, fall back to raw prompt. | Unit |
| 7 | D11 Input | Raw prompt already exceeds 3000 chars | Low | Bypass expansion entirely to avoid truncation; proceed with raw prompt. | Unit |
| 8 | D11 Input | Expanded prompt exceeds Flow's 4000-character limit | Medium | Truncate expanded prompt safely to 3500 chars and append ellipsis. | Unit |
| 9 | D12 Obs | Structured logging contract | Low | Emit `prompt_expanded` event with keys `original_len`, `expanded_len`, and `latency_ms`. | Unit |

---

## Must-cover before merge (Critical + High)
1. **D6 Data**: Update the SQLite data layer to store `expanded_prompt` without losing historical data.
2. **D7 Error**: Verify that any network/API failure on the expansion path degrades gracefully and falls back to the original prompt, rather than crashing the generation.

## Suggested BDD scenarios

```gherkin
Feature: Prompt Expansion
  Scenario: Graceful degradation on missing API key
    Given the environment variable "GFLOW_CLI_GEMINI_API_KEY" is unset
    When I run "gflow image t2i 'a cute cat' --expand"
    Then the command should complete successfully with exit code 0
    And the generated image should use the raw prompt "a cute cat"
    And a warning log should indicate the expansion was skipped

  Scenario: Successful prompt expansion
    Given the environment variable "GFLOW_CLI_GEMINI_API_KEY" is set to a valid key
    And the Gemini API mock returns an expanded prompt
    When I run "gflow image t2i 'a cute cat' --expand"
    Then the command should complete successfully
    And the generated image should use the expanded prompt
```
