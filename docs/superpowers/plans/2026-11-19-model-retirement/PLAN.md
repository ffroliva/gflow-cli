# Model Retirement (slice 1) Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature 2026-11-19-model-retirement` to find
> the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Before 5 Dec 2026, gflow tells users which video models Flow is retiring (`gflow models`,
`gflow_list_models`, a warning on use), no default, example, doc or e2e test depends on a leaving
model, and a test fails when Flow's own catalog (`HTrJv`) disagrees with gflow's static dates.

**Architecture:**
- **New pure module `api/model_catalog.py`.** It holds the static retirement dates, the map from
  gflow's `VideoModel` to Flow's family key, and a parser for `HTrJv` model families, with no I/O.
  The parser feeds only an offline drift test in this slice; slice 2 reuses it for a live read.
- **The warning comes from one generation chokepoint.** It is a structlog event on the CLI's
  stderr and a result field over MCP.
- **Unchanged:** the static menu matchers, the `--model` choices and the pre-submit menu
  refusal's exit code (11, with a better message). Slice 2 (the runtime `HTrJv` read) is out of
  scope.

**Predict verdict:** CAUTION, about 6/10, for the narrowed design. See the review comment on
#1375. Slice 1 is the part that review cleared without further measurement.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | A date-based refusal blocks a model Flow still offers (the date may move, and it has no time zone) | Never refuse on the date. Warn only; Flow's live menu decides (existing exit-11 refusal) |
| High | Wire display names used to select menu items could bill the wrong model | Out of scope. Matchers stay static; the fixture's names are compared in tests only |
| Medium | Usage-slot meanings (cost, duration, reference limits) rest on one capture | The drift test asserts family keys and retirement dates only |
| Medium | The warning pollutes `--json` / `-o id` stdout | The warning is a stderr structlog event; MCP gets a result field |
| Medium | The queued MCP path drops the warning | The warning is added in the worker path too, and asserted on a queued task's result |
| Low | The fixture goes stale silently | The recording script and its date sit in the fixture header; a refreshed fixture with a moved date fails the test until `retires_on` is updated |

---

## File structure

### New files
```
src/gflow_cli/api/model_catalog.py
  RETIRES_ON + retires_on(model), FAMILY_KEY (VideoModel -> HTrJv family key),
  ModelFamily value object, parse_htrjv_families(payload) (pure)
tests/api/test_model_catalog.py
  unit tests: dates, family map, parser on the fixture and on malformed shapes
tests/fixtures/flow_htrjv_model_families.json
  recorded HTrJv families, trimmed to [name, usage keys, enabled, family key, alias, _, date]
scripts/dev/record_htrjv_fixture.py
  regenerates the fixture from a live direct read (reuses spike_htrjv_direct_read's path)
tests/features/model_retirement.feature + tests/features/test_model_retirement_steps.py
  the three BDD scenarios from SCENARIO.md (offline, untagged)
```

### Modified files
```
src/gflow_cli/cli_models.py            video entries gain "retires_on" (ISO date or null)
src/gflow_cli/mcp/tools.py             gflow_list_models description names retires_on;
                                       gflow_generate_video result gains the retirement warning
src/gflow_cli/worker/ (codec/daemon)   the queued path carries the warning onto the task result
<generation chokepoint>                one call that emits "model.retiring" (located in Task 4)
src/gflow_cli/api/transports/_mc_settings_pane.py
                                       the exit-11 "not offered" message names a retired model
                                       and the offered alternatives
src/gflow_cli/cli_movie.py             template default model moves off omni-flash
src/gflow_cli/mcp/resources.py         agent guide prose: no leaving model as an example/default
docs/USAGE.md, docs/MCP.md, README.md, skills/gflow-cli/SKILL.md,
skills/video-production/SKILL.md (+ tasks.json), docs/USER_GUIDE.md, KNOWN_ISSUES.md
                                       examples off leaving models; retirement noted
tests/e2e/ (12 files)                  off leaving models: test_chain_e2e, test_crash_recovery_e2e,
                                       test_data_layer_e2e, test_i2v_flags_e2e,
                                       test_insufficient_credits_e2e, test_json_output_e2e,
                                       test_migrated_host_e2e, test_migrated_locale_e2e,
                                       test_migrated_omni_r2v_duration_bdd, test_tools_e2e,
                                       test_transports_e2e, test_video_r2v_ref_cap_e2e
CHANGELOG.md                           [Unreleased] Added + Changed
```

---

## Task 1 — Unit test scaffold (red)

**What:** Pin the catalog's retirement data, the family map and the parser before any code
exists.

**Files:** `tests/api/test_model_catalog.py`, `tests/cli/test_cli_models.py`

**Steps:**
- [x] Record the fixture first (Task 3's script, or trim `scripts/dev/_spike_out/htrjv_direct_*/a.json` by hand once), so the tests read real data.
- [x] Write the tests below against the not-yet-existing `api/model_catalog.py`.
- [x] Commit red, with the failing line in the message.

**Tests created (red):**
- [x] `test_leaving_models_retire_on_2026_12_05`: Omni 1.1 Flash, Veo 3.1 Fast and Veo 3.1 Quality are on 2026-12-05; every other `VideoModel` is `None`.
- [x] `test_every_video_model_maps_to_a_family_in_the_recorded_catalog`: each `VideoModel` appears in `FAMILY_KEY` with a key present in the fixture.
- [x] `test_static_dates_match_the_recorded_catalog`: for each family in the fixture, index 6 equals `retires_on`.
- [x] `test_parser_tolerates_a_changed_date_slot`: a non-date at index 6 gives `None` and does not raise (scenario 8).
- [x] `test_catalog_video_entries_carry_retires_on`: `build_catalog()["video"]["models"][*]["retires_on"]` is an ISO string or `None`, and the existing keys are unchanged (scenario 6).

## Task 2 — BDD scaffold (red)

**What:** The three offline scenarios from SCENARIO.md.

**Files:** `tests/features/model_retirement.feature`, `tests/features/test_model_retirement_steps.py`

**Steps:**
- [x] Copy the scenarios. Keep them untagged (no Flow surface). Two scenarios were added beyond SCENARIO.md's three: a model past its date is still not refused, and a model Flow is not retiring gets no warning (decision 3 and scenario 12).
- [x] Inject "today" through a clock argument, never `time.time()` in a parametrize (auto-memory `required-checks-wait-counts-passes`).
- [x] Commit red.

## Task 3 — `api/model_catalog.py` + fixture recorder

**What:** The pure module, plus the script that regenerates the fixture.

**Steps:**
- [ ] `RETIRES_ON: Mapping[VideoModel, date]` and `retires_on(model) -> date | None`.
- [ ] `FAMILY_KEY: Mapping[VideoModel, str]` (`omni_flash`→`abra`, `omni_1_2_flash`→`macaron_cookie`, `veo_3_1_lite`→`veo_3_1_lite`, `veo_3_1_fast`→`veo_3_1_fast`, `veo_3_1_quality`→`veo_3_1_quality`, `veo_3_1_lite_lower_priority`→`veo_3_1_lite_low_priority`; measured 2026-11-19).
- [ ] `@dataclass(frozen=True) ModelFamily(key, display_name, enabled, retires_on)` and `parse_htrjv_families(payload) -> list[ModelFamily]`, which tolerates shape drift (scenario 8).
- [ ] `scripts/dev/record_htrjv_fixture.py`: one direct read through the `flow_wire` path, trimmed to the family fields and the usage keys only. Write a header with the date and the account tier, but no account id.
- [ ] Tasks 1–2 green.

## Task 4 — Retirement warning at one chokepoint

**What:** A generation that selects a leaving model emits `model.retiring` (`model`, `retires_on`) once per run, and is never refused because of the date.

**Steps:**
- [ ] Locate the one place every video generation passes, CLI and the MCP worker alike (candidates: the `GenerateVideoRequest` construction in the service layer, or `FlowApiClient`'s video entry). Grep every caller of the chosen point first.
- [ ] Emit the structlog WARNING there. On the CLI it reaches stderr at the default level, and `--json` stdout is unchanged.
- [ ] Unit tests for scenarios 3, 4, 11 and 12 (date injected).

## Task 5 — CLI surface: `gflow models` + the exit-11 message

**Steps:**
- [ ] `build_catalog()` adds `retires_on`, and the table output gains a column.
- [ ] `_mc_settings_pane._select_model`'s "not offered" refusal (exit 11, `charged: no`): when `retires_on(model)` is past, the detail says Flow retired it on that date and lists the offered models. The exit code is unchanged (decision 2).
- [ ] Unit tests for scenarios 2 and 6.

## Task 6 — MCP surface mirror (never optional)

**Steps:**
- [ ] `gflow_list_models`: the `description=` names `retires_on`; the entries come from `build_catalog()`, so the data is automatic.
- [ ] `gflow_generate_video`: the direct result carries the warning (for example `"warnings": [{"type": "model_retiring", "model": …, "retires_on": …}]`). The queued path stores the same on the task result (`worker/codec.py`, worker daemon).
- [ ] The mirror axes are in `skills/check/SKILL.md` step 1b. Run `tests/mcp/test_cli_parity.py` and `test_server.py`.
- [ ] Unit tests for scenario 5, direct and queued.

## Task 7 — Sweep: defaults, examples, e2e

**Steps:**
- [ ] `cli_movie.py` template default moves to a model that is not leaving. Choose it from the movie path's needs (durations, cost) and record the choice in the decision log.
- [ ] `mcp/resources.py`, `docs/USAGE.md`, `docs/MCP.md`, `README.md`, `docs/USER_GUIDE.md`, `skills/gflow-cli/SKILL.md`, `skills/video-production/SKILL.md` + `tasks.json`: examples use `veo-lite` / `omni-1.2`, and the retirement is noted once where models are listed.
- [ ] Move the 12 e2e files to models that are not leaving, or skip them with a reason naming the retirement when the test is about a leaving model specifically (scenario 13). `pytest --collect-only -m e2e` stays clean.
- [ ] `KNOWN_ISSUES.md`: add an entry for the retirement, and the alias follow-up (decision 1).

## Task 8 — Gates, e2e, docs, CHANGELOG

**Steps:**
- [ ] `/gflow:check` green, including `generate_website_docs.py --check` and `generate_plugin_skills.py --check` (the skills changed).
- [ ] e2e: `tests/e2e/test_migrated_host_e2e.py` (and one migrated t2v) on the swept models, plus a $0 `gflow models list --json` check.
- [ ] CHANGELOG `[Unreleased]`: Added `retires_on` and the warning. Changed the movie template default and the doc examples.
- [ ] Fresh `/code-review xhigh` + `/ponytail-review xhigh`, one fix round, then `/gflow:pr-council-review`.

## Follow-up (separate PR, first release after 5 Dec)

- [ ] Repoint the short aliases `omni` and `flash` to Omni 1.2 Flash (decision 1). `omni-flash` stays the explicit Omni 1.1 name until Flow drops it. Filed as its own issue with the date.

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Docs updated (`USAGE.md` / `MCP.md` / skills)
- [ ] BDD feature file covers all Critical + High scenarios from `SCENARIO.md`
- [ ] No `# TODO` in diff without a tracked issue link
- [ ] Task 1's red tests failed before Task 3
- [ ] Worktree clean: no untracked scratch files, no leftover clones or temp dirs

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-19 | Slice 1 is static data, a warning and a drift test; no runtime `HTrJv` read | Design review on #1375: the direct read's window (about 1.5–2.5 h after a browser visit) and the single capture make a live read slice-2 work | Three-layer runtime catalog now (over-built for one date) |
| 2026-11-19 | Never refuse on the retirement date; warn only | The date has no time zone and may move. Flow's live menu already refuses a missing model with exit 11 before submit | Refusing from the static date |
| 2026-11-19 | Keep the menu matchers static | The direct read is `hl=en`, while the menu renders in the user's locale; the matcher is a substring match ("Nano Banana 2" ⊂ "Nano Banana 2.1") | Selecting by wire display name |
| 2026-11-19 | Decision 1: `omni`/`flash`/`omni-flash` keep selecting Omni 1.1 until 5 Dec; repoint the short aliases to Omni 1.2 in the first release after | No silent model or price change while Omni 1.1 is still offered (4 s costs 12 credits on Omni 1.2 against 7 on Omni 1.1, per the 2026-11-19 capture) | Repointing now; never repointing |
| 2026-11-19 | Decision 2: keep exit 11 for "not offered", with a message naming the retirement | No contract change for scripts | A new class on exit 22 |
| 2026-11-19 | Decision 3: keep `veo-fast`, `veo-quality` and `omni-flash` accepted after 5 Dec; remove them in a later release once Flow has dropped them | Flow's live menu decides; a postponement keeps working | Removing them right after 5 Dec (Click usage error) |
