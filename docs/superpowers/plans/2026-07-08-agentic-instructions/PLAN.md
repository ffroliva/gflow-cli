# API-Driven & Relational Agentic Instructions Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature agentic-instructions` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Implement a programmatic, API-driven, and relational instructions management system in the Google Flow Agentic transport, coordinating reference assets across individual generations and multi-scene movies.

**Architecture:** 
We will shift from DOM-based automation inside Playwright to direct REST API calls using `PATCH /v1/projects/{projectId}/agentInfo`, which is project-scoped and supports relational asset attachments (`imageReferenceMediaIds`). We will expose this relational schema on the image/video DTOs, integrate it with the background worker, add `movie.toml` scene-level mapping, and conclude with a systematic developer skills audit.

**Predict verdict:** GO — confidence 9/10

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Medium | API signature drift on Google Flow backend | Scope tests to verify response status and keep Playwright DOM fallback logic in place |
| Low | Invalid media ID attachments | Pre-flight check verifies that any attached `imageReferenceMediaIds` are active in the project's media catalog |

---

## File structure

### New files
```
tests/e2e/test_live_agentic_instructions.py
  Live verification tests for the instructions lifecycle and image generation.
```

### Modified files
```
src/gflow_cli/api/client.py
  Add patch_agent_info endpoint method.
src/gflow_cli/api/image.py
  Extend AgentInstruction and GenerateImageRequest DTOs.
src/gflow_cli/api/transports/drivers/agentic.py
  Update configure_image_settings to call patch_agent_info instead of driving DOM.
src/gflow_cli/movie_manifest.py
  Integrate scene-specific instructions sync before generating each scene's clips.
skills/*
  Audit and synchronize all agent skills with the latest codebase architecture.
```

---

## Task 1 — Unit Test & Mock Scaffold (test scaffold)

**What:** Create unit test mocks verifying relational instructions serialization and the `patch_agent_info` method wrapper.

**Files:**
- `tests/api/transports/drivers/test_agentic.py` — Mock testing for driver-API integration.
- `tests/worker/test_daemon.py` — Verify relational instructions extraction from background queue payload.

**Steps:**
- [x] Write mock DTO serialization tests for `AgentInstruction` containing `image_media_ids`.
- [x] Add assertions ensuring the driver correctly calls the `patch_agent_info` client method with structured payloads.

**Tests created (red):**
- [x] `test_agent_instruction_serialization_with_references`
- [x] `test_driver_reconcile_dispatches_patch_payload`

---

## Task 2 — BDD Feature & Step Scaffold (test scaffold)

**What:** Write BDD scenarios to verify relational instructions coordination in Cucumber features.

**Files:**
- `tests/features/video_agent_ui.feature` — Append scenarios for multi-instruction asset mapping.
- `tests/features/test_video_agent_ui_steps.py` — Implement step definitions simulating the mock server responses.

**Steps:**
- [x] Write Cucumber scenario for syncing relational instructions.
- [x] Mock the tRPC and REST responses for project brief updates.

**Tests created (red):**
- [x] Scenario: "Syncing instructions containing reference image IDs"
- [x] Scenario: "Toggling active/inactive states of relational cards"

---

## Task 3 — Relational Instructions API Client Implementation (implementation)

**What:** Implement direct `FlowApiClient` methods for patching agent settings.

**Files:**
- `src/gflow_cli/api/client.py` — Implement `patch_agent_info(project_id, enabled, cards)`.
- `src/gflow_cli/api/image.py` — Update `AgentInstruction` class attributes.

**Steps:**
- [x] Add `image_media_ids` and `character_ids` fields to `AgentInstruction`.
- [x] Implement `patch_agent_info` using the REST route `PATCH /v1/projects/{projectId}/agentInfo`.
- [x] Support `updateMask` parameters to selectively update `project_brief.enabled` and `project_brief.cards`.

---

## Task 4 — Driver and Worker Integration (integration)

**What:** Update `AgenticFlowUiDriver` and the daemon worker to use the new API client method.

**Files:**
- `src/gflow_cli/api/transports/drivers/agentic.py` — Call `patch_agent_info` inside `configure_image_settings`.
- `src/gflow_cli/worker/daemon.py` — Parse relational assets from task queue and forward them.

**Steps:**
- [x] Modify `configure_image_settings` to bypass DOM loop reconciliation and call the client REST endpoint directly.
- [x] Refactor background daemon payload builder to support `image_media_ids` mapping.

---

## Task 5 — Live Agentic Image Generation Spike (spike)

**What:** Validate empirically that the REST PATCH → generate round-trip works as expected
before writing any more production code. Run a real T2I generation on an agentic profile
with instructions ON vs OFF. Confirm cards influence output and that the `enabled: false`
soft-disable is respected by the model.

**Files:**
- `C:\development\github\gflow-agent-browser-spike\instructions_spike.py` — standalone spike script.

**Steps:**
- [x] Run `scripts/probe-agent-mode.ps1` on an active Flow browser session to inspect and verify all Agent Mode and sidebar DOM selectors, attributes, and roles.
- [x] Write spike: create project, PATCH two cards (one enabled, one disabled), generate one image, inspect output. (`scratch/spike_instructions_phase_a.py` + live Chrome; project `6b714c4e…`.)
- [x] Confirm `enabled: false` card does NOT appear to influence generation. (Confirmed — disabled noir card never injected.)
- [ ] ~~Confirm `imageReferenceMediaIds` on a card actually anchors the model's visual style.~~ Deferred — not tested; plausible via the reasoning path. Probe later with an uploaded asset.
- [x] Document findings in `spike-findings.md` (pass/fail per hypothesis).
- [ ] **Spike Fallback:** If reference media IDs are ignored, specify the fallback to degrade gracefully (using them only for UI catalog context, with warning logged).

**Gate:** ✅ MET (with redesign requirement). All hypotheses have observed answers.

**Spike outcome (2026-07-08):** Cards DO steer output (H1/H2/H3 confirmed) **but only
through the agent's reasoning path.** An imperative `"Generate N image(s): {prompt}"`
directive is passed to the image tool verbatim → brief ignored; a conversational
request → agent rewrites the tool prompt and injects enabled cards. **Load-bearing
consequence:** the transport made instructions inert on the CLI for TWO reasons
(both found live via the e2e; both fixed in this branch):
- `_compose_directive` used the imperative form → now conversational (`"Make me a picture of {prompt} …"`).
- `_reconcile_instructions` never set the brief-level **master switch**
  `project_brief.enabled` (defaults OFF on a fresh project → all cards ignored) → now
  PATCHes `updateMask=project_brief.enabled,project_brief.cards` with `enabled:true`.
- `_reconcile_instructions` content-type `application/json+protobuf` (silent 400)
  → `text/plain`; response status now checked/warned.
- Per-card `title` hardcode → `AgentInstruction.title` + `resolved_title()`, via a
  shared `build_agent_brief_cards()` used by both PATCH paths.
- `patch_agent_info` now returns the echoed `projectBrief` (no `GET /agentInfo`; it 404s).

**Live e2e (Task 9, done early):** `tests/e2e/test_live_agentic_instructions.py`
(`-m e2e_image`, `GFLOW_CLI_FORCE_AGENT_UI=1`) drives the real transport and was run
to a **verified crayon drawing** from a style-neutral prompt — cards steer output
end-to-end. Known follow-up: `GFLOW_CLI_FORCE_AGENT_UI` binding is ~50/50 flaky (may
bind classic and silently skip instructions). See `spike-findings.md`.

---

## Task 6 — `gflow instructions` Docs-First Spec (docs-first)

**What:** Write all user-facing documentation BEFORE implementing the subcommand.
The docs become the acceptance criteria for Task 7. This forces the design to be
coherent and agent-readable before a single implementation line is written.

**Design decisions locked in (do not reopen without a predict):**
- `-i "text"` on generation commands = ephemeral text-only card, always creates, never looks up.
- `gflow instructions` = persistent CRUD on project-scoped cards, supports refs and characters.
- No UUID inputs at the CLI; title matching is case-insensitive, fail-fast on ambiguity.
- `--project proj-id` on generation commands selects which project's active cards to use.
- Cards with `enabled: false` stay in the project but are ignored by the model.

**Files:**
- `docs/INSTRUCTIONS.md` — full user-facing guide: what instruction cards are, the
  three-layer pipeline (setup → generate → compose), examples, movie context.
- `skills/gflow-cli/SKILL.md` — add a **Pipeline** section documenting the layered
  sequence so AI agents always call setup before generation.
- `docs/USAGE.md` — add `gflow instructions` to the command surface section.
- `docs/INDEX.md` — register the new `INSTRUCTIONS.md` documentation file.
- `docs/superpowers/plans/2026-07-08-agentic-instructions/PLAN.md` — update this file
  (Task 5b spec section).

**Steps:**
- [ ] Write `docs/INSTRUCTIONS.md` covering:
  - What an instruction card is and why it is credits-free to set up.
  - The three-layer pipeline: project context → generation → movie composition.
  - Full `gflow instructions` command surface with annotated examples.
  - Ephemeral `-i` vs persistent card distinction.
  - `--project` flag semantics for generation commands.
  - Typical agent-driven workflow (numbered steps, machine-readable).
- [ ] Update `skills/gflow-cli/SKILL.md` — add Pipeline section (with explicit project ID discovery guidance and "DO NOT" rules).
- [ ] Update `docs/USAGE.md` — add `gflow instructions` command surface entry.
- [ ] Update `docs/INDEX.md` — register link to `docs/INSTRUCTIONS.md`.
- [ ] Run `scripts/ci/check_doc_links.py` — all internal links must resolve.

**Gate:** Docs reviewed and approved before any Task 7 implementation starts.

---

## Task 7 — `gflow instructions` Subcommand Implementation (implementation)

**What:** Implement the `gflow instructions` Click subcommand group based on the
Task 6 spec. Strictly no scope creep — implement exactly what the docs say.

**Files:**
- `src/gflow_cli/cli_instructions.py` — new module with Click group + subcommands.
- `src/gflow_cli/cli.py` — register `gflow instructions` group.
- `src/gflow_cli/api/client.py` — add `get_agent_info()` for list/enable/disable lookups.
- `src/gflow_cli/cli_image.py` / `src/gflow_cli/cli_video.py` — add `--project` option.
- `tests/test_cli_instructions.py` — unit tests for all subcommands.
- `tests/features/instructions.feature` — BDD feature definitions for subcommands.
- `tests/features/test_instructions_steps.py` — BDD step definitions.

**Subcommands:**
```
gflow instructions add TITLE --text TEXT [--ref PATH]... [--character ID]... [--project ID]
gflow instructions list [--project ID] [--json]
gflow instructions enable TITLE [--project ID]
gflow instructions disable TITLE [--project ID]
gflow instructions rm TITLE [--project ID]
gflow instructions apply FILE [--project ID]   # declarative full-sync from TOML/JSON
gflow instructions toggle-mode [--on/--off] [--project ID] # toggle project-level Agentic mode
```

**Steps:**
- [ ] Implement `get_agent_info(project_id)` client method returning a typed DTO (`ProjectBrief`).
- [ ] Implement `gflow instructions add` (upload refs → get media UUIDs → PATCH cards).
- [ ] Implement `gflow instructions list` with Rich table output and `--json` flag.
- [ ] Implement `gflow instructions enable` / `disable` (title match, case-insensitive, fail-fast).
- [ ] Implement `gflow instructions rm` (remove matching card, PATCH remaining).
- [ ] Implement `gflow instructions apply FILE` (declarative full-replace idempotent sync from TOML/JSON manifest).
- [ ] Implement `gflow instructions toggle-mode` (programmatic toggle of project-level Agentic mode).
- [ ] Add `--project proj-id` to `gflow image t2i` / `i2i` / `gflow video` commands.
- [ ] Gracefully handle classic UI projects in `gflow instructions` — fail-fast with clean HTTP error reporting.
- [ ] **UI Visual Consistency:** Ensure the driver's `configure_image_settings` clicks the `article_spark` button if the agent sidebar is closed, ensuring the browser Page visually reflects active cards.
- [ ] Ensure MCP schema symmetry: mirror all subcommands in MCP tool definitions (CI gate).
- [ ] Create `tests/features/instructions.feature` and verify CLI subcommands via BDD tests.
- [ ] All new commands covered by unit tests.
- [ ] Run `/gflow:check` — all gates green.

---

## Task 8 — Movie Manifest Integration (movie)

**What:** Integrate instruction card management into `gflow movie` so each scene can
declare its own instruction context declaratively in `movie.toml`.

**Files:**
- `src/gflow_cli/movie_manifest.py` — parse `[instructions]` and `[[scene.instructions]]` blocks.
- `src/gflow_cli/cli_movie.py` — inject `gflow instructions apply` step before each scene's generation.
- `docs/MOVIE.md` — document the `instructions` blocks in the manifest format.

**Steps:**
- [ ] Define `movie.toml` schema for global + per-scene instruction blocks:
  ```toml
  [instructions]
  # Applied to all scenes unless overridden.
  [[instructions.card]]
  title = "Cinematic Lighting"
  text  = "Volumetric cinematic light from camera-left"
  ref   = "./refs/mood.jpg"
  enabled = true

  [[scene]]
  prompt = "hero emerges from fog"
  [scene.instructions]
  disable = ["Cinematic Lighting"]
  [[scene.instructions.card]]
  title = "Fog Atmosphere"
  text  = "Dense volumetric fog, low contrast"
  ```
- [ ] Implement manifest parser for the new blocks.
- [ ] Inject a pre-generation `PATCH agentInfo` call for each scene's instruction diff.
- [ ] Update `docs/MOVIE.md` with the new schema.
- [ ] BDD scenario: multi-scene movie with per-scene instruction override.

---

## Task 9 — E2E Verification (e2e)

**What:** Live integration test covering the full instructions lifecycle and a real
generation using the established context.

**Files:**
- `tests/e2e/test_live_agentic_instructions.py` — full create-patch-generate-teardown lifecycle.

**Steps:**
- [ ] Write `test_live_agentic_instructions.py`:
  - Create a project.
  - Add two instruction cards (one with a reference image).
  - Enable one, disable the other.
  - Generate one image (`gflow image t2i`).
  - Assert generation succeeds and returns a valid `GeneratedImage`.
  - Teardown: delete cards.
- [ ] Mark test `@pytest.mark.live` and `@pytest.mark.e2e`.
- [ ] Document run instructions in `docs/TESTING.md`.

---

## Task 10 — Re-assess and Optimize Developer Skills (skills audit)

**What:** Systematically review and update all agent skills to reflect the full
evolved architecture including the new instruction pipeline, `gflow instructions`
subcommand, and layered project context model.

**Files:**
- `skills/gflow-cli/SKILL.md` — primary update (Pipeline section added in Task 6).
- `skills/predict/SKILL.md`, `skills/scenario/SKILL.md`, `skills/plan/SKILL.md`,
  `skills/status/SKILL.md`, `skills/pr-council-review/SKILL.md` — audit each.

**Steps:**
- [ ] Scan all skills and compare against current codebase: commands, transports, APIs, DTOs.
- [ ] Identify stale: legacy DOM transport descriptions, missing `gflow instructions`, old REST routes.
- [ ] Update each skill to correctly describe current components (SQLite ledger, daemon workers,
  REST API, new `gflow instructions` subcommand, layered pipeline model).
- [ ] Verify all YAML frontmatter, internal links, and cross-references resolve correctly.
- [ ] Run `scripts/ci/check_doc_links.py` — merge gate.

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] `docs/INSTRUCTIONS.md` created and registered in `docs/INDEX.md`
- [ ] `skills/gflow-cli/SKILL.md` Pipeline section present
- [ ] `docs/USAGE.md` covers `gflow instructions` command surface
- [ ] `docs/MOVIE.md` documents `[instructions]` manifest blocks
- [ ] BDD feature files cover all scenarios (including CLI subcommands in `tests/features/instructions.feature`)
- [ ] MCP schema symmetry test passes (CI gate)
- [ ] Live spike findings documented in `spike-findings.md`
- [ ] Documented link check (`scripts/ci/check_doc_links.py`) passes without errors
- [ ] No `# TODO` in diff without a tracked issue link

