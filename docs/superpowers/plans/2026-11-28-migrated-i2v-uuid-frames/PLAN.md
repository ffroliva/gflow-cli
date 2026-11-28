# Migrated `video i2v` In-Project UUID Frames Implementation Plan (#1542)

> **For agentic workers:** Run `/gflow:status --feature migrated-i2v-uuid-frames` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Allow `gflow video i2v` (and `video generate` / MCP `gflow_generate_video`) on `flow.google.com` to bind catalogued in-project image UUIDs as Start and/or End frames in place with zero `maseQ` upload, mirroring `#913`/`#995`.

**Architecture:** `GenerateVideoRequest` gains `start_image_ref_in_project` and `end_image_ref_in_project`, populated via the shared `api.image.recorded_in_project` predicate across CLI (`cli_video.py`), MCP (`mcp/tools.py`), and worker (`worker/codec.py`). `_mc_common._unported_form` (delegating to `_unported_i2v_form` to stay under the `#1249` C901 ratchet) allows in-project captioned UUID frames while refusing cross-project or un-captioned UUIDs at `$0` (exit 36). A new focused helper module `src/gflow_cli/api/transports/migrated_existing_frames.py` handles pre-settings grid-token resolution, sanitized caption search with `search.fill("")` unfiltered fallback, exact `/asb/<token>` option selection, and `BOUND_CHIP` verification while keeping existing UUIDs out of `adopt_ids`.

**Predict verdict:** GO — confidence 8/10 ([`PREDICT.md`](PREDICT.md))

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Reused UUID in `adopt_ids` could adopt an older clip on a lost submit reply (`#1112`) | Include only uploaded (`start_image`/`end_image is not None`) IDs in `adopt_ids`; keep UUIDs in `expect_media_id`/`expect_end_media_id` |
| High | `#1249` size/complexity ratchet breaches on `_unported_form`, `run_video`, and `mcp/tools.py` | Extract `_unported_i2v_form`, `migrated_existing_frames.py`, and tighten `_resolve_payload_refs` helper so `check_size.py` stays green |
| High | Leaking prompt-derived catalog caption in error details | Report `media_id`, `chip_label`, and `offered` count only in `ReferenceNotFoundError` / `UiSelectorDriftError` |
| Medium | Deleted same-project tile spends up to 90 s reloading before exit 32 (vs instant exit 36) | Document behavior change in `CHANGELOG.md` and `docs/REFERENCE_STRATEGIES.md` (mirrors `#995`) |

---

## File structure

### New files
```
scripts/dev/spike_1542_migrated_i2v_uuid_frames.py
  $0 live DOM + route-aborted wire spike for #1542
docs/superpowers/spikes/2026-11-28-migrated-i2v-uuid-frames.md
  Redacted spike findings
docs/superpowers/plans/2026-11-28-migrated-i2v-uuid-frames/{PREDICT,SCENARIO,PLAN}.md
  Pre-implementation predict, scenario, and task plan
src/gflow_cli/api/transports/migrated_existing_frames.py
  Pure query sanitizer + in-project frame token lookup, picker selection, and attach helper
tests/api/transports/test_migrated_existing_frames.py
  Unit tests for frame_search_query, await_existing_frame_tokens, pick/bind, collisions, fallback, and run_video wiring
tests/worker/test_codec_video_frame_ref_project.py
  Unit tests for worker/codec.py start/end *_in_project resolution from *_flow_project_id
tests/features/migrated_i2v_uuid_frames.feature
  @e2e @e2e_auth Gherkin scenarios for $0 route-aborted Start-only and Start+End UUID binding
tests/e2e/test_migrated_i2v_uuid_frames_bdd.py
  pytest-bdd binding for migrated_i2v_uuid_frames.feature ($0 route-aborted live regression)
```

### Modified files
```
src/gflow_cli/api/video.py
  Add start_image_ref_in_project / end_image_ref_in_project fields + validation in _validate_frame_ref_ids
src/gflow_cli/api/transports/_mc_common.py
  Extract _unported_i2v_form; allow in-project captioned UUID start/end frames; keep cross-project/uncaptioned exit 36
src/gflow_cli/api/transports/_mc_upload_frame.py
  Extract _confirm_frame_bound helper so local upload and existing-frame paths share the #792 confirm + BOUND_CHIP check
src/gflow_cli/api/transports/migrated_composer.py
  Wire pre-settings token resolution, in-place frame binding, uploaded-only adopt_ids, and updated exit-36 message
src/gflow_cli/cli_video.py
  Propagate flow_project_id / recorded_in_project from _media_picker_metadata into _I2VParams and GenerateVideoRequest
src/gflow_cli/mcp/tools.py
  Record start/end *_flow_project_id in _resolve_payload_refs (within line/C901 ratchet) and update gflow_generate_video docs
src/gflow_cli/worker/codec.py
  Compute start_image_ref_in_project / end_image_ref_in_project via recorded_in_project in build_video_request
src/gflow_cli/cli_capabilities.py
  Update _MIGRATED_VIDEO and _UPLOAD_MIGRATED notes
src/gflow_cli/errors.py
  Update FlowHostMigratedError._default_remediation
tests/api/test_video.py, tests/api/transports/test_migrated_dispatch.py, tests/cli/test_cli_video.py,
tests/mcp/test_migrated_i2v_parity.py, tests/mcp/test_tools_helpers.py
  Update existing assertions + add unit tests for new fields, routing, and CLI/MCP parity
docs/USAGE.md, docs/MCP.md, docs/REFERENCE_STRATEGIES.md, docs/CONFIGURATION.md,
KNOWN_ISSUES.md, README.md, llms.txt, AGENTS.md, skills/gflow-cli/SKILL.md, CHANGELOG.md,
website/docs/*, plugins/gflow/skills/gflow-cli/SKILL.md
  Mirror-axis documentation updates + regenerated mirrors
```

---

## Task 1 — Unit test scaffold (red first)

**What:** Write failing unit tests covering the new DTO fields, `_unported_form` gating, `migrated_existing_frames` logic, CLI/MCP/worker `in_project` propagation, and `adopt_ids` isolation.

**Files:**
- `tests/api/test_video.py` — `*_in_project` validation without `*_ref_id`
- `tests/api/transports/test_migrated_existing_frames.py` — query sanitization, grid reload before settings, agent-only refusal after reload, token matching on colliding captions, `fill("")` stale-caption fallback, `#792` confirm fallback, no caption leak in errors, mixed local+UUID, same Start+End UUID, `adopt_ids` excludes existing UUIDs
- `tests/worker/test_codec_video_frame_ref_project.py` — `start_image_ref_in_project` / `end_image_ref_in_project` across same/other/missing project
- `tests/mcp/test_tools_helpers.py` & `tests/mcp/test_migrated_i2v_parity.py` — `*_flow_project_id` round-trip and `migrated_can_serve`
- `tests/cli/test_cli_video.py` — `_media_picker_metadata` project propagation and CLI `video i2v` call site

**Steps:**
- [x] Add failing unit tests for every Critical + High scenario from `SCENARIO.md`
- [x] Run pytest on the new tests and confirm they fail for the expected pre-implementation reasons

**Tests created (red):**
- [x] `test_in_project_requires_matching_ref_id` — DTO rejects `in_project=True` without `ref_id`
- [x] `test_unported_form_allows_in_project_captioned_uuid_frames_and_refuses_others` — exit-36 matrix
- [x] `test_existing_frame_picker_and_run_video_wiring` — token match, collisions, reload-before-settings, fallback, `adopt_ids`
- [x] `test_codec_video_frame_ref_project` — worker `recorded_in_project` for start & end
- [x] `test_mcp_and_cli_in_project_propagation` — CLI + MCP end-to-end routing parity

---

## Task 2 — BDD feature & `$0` E2E scaffold

**What:** Create the `@e2e @e2e_auth` Gherkin feature and its `tests/e2e/` pytest-bdd binding for route-aborted Start-only and Start+End UUID binding.

**Files:**
- `tests/features/migrated_i2v_uuid_frames.feature`
- `tests/e2e/test_migrated_i2v_uuid_frames_bdd.py`

**Steps:**
- [x] Write `tests/features/migrated_i2v_uuid_frames.feature` tagged `@e2e @e2e_auth`
- [x] Write `tests/e2e/test_migrated_i2v_uuid_frames_bdd.py` binding both scenarios via `route.abort()` at `$0`
- [x] Verify `tests/features/test_e2e_binding_guard.py` passes offline

---

## Task 3 — Core DTO (`api/video.py`)

**What:** Add `start_image_ref_in_project` and `end_image_ref_in_project` to `GenerateVideoRequest` with structural validation.

**Files:**
- `src/gflow_cli/api/video.py`

**Steps:**
- [x] Add `start_image_ref_in_project: bool = False` and `end_image_ref_in_project: bool = False`
- [x] Validate in `_validate_frame_ref_ids()` that each `*_in_project=True` requires its `*_ref_id`
- [x] Confirm `tests/api/test_video.py` passes

---

## Task 4 — Migrated transport (`migrated_existing_frames.py`, `_mc_common.py`, `_mc_upload_frame.py`, `migrated_composer.py`)

**What:** Implement zero-upload in-place frame binding and pre-settings grid token resolution while respecting `#1249` complexity limits and `adopt_ids` safety.

**Files:**
- `src/gflow_cli/api/transports/migrated_existing_frames.py`
- `src/gflow_cli/api/transports/_mc_common.py`
- `src/gflow_cli/api/transports/_mc_upload_frame.py`
- `src/gflow_cli/api/transports/migrated_composer.py`

**Steps:**
- [x] Extract `_unported_i2v_form(request)` in `_mc_common.py` and allow in-project captioned Start/End UUIDs
- [x] Extract `_confirm_frame_bound(...)` in `_mc_upload_frame.py` (reused by both upload and existing-frame paths)
- [x] Implement `migrated_existing_frames.py` (`frame_search_query`, `await_existing_frame_tokens`, `attach_existing_frame`, `attach_video_frames`)
- [x] Wire into `migrated_composer.py::run_video`: resolve tokens before `apply_video_settings()`, bind frames, exclude non-uploaded UUIDs from `adopt_ids`, update `_refuse_unported_video` wording
- [x] Confirm `tests/api/transports/test_migrated_*.py` pass and `check_size.py` is green

---

## Task 5 — CLI surface (`cli_video.py`, `cli_capabilities.py`, `errors.py`)

**What:** Propagate `recorded_in_project` through the CLI `video i2v` path and update capability/remediation strings.

**Files:**
- `src/gflow_cli/cli_video.py`
- `src/gflow_cli/cli_capabilities.py`
- `src/gflow_cli/errors.py`

**Steps:**
- [x] Return catalog `flow_project_id`s from `_media_picker_metadata` (or compute `in_project` via `recorded_in_project` against `project_id`)
- [x] Thread `image_ref_in_project` and `end_frame_ref_in_project` through `_I2VParams` → `_run_i2v` → `GenerateVideoRequest`
- [x] Update `_MIGRATED_VIDEO`, `_UPLOAD_MIGRATED` in `cli_capabilities.py` and `FlowHostMigratedError._default_remediation` in `errors.py`
- [x] Confirm `tests/cli/test_cli_video.py` and capability tests pass

---

## Task 6 — MCP & worker codec mirror (`mcp/tools.py`, `worker/codec.py`)

**What:** Mirror `in_project` resolution across the MCP enqueue path, worker codec decode, and MCP tool documentation (non-optional sibling of Task 5).

**Files:**
- `src/gflow_cli/mcp/tools.py`
- `src/gflow_cli/worker/codec.py`

**Steps:**
- [x] Record `start_image_ref_flow_project_id` / `end_image_ref_flow_project_id` in `_resolve_payload_refs` (`task_type == "i2v"`) while keeping `mcp/tools.py` within the `#1249` line and C901 limits
- [x] Read those exact keys in `worker/codec.py::build_video_request` via `recorded_in_project(..., target_project)`
- [x] Update `gflow_generate_video` tool description and docstring in `mcp/tools.py`
- [x] Confirm `tests/mcp/` and `tests/worker/` parity and codec tests pass

---

## Task 7 — Docs, mirrors, and CHANGELOG

**What:** Update all six mirror-axis documentation surfaces and regenerate website + plugin mirrors.

**Files:**
- `docs/USAGE.md`, `docs/MCP.md`, `docs/REFERENCE_STRATEGIES.md`, `docs/CONFIGURATION.md`, `docs/CAPABILITIES.md`
- `KNOWN_ISSUES.md`, `README.md`, `README.zh-CN.md`, `llms.txt`, `AGENTS.md`, `skills/gflow-cli/SKILL.md`, `CHANGELOG.md`
- Regenerated: `website/docs/*`, `plugins/gflow/skills/gflow-cli/SKILL.md`

**Steps:**
- [x] Update every doc surface that claims video UUID frames are unported on `flow.google.com`
- [x] Add `[Unreleased]` entry in `CHANGELOG.md` (including the 90 s deleted-tile behavior note)
- [x] Run `scripts/ci/generate_website_docs.py` and `scripts/ci/generate_plugin_skills.py`

---

## Task 8 — Full CI gates, budgeted live E2E verification, and local council review

**What:** Run the full Impeccable Routine (`/gflow:check`), `$0` BDD e2e, budgeted live `veo-lite` generation (`<= 80` credits total), and local `pr-council-review` audit.

**Steps:**
- [x] Run all CI scripts (`check_repo_hygiene`, `check_size`, `check_doc_links`, `check_website_docs_pii`, `generate_website_docs --check`, `check_council_memory`, `generate_plugin_skills --check`, `ruff check`, `ruff format --check`, `pyright src`)
- [x] Run affected unit/integration/parity/BDD guard pytest suites
- [x] Run `$0` BDD e2e (`pytest -m e2e_auth tests/e2e/test_migrated_i2v_uuid_frames_bdd.py`)
- [x] Run budgeted live `veo-lite` CLI Start-only UUID i2v + Start+End UUID i2v (with explicit `--max-credits 20`, verifying 0 `maseQ` upload and real MP4 output) + MCP parity verification within the 80-credit cap
- [x] Run `gflow credits user --json` and verify exact spend `<= 80`
- [x] Run local branch council review (`skills/pr-council-review/SKILL.md`) and fix any must-fix findings

---

## Definition of done

- [x] All task steps checked off
- [x] `/gflow:check` green (hygiene / size / doc links / mirrors / ruff / format / pyright / pytest)
- [x] `CHANGELOG.md` `[Unreleased]` section updated
- [x] Docs and mirrors updated (`USAGE.md`, `MCP.md`, `REFERENCE_STRATEGIES.md`, `CONFIGURATION.md`, `CAPABILITIES.md`, `KNOWN_ISSUES.md`, `README.md`, `README.zh-CN.md`, `llms.txt`, `AGENTS.md`, `skills/gflow-cli/SKILL.md`, `website/docs/`, `plugins/`)
- [x] BDD feature file covers `$0` route-aborted live e2e and passes binding guard + live run
- [x] Live-verified against real `flow.google.com` within `<= 80` credits total
- [x] Local council review run and all must-fix items resolved
- [x] Worktree contains only clean intended changes (no stray scratch files, no secrets/PII, no git commits/pushes)

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| 2026-11-28 | Exclude existing UUIDs from `adopt_ids` in `run_video` | `adopt_ids` assumes run-unique `maseQ` upload IDs; a reused UUID could match an earlier clip if the submit reply body is evicted | Passing `expect_media_id`/`expect_end_media_id` unconditionally into `adopt_ids` |
| 2026-11-28 | Put existing-frame logic in `migrated_existing_frames.py` | Respects `#1249` ratchet (`run_video` C901=13, `_unported_form` C901=12) and mirrors `migrated_existing_refs.py` | Growing `_mc_upload_frame.py` and `migrated_composer.py` in place |
| 2026-11-28 | Use `search.fill("")` for stale-caption unfiltered fallback | Spike Q4 measured `fill("")` restores all 15 unfiltered options with 0 extra `UpteDb` RPCs, matching `Ctrl+A+Backspace` and `Escape+reopen` | Closing and reopening the popover on every caption miss |
| 2026-11-28 | Omit raw caption/query text from `ReferenceNotFoundError` / `UiSelectorDriftError` details | Captions are prompt-derived user data (`_mc_mention.py:349`); errors should report UUID + option counts only | Echoing `query!r` in exception details |
| 2026-11-28 | Skip video-path `#546` catalog write-through for now | `search.fill("")` already binds recent renamed assets in place without reload; adding `name_resolver` plumbing to `run_video` is speculative (YAGNI) | Threading `RefreshResolver` through `UiAutomationTransport.generate_video` |
