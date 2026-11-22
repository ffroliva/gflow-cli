# Scenario Analysis — Per-Generation Credit Cap (`--max-credits`, #1355)

- **Issue:** [#1355](https://github.com/lanesket/gflow-cli/issues/1355) — `feat(credits): hard per-generation credit cap read from Flow's own quote (--max-credits)`
- **Phase 0 spike:** [`docs/superpowers/spikes/2026-11-20-credit-quote-before-submit.md`](../../spikes/2026-11-20-credit-quote-before-submit.md)
- **Plan:** [`PLAN.md`](PLAN.md)
- **Date:** 2026-11-20

---

## Coverage Map (13 Dimensions)

| Dimension | Applicability | Scenarios |
|---|---|---|
| D1 — Transport & Network Edge Cases | Applicable (labs `projectInitialData` `creditMapping` missing or malformed) | S4, S8 |
| D2 — Authentication & Session Lifecycle | Indirect (session expiry happens before composer settings open; existing exit 4/40 covers it) | — |
| D3 — Browser Automation & Playwright Edge Cases | Primary (migrated settings pane `flow-credit-cost-label`, agent-only `LIVE_GATE` title, locale tokens, decoy numbers, missing DOM anchor) | S1, S3, S5, S6, S7, S15 |
| D4 — Concurrency & Race Conditions | Applicable (cumulative per-command budget deduction across `video chain` links and `movie run` scenes) | S9, S10 |
| D5 — File System & Storage | Applicable (`movie run` handoff/state persistence and `chain` partial output when a later link exceeds remaining budget) | S9, S10 |
| D6 — SQLite & Data Layer | Applicable (`record_failed_operation_safe` records pre-submit `CreditCapExceededError` with exit 45) | S14 |
| D7 — Error Classification & Exit Codes | Primary (`CreditCapExceededError` exit 45 in both legacy mode 1 and `GFLOW_CLI_EXIT_CODES=2`, `charged: "no"`, `next_action: "fix_input"`, `quoted`, `max_credits`, distinct from `InsufficientCreditsError` exit 37) | S1, S3, S4, S9, S11 |
| D8 — Security & Privacy | Applicable (no account identifiers or cookies in `CreditCapExceededError` details or structured logs) | S14 |
| D9 — Google Flow API Contract Drift | Primary (Flow omits or renames `flow-credit-cost-label`, changes `creditMapping`, or serves a route without a readable pre-submit quote -> fail closed) | S3, S4, S5, S8 |
| D10 — Cross-Platform Differences | Indirect (pure integer regex extraction and RFC 9457 JSON formatting are OS-independent) | — |
| D11 — CLI UX & Input Validation | Primary (`--max-credits` vs `GFLOW_CLI_MAX_CREDITS` precedence, `0` vs negative integer validation, `--dry-run` output, `--json` Problem Details extensions) | S11, S12, S13 |
| D12 — Observability & Debugging | Applicable (`structlog` events `migrated.credit_quote_checked` and `error_raised` with `quoted` and `max_credits`) | S14 |
| D13 — MCP Tool Layer Twin | Primary (`gflow_generate_video` `max_credits` parameter, `_build_video_payload`, worker `codec.py` round-trip, `daemon.py` env fallback, CLI↔MCP parity test gate, MCP e2e refusal twin) | S2, S12 |

---

## Severity-Ranked Scenario Table

| ID | Dimension | Severity | Scenario | Trigger / Precondition | Expected Behavior | Exit Code / Error Class |
|---|---|---|---|---|---|---|
| S1 | D3 / D7 | **Critical** | Migrated classic composer quote exceeds `--max-credits` | `flow.google.com` classic composer (`t2v`/`i2v`/`r2v`/`generate`); `.cdk-overlay-pane flow-prompt-box-settings div.settings-content > flow-credit-cost-label.settings-credit-cost` quotes `4` credits (`omni-flash` 4s x1) while `--max-credits 3` is set | Refuses inside `apply_video_settings` before closing the settings pane, uploading frames, filling the prompt, or clicking submit. Zero `batchGenerateVideos` RPCs fire; account credit balance is unchanged | `CreditCapExceededError` — Exit **45** (mode 1 & mode 2), `charged: "no"`, `next_action: "fix_input"`, `quoted: 4`, `max_credits: 3` |
| S2 | D13 | **Critical** | MCP `gflow_generate_video` credit-cap refusal (`wait=True` and `wait=False` queued) | MCP caller passes `max_credits=3` (or relies on `GFLOW_CLI_MAX_CREDITS=3` in worker environment) for a 4-credit video request | `max_credits` round-trips through `_build_video_payload` and `worker/codec.py::build_video_request`; worker refuses before submit and returns RFC 9457 error payload with `type: "https://gflow-cli.dev/errors/credit-cap-exceeded"`, `exit_code: 45`, `charged: "no"`, `quoted: 4`, `max_credits: 3` | `CreditCapExceededError` — Exit **45** in MCP error envelope |
| S3 | D3 / D9 | **Critical** | Fail-closed when DOM quote element is missing, hidden, or has no integer | `--max-credits 10` is set on `flow.google.com` classic composer, and `flow-credit-cost-label.settings-credit-cost` is absent from `.cdk-overlay-pane` or renders text without digits | Refuses before submit rather than falling back to a hardcoded price table; reports that Flow did not expose a readable pre-submit credit quote | `CreditCapExceededError` — Exit **45**, `charged: "no"`, `next_action: "fix_input"`, `quoted: None`, `max_credits: 10` |
| S4 | D9 / D7 | **Critical** | Fail-closed on unquoted routes (`flow.google.com` `video extend` and `labs.google` UI video composer) | `--max-credits 10` is set on `gflow video extend` when served `flow.google.com` (`migrated_extend.py`, where the extend menu shows no quote) or on `ui_automation_video.py` `route == "labs"` (where `ClassicFlowUiDriver` has no quote anchor) | Refuses before clicking the extend action or submitting the prompt; states that the active Flow route does not provide a pre-submit credit quote | `CreditCapExceededError` — Exit **45**, `charged: "no"`, `next_action: "fix_input"`, `quoted: None`, `max_credits: 10` |
| S5 | D3 / D9 | **Critical** | Agent-only composer (#799) confirmation-gate quote enforcement and rejection | `flow.google.com` agent-only composer (`agent_only_composer.py`) with `max_credits=5`; Flow's `LIVE_GATE` (`flow-permission-message p.title`) quotes `10` credits (or `GFLOW_CLI_AGENT_CONFIRM=never` was configured) | If `GFLOW_CLI_AGENT_CONFIRM=never`, refuses before clicking submit (`quoted=None`, `charged="no"`). Otherwise forces `confirm="always"` in settings, reads trailing integer `10` from `p.title`, clicks `REJECT_ROW` (`_reject_gate`), and raises `CreditCapExceededError` | `CreditCapExceededError` — Exit **45**, `charged: "no"` (or `"maybe"` with `next_action: "reconcile"` if `REJECT_ROW` fails to dismiss the gate), `quoted: 10`, `max_credits: 5` |
| S6 | D3 | **High** | Compliant generation (`quoted <= max_credits`) proceeds and completes | `flow.google.com` classic composer quotes `4` credits (`omni-flash` 4s x1) and `--max-credits 4` (or higher) is passed | Settings pane closes normally, generation submits and downloads clip, and `VideoResult.quoted_credits` is populated with `4` | Exit **0** |
| S7 | D3 | **High** | Locale-invariant integer extraction across `en`, `pt-BR`, and non-Latin templates | `flow-credit-cost-label.settings-credit-cost` renders `"Generating will use 12 credits"` (`en`), `"A geração vai usar 12 créditos"` (`pt-BR`), or agent gate renders `"Сгенерировать 1 видео за бонусы (12)?"` (`ru`) | Structural selector locates the dedicated quote node and extracts integer `12` without matching any translated word | Parsed `quoted == 12` |
| S8 | D1 / D9 | **High** | Labs direct-wire `video extend` pre-flight and API credit cap | `gflow video extend --segments 2 --max-credits 15` on labs direct-wire (`_extend_session` / `client.extend_video`), where `projectInitialData` `creditMapping` quotes `10` credits per segment (`total_cost = 20`) | `_extend_session` refuses before creating a scene or minting reCAPTCHA (`quoted=20, max_credits=15`). Direct callers of `client.extend_video(max_credits=5)` also check `unit_cost <= max_credits` before reCAPTCHA mint. If `unit_cost is None`, fails closed (`quoted=None`) | `CreditCapExceededError` — Exit **45**, `charged: "no"`, `quoted: 20`, `max_credits: 15` |
| S9 | D4 / D5 / D7 | **High** | `video chain` cumulative per-command cap across links | 3-link `gflow video chain --max-credits 6` where each link quotes `4` credits | Link 0 (`quoted=4 <= remaining 6`) succeeds and spends 4 (`remaining=2`). Link 1 receives `max_credits=2`, sees `quoted=4 > 2`, and refuses before link 1 submits. `chain.py` preserves link 0's output and upgrades the raised `CreditCapExceededError` to `charged="yes"`, `next_action="reconcile"`, `completed_media_ids=[link0_id]`. (If link 0 itself exceeds `--max-credits`, exits 45 with `charged="no"`.) | `CreditCapExceededError` — Exit **45** (`charged: "no"` on link 0; `charged: "yes"`, `next_action: "reconcile"` on link `k > 0`) |
| S10 | D4 / D5 | **High** | `movie run` cumulative per-command cap across scenes (including `--continue-on-error`) | 2-scene `gflow movie run --max-credits 6` (with or without `--continue-on-error`) where each scene quotes `4` credits | Scene 1 completes (`spent=4`, `remaining=2`). Scene 2 receives `max_credits=2`, refuses before submit with `CreditCapExceededError(quoted=4, max_credits=2)`, writes handoff/state for scene 1, and halts immediately even when `--continue-on-error` is active so no subsequent scene can spend remaining budget | `CreditCapExceededError` — Exit **45** |
| S11 | D7 / D11 | **High** | Exit-code mode parity (`GFLOW_CLI_EXIT_CODES=1` vs `2`) and distinction from `InsufficientCreditsError` | `CreditCapExceededError` is formatted under default exit codes and under `GFLOW_CLI_EXIT_CODES=2`; compared against `InsufficientCreditsError` (exit 37) | `CreditCapExceededError` resolves to exit **45** in both modes, type slug `credit-cap-exceeded`, `next_action: "fix_input"`, and Problem Details members `quoted` (`int \| None`) and `max_credits` (`int`). `InsufficientCreditsError` remains exit **37** with `next_action: "wait"` | Exit **45** vs Exit **37** |
| S12 | D11 / D13 | **High** | Boundary & precedence validation (`0`, negative values, flag vs env, unset) | (a) `--max-credits 0` against quote `4` (or quote `0`); (b) `--max-credits -1` or `GFLOW_CLI_MAX_CREDITS=-1` or `GenerateVideoRequest(max_credits=-1)`; (c) `--max-credits 8` with `GFLOW_CLI_MAX_CREDITS=4`; (d) both unset | (a) `--max-credits 0` is a valid zero-spend ceiling: refuses quote `4 > 0` with exit 45 (`charged="no"`), allows quote `0 <= 0`; (b) negative values fail input validation (Click `IntRange(min=0)` exit 2, Pydantic `ge=0` / `GenerateVideoRequest` `ValueError`); (c) explicit CLI/MCP value `8` overrides env `4`; (d) unset (`None`) skips cap enforcement and does not fail if the quote element is absent | Exit **45** / Exit **2** / Exit **0** |
| S13 | D11 | **Medium** | `--dry-run` output on `video chain`, `video extend`, and `movie run` | User passes `--dry-run --max-credits 12` to `video chain`, `video extend`, or `movie run` | `--dry-run` opens no browser and makes no network requests; the dry-run summary includes `max_credits` (`12`) alongside the existing plan metadata and exits 0 | Exit **0** |
| S14 | D6 / D8 / D12 | **Medium** | Failed-operation telemetry & structured log hygiene on refusal | A generation refuses pre-submit with `CreditCapExceededError(quoted=12, max_credits=8)` | `record_failed_operation_safe` records `error_class="CreditCapExceededError"`, `exit_code=45`; `structlog` emits `migrated.credit_quote_checked` with `quoted=12, max_credits=8` and no PII | Exit **45** |
| S15 | D3 | **Medium** | Decoy numbers inside `.cdk-overlay-pane` (`4s`, `8s`, `720p`, `x4`, `Omni 1.2`) | `.cdk-overlay-pane` contains model, aspect, count, and duration buttons carrying integers before `flow-credit-cost-label.settings-credit-cost` | Selector scopes strictly to `flow-prompt-box-settings div.settings-content > flow-credit-cost-label.settings-credit-cost` and never reads numbers from sibling buttons | Exact quote integer parsed |

---

## Gherkin Specification

### 1. Browser E2E Feature (`tests/features/credit_cap_refusal.feature`)

Bound from `tests/e2e/test_credit_cap_refusal_bdd.py` per [`docs/E2E_TESTING.md`](../../../E2E_TESTING.md) § BDD-bound e2e. Scenarios 1 and 2 are tagged `@e2e @e2e_auth` ($0 credit cost, real browser session against `flow.google.com`, with a Playwright route-abort backstop on `batchGenerateVideos` / `batchexecute` generation mutations and a before/after `gflow credits` balance check). Scenario 3 is tagged `@e2e @e2e_video` (opt-in via `GFLOW_CLI_E2E_RUN_VIDEO=1`, 4 Veo credits).

```gherkin
@e2e @e2e_auth
Feature: Pre-submit credit cap enforcement against Flow's live quote
  As an operator or autonomous agent running billed video commands
  I want gflow to read Flow's own pre-submit credit quote and refuse when it exceeds --max-credits
  So that an unexpected model, duration, or count multiplier cannot spend past my budget

  Scenario: Migrated composer refuses before submit when Flow's quote exceeds --max-credits
    Given a live Flow profile served flow.google.com with a recorded credit balance
    And a Playwright route backstop that aborts any video generation submission request
    When the operator requests a 4-second x1 omni-flash video with "--max-credits 3"
    Then gflow reads Flow's settings-pane quote of 4 credits before clicking submit
    And the command fails with CreditCapExceededError and exit code 45
    And the Problem Details payload reports charged "no", next_action "fix_input", quoted 4, and max_credits 3
    And no video generation submission request was sent over the network
    And the profile's credit balance is unchanged

  Scenario: MCP gflow_generate_video refuses before submit when Flow's quote exceeds max_credits
    Given a live Flow profile served flow.google.com with a recorded credit balance
    And a Playwright route backstop that aborts any video generation submission request
    When an MCP client calls gflow_generate_video for a 4-second x1 omni-flash video with max_credits 3
    Then the MCP tool returns an error envelope with type "https://gflow-cli.dev/errors/credit-cap-exceeded" and exit_code 45
    And the MCP error envelope reports charged "no", quoted 4, and max_credits 3
    And no video generation submission request was sent over the network
    And the profile's credit balance is unchanged

  @e2e_video
  Scenario: Migrated composer submits and completes when Flow's quote equals --max-credits
    Given a live Flow profile served flow.google.com with video E2E enabled
    When the operator requests a 4-second x1 omni-flash video with "--max-credits 4"
    Then gflow reads Flow's settings-pane quote of 4 credits and proceeds with submission
    And the video generation completes with exit code 0 and downloads a non-empty MP4 file
```

### 2. Offline BDD Feature (`tests/features/credit_cap.feature`)

Bound from `tests/features/test_credit_cap_steps.py` (untagged, runs in the default offline pytest suite).

```gherkin
Feature: Credit quote parsing, fail-closed enforcement, and per-command budget deduction
  As a gflow-cli user or MCP agent
  I want deterministic pre-submit credit-cap checks across locales, routes, and multi-step commands
  So that credit caps fail closed on unknown quotes and bound total command spend

  Scenario Outline: Settings-pane credit quote parser extracts the integer across locales
    Given a settings-pane credit cost label containing "<label_text>"
    When gflow parses the pre-submit credit quote
    Then the parsed credit quote is <expected_quote>

    Examples:
      | label_text                       | expected_quote |
      | Generating will use 4 credits    | 4              |
      | A geração vai usar 12 créditos   | 12             |
      | Generating will use 0 credits    | 0              |

  Scenario: Settings-pane credit quote enforces fail-closed when the anchor is missing or non-numeric
    Given a migrated settings pane where the credit cost label is absent
    When gflow enforces a max_credits cap of 10
    Then CreditCapExceededError is raised with quoted None, max_credits 10, charged "no", and exit code 45

  Scenario: CreditCapExceededError preserves exit code 45 in both legacy and v2 exit-code modes
    Given a CreditCapExceededError raised for a quote of 8 against max_credits 4
    When the error is formatted in default exit-code mode and in GFLOW_CLI_EXIT_CODES=2 mode
    Then the resolved exit code is 45 in both modes
    And the Problem Details payload includes quoted 8, max_credits 4, charged "no", and next_action "fix_input"

  Scenario: Multi-link video chain deducts each link's quote from the per-command budget
    Given a 2-link video chain with a command max_credits cap of 6
    And link 0 quotes 4 credits and completes with media_id "media-link-0"
    And link 1 quotes 4 credits against the remaining budget of 2
    When gflow executes the video chain
    Then link 1 is refused before submission with CreditCapExceededError
    And the chain error reports quoted 4, max_credits 2, charged "yes", next_action "reconcile", and completed_media_ids "media-link-0"

  Scenario: MCP video payload codec round-trips max_credits to GenerateVideoRequest
    Given an MCP gflow_generate_video call with max_credits 12
    When the worker codec builds the GenerateVideoRequest from the queued job payload
    Then the resulting GenerateVideoRequest has max_credits 12
```

---

## Test Implementation Routing

| Scenario IDs | Target Test File | Layer |
|---|---|---|
| S1, S2, S6 (live browser) | `tests/features/credit_cap_refusal.feature` + `tests/e2e/test_credit_cap_refusal_bdd.py` | E2E BDD (`@e2e @e2e_auth` for S1/S2 $0 refusal; `@e2e @e2e_video` for S6 paid run) |
| S3, S7, S9, S11, S12 (offline BDD) | `tests/features/credit_cap.feature` + `tests/features/test_credit_cap_steps.py` | Offline BDD (`pytest-bdd`, default suite) |
| S1, S3, S4, S5, S6, S7, S8, S11, S12, S15 | `tests/api/test_credit_cap.py`, `tests/test_errors_classification.py`, `tests/test_problem_details_extensions.py` | Offline unit tests |
| S8, S9, S10, S12, S13, S14 | `tests/test_cli_video_credit_cap.py`, `tests/test_cli_movie_credit_cap.py` | Offline CLI / runner tests |
| S2, S12 (MCP schema & parity) | `tests/mcp/test_cli_parity.py`, `tests/mcp/test_server.py`, `tests/worker/test_codec.py` | Offline MCP & worker unit tests |
