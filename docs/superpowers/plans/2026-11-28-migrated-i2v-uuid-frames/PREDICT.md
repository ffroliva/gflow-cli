# Predict: In-project image UUIDs for `video i2v` Start/End frames on `flow.google.com` (#1542)

## Verdict: GO
**Confidence:** 8/10

## Summary
Five personas evaluated porting the `#913`/`#995` in-project UUID reference pattern from `image i2i` to `video i2v` Start and End frames on `flow.google.com`, grounded in the `$0` live spike ([`2026-11-28-migrated-i2v-uuid-frames.md`](../../spikes/2026-11-28-migrated-i2v-uuid-frames.md)). All personas aligned on **GO**, surfacing three concrete design constraints that must be baked into the plan: (1) do **not** put existing (reused) UUIDs into `adopt_ids` (which assumes run-unique uploads), (2) never echo prompt-derived catalog captions in error detail strings, and (3) extract helpers cleanly to respect the `#1249` size/complexity ratchet (`size_baseline.json`).

## Persona findings

### Architect — GO (8/10)
- **Domain boundary (`api/video.py`):** Adding `start_image_ref_in_project: bool = False` and `end_image_ref_in_project: bool = False` on `GenerateVideoRequest` cleanly mirrors `ImageRef.in_project` (`#995`). Enforcing in `_validate_frame_ref_ids()` that `*_in_project=True` requires the corresponding `*_ref_id` makes invalid states unrepresentable.
- **Shared predicate:** Reuse `api.image.recorded_in_project(recorded_project_id, target_project_id)` across all three request producers: CLI (`cli_video.py`), MCP (`mcp/tools.py`), and worker (`worker/codec.py`). Zero duplicated comparison logic.
- **Size ratchet (`#1249`, `scripts/ci/size_baseline.json`):**
  - `_mc_common.py::_unported_form` is pinned at `C901=12` (0 headroom) → extract `_unported_i2v_form(request)` so `_unported_form` shrinks.
  - `migrated_composer.py::run_video` is pinned at `C901=13` (0 headroom) → extract existing-frame resolution/binding into a dedicated module `src/gflow_cli/api/transports/migrated_existing_frames.py` (mirroring `migrated_existing_refs.py` created for `#1249`/`#546`).
  - `mcp/tools.py` is at 2,689/2,691 lines (2 lines headroom) and `_resolve_payload_refs` is at `C901=13` (0 headroom) → extract the i2v ref enrichment helper cleanly so neither line count nor complexity grows past the baseline.
- **Critical safety finding — `adopt_ids` collision hazard:** `migrated_composer.py:477` passes `expect_media_id` and `expect_end_media_id` into `adopt_ids` for `#1112` lost-submit-reply recovery. `_mc_submit_observe.py` explicitly assumes every `adopt_id` is a run-unique `maseQ` upload (so no older clip in the project can carry it). Reusing an existing in-project image UUID violates that invariant: if a second video is generated from the same image and Chrome evicts the submit reply body, `_adopt` could mistakenly adopt the older clip. **Fix:** only include **uploaded** frame IDs (`request.start_image is not None` / `request.end_image is not None`) in `adopt_ids`; keep existing UUIDs in `expect_media_id` / `expect_end_media_id` for the pre-send route guard.

### Security / reCAPTCHA — GO (8/10)
- **Auth / reCAPTCHA / profile isolation:** Untouched. Zero changes to cookies, SAPISIDHASH, stealth flags, or profile directory boundaries.
- **Reduced upload attack surface:** Zero-upload binding removes local-file reads and `maseQ` uploads for in-project UUIDs.
- **Selector / DOM injection:** `_GRID_TOKEN_JS` passes `media_id` as a Playwright argument (never interpolated into a CSS selector). `frame_search_query` strips trailing `…`/`.` and rejects Unicode `Cc`/`Cf`/`Zl`/`Zp` characters and strings `> 120` chars; typing uses `page.keyboard.insert_text` (no Enter key dispatch).
- **Caption redaction in errors:** Catalog `display_name` values are prompt-derived text. Following `_mc_mention.py:349-350` ("report counts, not text"), `ReferenceNotFoundError` and `UiSelectorDriftError` raised by the existing-frame binder must **not** echo the raw `query` / `display_name` string in `detail` — log/report `media_id`, `chip_label`, and `offered` option count instead.

### Performance / Playwright — GO (9/10)
- **Hot-path win:** Skips `maseQ` upload + server-side re-indexing delay entirely on the happy path; spike measured Start+End binding in ~2 s total.
- **Reload ordering:** `await_existing_frame_tokens` runs **before** `apply_video_settings()`, mirroring `reference_existing` on `image i2i`: if a freshly generated image requires a page reload to appear in the grid, settings are applied once afterward instead of being wiped by the reload.
- **Stale/deleted row latency:** Mirrors `#995` — a same-project catalog UUID whose tile was deleted from Flow now spends up to `EXISTING_REF_WAIT_S` (90 s) reloading before raising `ReferenceNotFoundError` (exit 32), instead of immediate exit 36. Both are `$0`; document explicitly in `CHANGELOG.md` and `docs/REFERENCE_STRATEGIES.md`.
- **Unfiltered fallback efficiency:** Spike Q4 proved `search.fill("")` resets Angular's picker filter client-side (`0` extra `UpteDb` RPCs) without closing/reopening the popover.

### CLI & MCP UX / Cross-platform — GO (8/10)
- **MCP parity (mandatory 3-hop check):**
  - CLI (`cli_video.py`): `_media_picker_metadata` reads `asset.flow_project_id` and populates `image_ref_in_project` / `end_frame_ref_in_project` against the resolved `project_id`; `gflow video generate` delegates to `video i2v` unchanged.
  - MCP (`mcp/tools.py`): `_resolve_payload_refs` (`task_type == "i2v"`) writes `start_image_ref_flow_project_id` / `end_image_ref_flow_project_id` into the queue payload when `asset.flow_project_id` is present (while still refusing an asset that has *only* a project id and neither `display_name` nor `local_path`).
  - Worker (`worker/codec.py`): `build_video_request` reads `start_image_ref_flow_project_id` / `end_image_ref_flow_project_id` and compares against `payload.get("project_id")` via `recorded_in_project`.
  - MCP tool description + docstring (`gflow_generate_video`) and `docs/MCP.md` must be updated so agents know same-project UUIDs work on `flow.google.com` without re-uploading.
- **Exit codes & remediation:**
  - Cross-project UUID or un-captioned UUID → `FlowHostMigratedError` (exit 36, `charged: "no"`, `retryable: false`), failing fast before opening the editor.
  - Missing/deleted grid tile or picker token miss → `ReferenceNotFoundError` (exit 32, `charged: "no"`).
  - Unbound chip or missing picker → `UiSelectorDriftError` (exit 23, `charged: "no"`).
  - Wire submit guard mismatch (`_i2v_body_problem` / `_interpolation_body_problem`) → `WireFormatError` (exit 7, aborted before send, `charged: "no"`).
- **Locale invariance:** 100% structural anchors (`EMPTY_CHIP`, `BOUND_CHIP`, `PICKER`, `PICKER_SEARCH`, `PICKER_OPTION`, `PICKER_CONFIRM`, `img[data-media-id]`, `/asb/<token>`). Zero `has-text(...)` on Flow UI labels.
- **Capabilities & docs:** Update `cli_capabilities.py` (`gflow capabilities` + `capabilities://self`), `errors.py`, `docs/USAGE.md`, `docs/MCP.md`, `docs/REFERENCE_STRATEGIES.md`, `docs/CONFIGURATION.md`, `KNOWN_ISSUES.md`, `README.md`, `llms.txt`, `skills/gflow-cli/SKILL.md`, `AGENTS.md`, `CHANGELOG.md`, and regenerate `website/docs/` and `plugins/gflow/skills/`.

### Devil's Advocate — GO (7/10)
- **Simpler path check:** Reusing `MentionMixin` was considered and rejected: Frames uses `EMPTY_CHIP` / `BOUND_CHIP` + search input in `flow-add-menu-popover-content`, whereas `image i2i` uses `@` mentions in `contenteditable`. Extracting a small `migrated_existing_frames.py` module while sharing `_GRID_TOKEN_JS`, `_picker_pane`, `_confirm_frame_bound`, and `EXISTING_REF_WAIT_S` is the smallest maintainable change.
- **Mixed local + UUID frames:** Unlike `image i2i` (single mention list), Start and End are separate chip slots, so local Start + in-project UUID End and in-project UUID Start + local End both work naturally with no extra code branches beyond evaluating each slot independently.
- **Video `#546` catalog write-through:** Not adding a new `name_resolver` parameter to `run_video` / `UiAutomationTransport` keeps scope tight (YAGNI): `search.fill("")` already binds renamed recent assets directly by `/asb/<token>` without needing a reload or DB write-through.
- **Rollback:** Isolated behind `*_in_project` booleans and `_unported_i2v_form`; local-file `i2v` and `labs.google` paths are untouched.

## High-confidence risks (flagged by 2+ personas)
1. **`#1249` size/complexity ratchet breaches** (Architect, Devil's Advocate) — mitigated by extracting `_unported_i2v_form`, `migrated_existing_frames.py`, and keeping `mcp/tools.py` within its 2,691-line / C901=13 limits.
2. **Stale/deleted same-project tile waits up to 90 s before exit 32** (Performance, CLI/MCP UX) — documented explicitly in `CHANGELOG.md` and `docs/REFERENCE_STRATEGIES.md`, mirroring `#995`.

## Conflicts resolved
- **Caption sanitization refusal vs unfiltered fallback** (Security vs Devil's Advocate): An empty/whitespace-only catalog caption is refused upfront at exit 36 in `_unported_form` (matching `#995`). If a non-empty caption fails search sanitization (`> 120` chars, control characters, or bare `…`), `_frame_search_query` returns `""` and skips typing into the search box, falling back to exact `/asb/<token>` lookup in the unfiltered picker list — zero unsafe keystrokes and zero wrong-asset risk.

## Recommended next step
Predict **GO (8/10)**. Proceed to Phase 3 BDD Scaffolding (`SCENARIO.md`) and Phase 4 Implementation Plan (`PLAN.md`).
