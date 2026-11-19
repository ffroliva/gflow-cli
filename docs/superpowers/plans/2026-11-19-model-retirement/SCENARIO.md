# Scenario: model retirement, slice 1 (#1375)

Scope: the slice the design review (#1375) cleared for before 5 Dec 2026:

- **Sweep.** Remove the leaving models (Omni 1.1 Flash `abra`, Veo 3.1 Fast `veo_3_1_fast`, Veo 3.1 Quality `veo_3_1_quality`) from defaults, examples, docs and e2e tests.
- **Static date.** Add a `retires_on` date, shown by `gflow models` and `gflow_list_models`.
- **Warning.** Warn when a retiring model is selected.
- **Drift test.** Add a test against a recorded `HTrJv` fixture.

No runtime `HTrJv` read is in scope; that is slice 2.

## Coverage map

| Dimension | Active? | Why |
|---|---|---|
| D1 Auth | no | no new session use |
| D2 WAF/reCAPTCHA | no | no new requests |
| D3 Selector / locale | **yes** | menu matchers stay static; a retired model leaves the menu |
| D4 Batch / resume | **yes** | `movie run`, `run`, `video chain` and manifests can name a leaving model |
| D5 Concurrency | no | no shared state |
| D6 Data layer | no | `retires_on` is not persisted |
| D7 Error propagation | **yes** | what a retired model raises before and after 5 Dec |
| D8 Cross-platform | no | no new files at runtime (the fixture is test data) |
| D9 Transport | **yes** | the `HTrJv` fixture's shape and its drift |
| D10 Headless | no | |
| D11 Input validation | **yes** | aliases that resolve to a leaving model (`omni`, `flash`, `fast`, `quality`) |
| D12 Observability | **yes** | the warning event and its key names |
| D13 MCP parity | **yes** | `gflow_list_models`, `gflow_generate_video`, direct and queued paths |

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D11 | `--model omni` (or `flash`, `omni-flash`) after 5 Dec | **High** | Resolves per the alias decision (plan, decision 1). It never silently selects a different model than documented | Unit |
| 2 | D3/D7 | A retired model is requested after Flow removes it from the menu | **High** | The existing pre-submit refusal fires (`_select_model`, exit 11 today; see plan, decision 2), `charged: no`, and the message names the retirement and the offered alternatives | Unit + E2E (`e2e_auth`, menu read only) |
| 3 | D3 | A leaving model is requested **before** 5 Dec | **High** | The generation proceeds, and a warning naming the date is emitted once per run | Unit + BDD |
| 4 | D12 | Warning on the CLI with `--json` / `-o id` | **High** | The warning goes to stderr as a structlog event (`model.retiring`, keys `model`, `retires_on`); stdout stays a clean JSON or id | Unit |
| 5 | D13 | The same request over MCP `gflow_generate_video` (direct and `wait=False` queued) | **High** | The result carries the warning as a field; the queued path decodes it through `worker/codec.py` and stores it on the task result | Unit (MCP) |
| 6 | D13 | `gflow_list_models` / `gflow models list --json` | **High** | Each video model entry gains `retires_on` (ISO date or `null`); existing keys are unchanged; the tool `description=` names the field | Unit + parity |
| 7 | D9 | `HTrJv` fixture drift: Flow renames a family key, drops a family, or moves the date | **High** | The drift test fails, naming the family; a refreshed fixture with a changed date fails until `retires_on` is updated | Unit |
| 8 | D9 | The fixture payload shape changes (index 6 is no longer a 3-int list) | Medium | The parser returns `retires_on=None` for that family; the drift test reports the shape, it does not crash | Unit |
| 9 | D4 | `gflow movie template` / `movie run` manifest defaults to `omni-flash` | **High** | The template default is a model that is not leaving; old manifests that name a leaving model get the warning (scenario 3) | Unit |
| 10 | D4 | `video chain` with a leaving model | Medium | Same warning; the chain's own Omni refusal is unchanged | Unit |
| 11 | D11 | Date arithmetic at the boundary (the run starts on 4 Dec, local time ahead of UTC) | Medium | The warning uses the calendar date and says "leaving on 2026-12-05"; it never refuses on the date alone | Unit |
| 12 | D7 | Google postpones the retirement and the model stays in the menu after 5 Dec | Medium | Generation still works (no date-based refusal); the warning wording stays accurate ("Flow listed it as leaving on …") | Unit |
| 13 | D3 | The e2e tests that pin a leaving model run after 5 Dec | **High** | None of them depends on a leaving model; each moved to a model that stays, or skips with a reason naming the retirement | E2E (collect-only check offline) |

## Must-cover before merge (Critical + High)

1, 2, 3, 4, 5, 6, 7, 9, 13.

## Deferred (Medium + Low)

8, 10, 11 and 12 are covered by unit tests in this slice if they are cheap. Otherwise they are logged on #1375.

## Suggested BDD scenarios

```gherkin
Feature: models Flow lists as leaving are flagged before they go (#1375)

  Scenario: the model catalog shows when a model leaves
    When the model catalog is built
    Then Veo 3.1 Fast, Veo 3.1 Quality and Omni 1.1 Flash carry retires_on 2026-12-05
    And every other video model carries retires_on null

  Scenario: selecting a leaving model warns without blocking
    Given today is before 2026-12-05
    When a generation request selects Veo 3.1 Fast
    Then a "model.retiring" warning names the model and 2026-12-05
    And the request is not refused

  Scenario: the recorded Flow catalog agrees with gflow's static dates
    Given the recorded HTrJv fixture
    Then every gflow video model maps to a family key present in it
    And each family's retirement date equals gflow's retires_on
```

These run offline (no Flow surface), so they are untagged and bound from `tests/features/`. The browser half of scenario 2 is the existing pre-submit menu refusal, which is already e2e-covered. Re-run it after 5 Dec against a real retired model.

## Known-issues cross-reference

- **Duration cohorts.** KNOWN_ISSUES records that capabilities differ by cohort. The fixture is one account and one tier, so the drift test asserts only family keys and retirement dates, never durations or cost.
- **Agent-only composer refusals** (`unported_video`) key on `VIDEO_MODEL_MENU_MATCHERS`. Removing a model from the matchers would change those refusals. This slice keeps the matchers and only adds the date.
