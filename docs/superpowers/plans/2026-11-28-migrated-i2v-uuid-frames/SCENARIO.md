# Scenario: In-project image UUIDs for `video i2v` Start/End frames on `flow.google.com` (#1542)

## Coverage map

| Dimension | Active? | Reason |
|---|---|---|
| D1 Auth & session lifecycle | Skipped | No auth/cookie/lease changes |
| D2 WAF / reCAPTCHA scoring | Skipped | Zero new token mints or uploads (`maseQ` bypassed) |
| D3 Selector cascade drift & Locale Invariance | **Active** | Frames picker option matching (`/asb/<token>`), `BOUND_CHIP`, `PICKER_CONFIRM` (#792), agent-only composer after reload |
| D4 Batch manifest & resume | Skipped | `gflow video chain` uses extracted local JPEGs, not UUIDs |
| D5 Concurrency & Page pool | Skipped | Runs inside existing `_generate_lock` single-page checkout |
| D6 Data layer (SQLite / DataStore) | **Active** | Reading `asset.flow_project_id` + `display_name` from `gflow.db` (non-image assets, missing DB, redacted history) |
| D7 Error propagation & exit codes | **Active** | Exit 36 (`FlowHostMigratedError`), exit 32 (`ReferenceNotFoundError`), exit 23 (`UiSelectorDriftError`), exit 7 (`WireFormatError`, aborted before send) |
| D8 Cross-platform paths | Skipped | UUID frames do not touch local filesystem paths |
| D9 Transport edge cases | **Active** | Grid reload before `apply_video_settings()`, `adopt_ids` non-pollution on lost submit reply, `eb1hJf` vs `nprQif` submit guards, mixed local+UUID frames, same UUID for Start & End |
| D10 Headless vs headed environment | Skipped | Uses existing headed Chrome transport unchanged |
| D11 Input validation & boundary values | **Active** | `*_in_project=True` without `*_ref_id`, truncated `…` captions, >120-char/control-char captions, cross-project UUID, uncatalogued UUID |
| D12 Observability & structured log contract | **Active** | `migrated.existing_frame_reload`, `migrated.existing_frame_option`, `migrated.existing_frame_bound`, zero `migrated.frame_uploaded`, no raw caption leaked in error details |
| D13 MCP surface parity | **Active** | `gflow_generate_video` -> `_resolve_payload_refs` (`start_image_ref_flow_project_id` / `end_image_ref_flow_project_id`) -> `worker/codec.py::build_video_request` -> `migrated_can_serve` + tool docstring/docs |

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D7 / D11 | Cross-project image UUID passed as Start or End frame on `flow.google.com` | **Critical** | `recorded_in_project` returns `False` → `_unported_form` refuses before opening Chrome/editor with `FlowHostMigratedError` (exit 36, `charged="no"`) | Unit |
| 2 | D7 / D11 | Same-project image UUID with missing/empty catalog `display_name` (e.g. `history_prompts=redacted` or pre-v0.58 row) | **Critical** | `_unported_form` refuses at `$0` with `FlowHostMigratedError` (exit 36, `"without a catalog caption"`) | Unit |
| 3 | D9 | Lost submit reply body on a reused in-project UUID frame (`#1112`) | **Critical** | Existing UUID is **excluded** from `adopt_ids` (only uploaded frame IDs ride `adopt_ids`), so an older clip sharing the same start frame is never wrongly adopted; `expect_media_id` / `expect_end_media_id` still guards the outgoing submit | Unit |
| 4 | D3 / D9 | Caption collision in the Frames picker (two images share identical caption, target at `option_index > 0`) | **Critical** | Picker matches option strictly by grid tile's `/asb/<token>`, clicks exact `nth(idx)`, never binds option 0 by name/position; wire guard still asserts exact `media_id` | Unit + E2E (`@e2e_auth`) |
| 5 | D9 | Start-only in-project UUID binds and submits on `eb1hJf` with zero `maseQ` upload | **Critical** | Grid token resolved → settings applied → Start chip bound (`BOUND_CHIP == 1`) → `eb1hJf` carries `start_id` and `_i2v_` key; `migrated.frame_uploaded` absent | Unit + E2E (`@e2e_auth` route-abort & `@e2e_video` live) |
| 6 | D9 | Start+End in-project UUIDs bind and submit on `nprQif` with zero `maseQ` upload | **Critical** | Both grid tokens resolved → settings applied → Start (`BOUND_CHIP == 1`) then End (`BOUND_CHIP == 2`) bound → `nprQif` carries both UUIDs and interpolation/first_last key; zero uploads | Unit + E2E (`@e2e_auth` route-abort & `@e2e_video` live) |
| 7 | D13 | MCP `gflow_generate_video(mode="i2v", initial_frame=<uuid>, project=<pid>)` (and `end_frame`) end-to-end key round-trip | **Critical** | `_resolve_payload_refs` writes `start_image_ref_flow_project_id` / `end_image_ref_flow_project_id` → `build_video_request` sets `*_in_project=True` iff matching `project_id` → `migrated_can_serve` returns `True` | Unit + Integration |
| 8 | D9 | Newly generated image not yet in initial grid load needs page reload | **High** | `await_existing_frame_tokens` reloads **before** `apply_video_settings()`, so video settings (model/aspect/Frames) are never wiped by the reload | Unit |
| 9 | D3 | Reload inside `await_existing_frame_tokens` lands on `#799` agent-only composer | **High** | Raises `FlowHostMigratedError` (`agent.refuse_unported("image-to-video")`, exit 36) instead of drifting | Unit |
| 10 | D3 / D11 | Catalog caption truncated with trailing `…` (e.g. `"Golden lantern over quiet harbor…"`) | **High** | `frame_search_query` strips trailing `…`/`.` before typing into `PICKER_SEARCH`; matches option by exact `/asb/<token>` | Unit + Spike measured |
| 11 | D3 | Stale/renamed caption in Flow (caption search returns 0 matches or omits target token) | **High** | Clears `PICKER_SEARCH` with `search.fill("")` (measured 0 `UpteDb` calls) and scans unfiltered picker list for exact `/asb/<token>`; binds if present, retries / raises `ReferenceNotFoundError` (exit 32) if absent — never binds wrong asset | Unit + Spike measured |
| 12 | D9 | Mixed local Start + in-project UUID End, and in-project UUID Start + local End | **High** | Local slot uploads via `maseQ` (and joins `adopt_ids`), UUID slot resolves grid token pre-settings and binds in place (excluded from `adopt_ids`); both slots verified on `nprQif` | Unit |
| 13 | D9 | Deleted/off-grid image UUID (catalog says same project, tile absent from grid) | **High** | Exhausts `EXISTING_REF_WAIT_S` reload budget and raises `ReferenceNotFoundError` (exit 32, `charged="no"`); never opens picker or submits | Unit |
| 14 | D11 | `GenerateVideoRequest` constructed with `start_image_ref_in_project=True` (or `end_...`) without corresponding `*_ref_id` | **High** | Raises `ValueError` in `_validate_frame_ref_ids()` | Unit |
| 15 | D12 | Error details on picker miss / unbound chip must not leak prompt-derived catalog caption | **High** | `ReferenceNotFoundError` / `UiSelectorDriftError` details report `media_id`, `chip_label`, and `offered` count — never the raw caption text | Unit |
| 16 | D13 | MCP `i2v` asset with `flow_project_id` alone (no `display_name`, no local file) | **High** | `_resolve_payload_refs` still refuses with `400 Reference Not Usable` (preserving `#995` contract) | Unit |
| 17 | D6 | CLI `_media_picker_metadata` ignores non-image (video) assets and handles missing/unreadable catalog gracefully | **Medium** | Returns `in_project=False`, no crash; `_unported_form` fails fast with exit 36 | Unit |
| 18 | D9 | Same in-project UUID passed for both Start and End frame (loop clip) | **Medium** | Grid token deduplicated (`dict.fromkeys`), Start binds chip 1, End binds chip 2, `nprQif` carries UUID for both | Unit |
| 19 | D3 | Cohort requiring `PICKER_CONFIRM` (`button.detail-add-to-prompt-btn`, `#792`) after option click | **Medium** | Shared `_confirm_frame_bound` clicks confirm after grace wait and verifies `BOUND_CHIP` count | Unit |
| 20 | D11 | Non-empty caption fails search sanitization (`> 120` chars, control chars, or bare `"…"`) | **Medium** | `frame_search_query` returns `""`; skips typing and safely checks unfiltered picker list by exact `/asb/<token>` | Unit |

## Must-cover before merge (Critical + High)
Items **1–16** (plus 17–20 are cheap pure unit tests and will be covered too).

## Deferred (Medium + Low — not blockers)
- Video-path `#546` catalog write-through (`RefreshResolver.heal` persistence on renamed video frame captions) — `search.fill("")` already binds recent renamed assets in-place without a reload; write-through can follow if needed.
- Virtual-scrolled projects where an older image tile is not in the initial DOM grid (same limit as `#995` on `image i2i`).

## Suggested BDD scenarios

```gherkin
@e2e @e2e_auth
Feature: In-project image UUIDs bind as video i2v frames on flow.google.com without upload
  A t2i -> review -> i2v workflow runs in the same Flow project across separate invocations.
  Passing an image's catalogued Flow media UUID as --initial-frame (and optionally --end-frame)
  with --project binds the existing asset in place via its grid thumbnail /asb/ token, never
  calling maseQ upload (#1542).

  Scenario: Start-only in-project image UUID binds in place and prepares an eb1hJf submit with zero uploads
    Given a migrated Flow project containing two catalogued images
    When the driver binds the start frame UUID in place and prepares a route-aborted i2v submit
    Then zero maseQ uploads were sent
    And the intercepted eb1hJf submit carries the start frame UUID and an i2v model key

  Scenario: Start and End in-project image UUIDs bind in place and prepare an nprQif submit with zero uploads
    Given a migrated Flow project containing two catalogued images
    When the driver binds both start and end frame UUIDs in place and prepares a route-aborted interpolation submit
    Then zero maseQ uploads were sent
    And the intercepted nprQif submit carries both frame UUIDs and an interpolation model key
```

*(Note: `tests/features/migrated_i2v_uuid_frames.feature` is tagged `@e2e @e2e_auth` and bound exclusively by `tests/e2e/test_migrated_i2v_uuid_frames_bdd.py` so `tests/features/test_e2e_binding_guard.py` enforces all 4 directions offline and the live regression runs at `$0`.)*

## Known-issues cross-reference
- **`KNOWN_ISSUES.md` § "Flow is migrating to `flow.google.com`"** (`#639`): previously stated that i2v-by-UUID rested on a positive observation of absence (picker tiles carry no media id). This feature **resolves** the same-project catalogued UUID case using grid-tile `/asb/<token>` identity (mirroring `#995` on `image i2i`), while foreign/un-captioned UUIDs and `@Name` frames remain unported (exit 36).
- **`KNOWN_ISSUES.md` § "Fresh generations may record without a `display_name`"** (`#543`/`#542`): rows without a stored `display_name` (async caption not yet stored or `history_prompts=redacted`) continue to exit 36 on `flow.google.com` until backfilled with `gflow data sync --names`.
