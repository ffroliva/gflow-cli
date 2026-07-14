# HLD & Implementation Plan: Asset Model Sheets & Storyboarding

> **Target Repository:** [gflow-cli](file:///C:/development/github/gflow-cli)
>
> **For agentic workers:** Run `/gflow:status --feature storyboarding-and-visual-curation` to find the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Abstract the model-sheet visual consistency pattern (generating Subject and Environment sheets) and integrate it natively into the `gflow movie` command pipeline.

---

## High-Level Design (HLD)

Visual consistency is the primary challenge in AI video generation. This design introduces **Model Sheets** as first-class citizens in the `gflow movie` manifest:

```
                  ┌──────────────────────┐
                  │      movie.toml      │
                  └──────────┬───────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │ gflow movie generate-sheets  │
              └──────────────┬───────────────┘
                             │
                             ├──────────────────────────┐
                             ▼                          ▼
                  ┌─────────────────────┐    ┌─────────────────────┐
                  │ Subject Reference   │    │ Environment Sheet   │
                  │ (convertible_sheet) │    │ (desert_road_sheet) │
                  └──────────┬──────────┘    └──────────┬──────────┘
                             │                          │
                             ▼                          ▼
              ┌─────────────────────────────────────────┴────────────┐
              │     gflow movie run (I2V / Ref generation)           │
              └──────────────────────────────────────────────────────┘
```

1. **Asset Declaration:** Users declare `[assets]` in their `movie.toml` manifest file representing Subjects or Environments.
2. **Sheet Generation:** The CLI generates turnaround sheets for subjects (e.g. car turnaround) and layout sheets for environments, then uploads them to Flow to retrieve reference IDs.
3. **Storyboard Execution:** Video generation scenes consume these uploaded references as image-to-image or character references, locking in consistent geometry, style, and textures.

---

## File Structure

### New Files
- [test_movie_assets.py](file:///C:/development/github/gflow-cli/tests/tools/test_movie_assets.py) — Unit tests covering manifest asset parsing and sheet generation logic.

### Modified Files
- [movie_manifest.py](file:///C:/development/github/gflow-cli/src/gflow_cli/movie_manifest.py) — Extend manifest parser to support the `[assets]` configuration table.
- [composition.py](file:///C:/development/github/gflow-cli/src/gflow_cli/composition.py) — Integrate assets into prompt compilation and handoff payloads.
- [cli_movie.py](file:///C:/development/github/gflow-cli/src/gflow_cli/cli_movie.py) — Click command to generate sheets and attach reference IDs.

---

## Implementation Tasks

### Task 1 — movie.toml Asset Manifest Parsing

**What:** Update the manifest schemas and parsing logic to support the `[assets]` configuration.

**Steps:**
- [ ] Define the `AssetDef` representation (with fields: `type`, `description`, `layout`) in [movie_manifest.py](file:///C:/development/github/gflow-cli/src/gflow_cli/movie_manifest.py).
- [ ] Extend `MovieManifest` to parse and validate `[assets.<name>]` tables.
- [ ] Allow characters and scenes to bind reference keys to these asset definitions.

**Tests (red to green):**
- [ ] `test_manifest_parses_assets` in [test_movie_assets.py](file:///C:/development/github/gflow-cli/tests/tools/test_movie_assets.py) — validates parsing.
- [ ] `test_invalid_asset_type_raises` — asserts validation errors on missing/malformed asset keys.

---

### Task 2 — CLI Sheet Generation Command

**What:** Implement the command to generate sheet images, upload them, and record reference IDs in the state.

**Steps:**
- [ ] Implement `gflow movie generate-sheets [manifest]` subcommand in [cli_movie.py](file:///C:/development/github/gflow-cli/src/gflow_cli/cli_movie.py).
- [ ] Map asset description and layout parameters to `t2i` generation commands.
- [ ] Generate the sheet images, save them in `out/assets/`, and upload them to Flow to retrieve reference IDs.
- [ ] Record the generated reference IDs in the movie's local state file.

**Tests (red to green):**
- [ ] `test_generate_sheets_command` — mocks generation and asserts reference IDs are recorded to state.

---

### Task 3 — Storyboard Pipeline Integration

**What:** Bind the generated reference assets as inputs during scene video generation.

**Steps:**
- [ ] Update `compose_prompt` in [composition.py](file:///C:/development/github/gflow-cli/src/gflow_cli/composition.py) to incorporate asset descriptions.
- [ ] Update video generation inside [cli_movie.py](file:///C:/development/github/gflow-cli/src/gflow_cli/cli_movie.py) to load asset reference IDs from the state file and bind them as image-to-image/character reference parameters.
- [ ] Output the storyboard mappings to the final handoff manifest.

**Tests (red to green):**
- [ ] `test_storyboard_sends_asset_references` — verifies generated asset reference IDs are bound to video generation calls.

---

## Definition of Done

- [ ] All task steps checked off.
- [ ] `/gflow:check` runs successfully (ruff / format / pyright / pytest).
- [ ] Tests cover at least 80% of the new code paths.
- [ ] [KNOWN_ISSUES.md](file:///C:/development/github/gflow-cli/KNOWN_ISSUES.md) and user guides updated.
