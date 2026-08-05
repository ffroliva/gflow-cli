# Character Entity Provenance Recording Implementation Plan (#402)

> **For agentic workers:** Run `/gflow:status --feature issue-402-entity-provenance` to find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Expose `--reference-entity` and `--reference-entity-name` CLI flags on `gflow video` commands (`t2v`, `i2v`, `r2v`) and ensure character entity provenance is recorded in `operations.metadata_json` across image and video generation runs.

**Architecture:** Add `_reference_entity_option` and `_reference_entity_name_option` Click options to `src/gflow_cli/cli_video.py` and forward them to `GenerateVideoRequest`. Verify `OperationRecorder` writes `entity_ids` and `entity_names` into `metadata_json`.

**Predict verdict:** GO (confidence 10/10)

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Low | CLI parameter name mismatch between image and video commands | Reuse shared option definitions matching `cli_image.py` |

---

## File structure

### New files
```
tests/test_entity_provenance.py
  Unit tests for character entity provenance recording in recorder.py and cli_video.py
tests/features/entity_provenance.feature
  BDD Gherkin specification for character entity provenance
```

### Modified files
```
src/gflow_cli/cli_video.py
  Add --reference-entity and --reference-entity-name options to video commands
docs/USAGE.md
  Document --reference-entity on video commands
CHANGELOG.md
  Add entry under [Unreleased]
```

---

## Task 1 — Unit & BDD Test Scaffold (test scaffold)

**What:** Create red unit and BDD tests covering CLI video reference-entity options and recorder `metadata_json` verification.

**Files:**
- `tests/test_entity_provenance.py`
- `tests/features/entity_provenance.feature`

**Steps:**
- [ ] Write unit test in `test_entity_provenance.py` asserting `metadata_json` carries `entity_ids` and `entity_names`
- [ ] Write unit test for `gflow video` CLI parsing of `--reference-entity`
- [ ] Write BDD scenario for character provenance recording

**Tests created (red):**
- [ ] `test_recorder_persists_character_entity_metadata`
- [ ] `test_cli_video_accepts_reference_entity_options`

---

## Task 2 — CLI Video Option Wiring Implementation

**What:** Add `--reference-entity` and `--reference-entity-name` options to `src/gflow_cli/cli_video.py`.

**Files:**
- `src/gflow_cli/cli_video.py`

**Steps:**
- [ ] Define `_reference_entity_option` and `_reference_entity_name_option` in `cli_video.py`
- [ ] Add options to `_shared_gen_tail_options` and `r2v` command
- [ ] Pass `reference_entities` and `reference_entity_names` to `GenerateVideoRequest` in `_run_t2v`, `_run_i2v`, `_run_r2v`

---

## Task 3 — MCP & Parity Verification

**What:** Verify CLI and MCP parity for video reference entities.

**Files:**
- `tests/mcp/test_cli_parity.py`

**Steps:**
- [ ] Run `tests/mcp/test_cli_parity.py` to confirm parity

---

## Task 4 — Documentation & Pre-Commit Gates

**What:** Update usage docs, changelog, and run all pre-commit quality gates.

**Files:**
- `docs/USAGE.md`
- `CHANGELOG.md`

**Steps:**
- [ ] Update `docs/USAGE.md` with `gflow video` `--reference-entity` options
- [ ] Add entry under `[Unreleased]` in `CHANGELOG.md`
- [ ] Run `/gflow:check` to ensure all 7 quality gates pass

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Docs updated (`docs/USAGE.md`)
- [ ] BDD feature file covers all Critical + High scenarios from `/gflow:scenario`
- [ ] No `# TODO` in diff without a tracked issue link
