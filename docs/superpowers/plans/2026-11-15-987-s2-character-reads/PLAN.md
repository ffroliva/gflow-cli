# Plan: Slice 2 of #987 — `gflow character list` / `show` and voice catalog on `flow.google.com`
> Created: 2026-11-15 | Status: COMPLETED | Branch: `feature/987-character-reads-migrated`

## Context
- **Goal:** Port Slice 2 of contributor PR #987 (`@ziyedbe`) onto current `develop` so `gflow character list` and `gflow character show` (and their MCP twins `gflow_character_list` and `gflow_character_show`) work on accounts Flow serves from `flow.google.com` by reading its `Zzl0ze` `batchexecute` project reply when `flow.projectInitialData` is refused (HTTP 401/404 under `GFLOW_CLI_FLOW_HOST=auto`) or when `GFLOW_CLI_FLOW_HOST=flow.google.com`. Also update the offline voice catalog (`VOICES`) to 30 voices (`Enceladus` added), add `voice_from_wire`, and fix the route-prefix bug (`startswith(target)` → exact path `is_at(url, target)`) so `/project/<id>/character/<eid>` is never mistaken for `/project/<id>`.
- **Scope boundary for `@Name` mentions:** `services/mentions.py` passes `migrated_read=False` to `client.list_characters(project_id, migrated_read=False)` in Slice 2 so `@Name` mention routing on image and video keeps `develop`'s exact behaviour. Slice 5 enables `flow.google.com` character catalog reads for `@Name` mentions, verified with a billed e2e.
- **Scenario doc:** Inline below (`## Scenario Matrix`).
- **Predict verdict:** GO (8.4/10) — see `## Pre-Implementation Debate` below.
- **Assessment reference:** `2026-11-15-987-takeover-assessment.md` (§2 rows 5 & 12, §5 rescoping note and Slice 2, §6).

## Pre-Implementation Debate (`/gflow:predict`)

| Persona | Weight | Score | Verdict & Key Constraints |
|---|---|---|---|
| **1. Systems Architect** | 25% | 9/10 | **GO.** `#1249` split `FlowApiClient` (`_flow_characters.py`, `_flow_workspace.py`) and `MigratedComposer` (`_mc_common.py`, `_mc_editor_gate.py`, `migrated_composer.py`) into mixins. Port `list_characters` / `get_character` / `list_voices` into `CharacterSurface` (`_flow_characters.py`), reusing `WorkspaceSurface._labs_project_route_refused(..., event="character.labs_route_refused")`. Put `is_at` and public `rpc_reply` in `_mc_common.py` (re-exported on `migrated_composer.py`), and keep `migrated_character.py` scoped strictly to Slice 2 (`PROJECT_READ_RPC = "Zzl0ze"`, `CharacterImage`, `ProjectRead`, `parse_project_read`, `read_project`). |
| **2. Security & reCAPTCHA Specialist** | 25% | 8/10 | **GO.** `Zzl0ze` replies carry signed `lh3.googleusercontent.com/asb/...` image URLs at entity slot `[5]` and workflow media slots. `Character` DTOs must store only UUIDs (`entity_id`, `workflow_ids`, `thumbnail_media_id`) and never signed URLs. When `parse_project_read` raises `WireFormatError` on a drifted `Zzl0ze` shape, `discovery_head(payload)` must scrub capability URLs (`https://lh3.googleusercontent.com/asb/...`) and tokens before slicing so no capability link leaks into `detail` or `discovery`. |
| **3. Performance & Playwright Engineer** | 20% | 8/10 | **GO.** `read_project` opens `/project/<id>` (or `reload()`s when already at `/project/<id>`) and awaits `batchexecute` RPC `Zzl0ze`. Two hazards: (1) if the pooled page is parked on `/project/<id>/character/<eid>`, `str(page.url).startswith(target)` falsely matches `/project/<id>` and `reload()` reloads the character sub-route or `ensure_editor` skips navigating to the project editor — `is_at(url, target)` comparing `urlsplit` `(scheme, netloc, path.rstrip('/'))` fixes all three sites (`read_project`, `ensure_editor`, `rename_project`); (2) a cold tab can miss the `Zzl0ze` response listener registration race — retry `read_project` once with a reload on `TransportTimeoutError`. |
| **4. CLI & MCP UX Designer** | 15% | 9/10 | **GO.** CLI flags and MCP tool schemas for `gflow_character_list`, `gflow_character_show`, and `gflow_character_voices` need no new parameters, but MCP `description=` and `_ENTITIES_DESCRIPTION` in `src/gflow_cli/mcp/tools.py` (and `docs/MCP.md`) currently tell agents that `gflow_character_list` fails with a wire-format error (#875) on `flow.google.com`. Update `description=` and docs so MCP agents know `gflow_character_list` and `gflow_character_show` read the project on both hosts. |
| **5. Devil's Advocate / Maintainer** | 15% | 8/10 | **GO.** Enforce the rescoping rule: Slice 2 ships only what Slice 2 calls (`parse_project_read`, `read_project`, `CharacterImage`, `ProjectRead`, `VOICES` + `voice_to_wire`/`voice_from_wire`, `is_at`). Do not include Slice 3/4 dead code (`create_from_files`, `patch_character`, `original_image_url`). Keep every new function well under `check_size.py` ceilings (`C901 <= 10`, `PLR0915 <= 50`). |

**Weighted Score:** `8.45 / 10` → **GO**.

## Scenario Matrix (`/gflow:scenario`)

| # | Dimension | Scenario | Given / When | Expected Outcome (CLI & API) | MCP Twin (`gflow_character_*`) |
|---|---|---|---|---|---|
| S1 | Happy Path (labs host) | Account served `labs.google` (`auto` or `labs.google`) | `flow.projectInitialData` returns HTTP 200 with CHARACTER entities | `list_characters` returns parsed `Character` rows from `projectInitialData`; no browser navigation to `flow.google.com` | `gflow_character_list` returns `status="ok"`, `characters=[...]`, `count=N` |
| S2 | Happy Path (`flow.google.com` fallback on `auto`) | Account served `flow.google.com` under `GFLOW_CLI_FLOW_HOST=auto` | `flow.projectInitialData` returns HTTP 404 (`Flow RPCs have been deprecated`) or HTTP 401; `Zzl0ze` returns project array | Logs `character.labs_route_refused`, checks out page, reads `Zzl0ze`, returns `Character` list with canonicalized voice (`aoede` → `Aoede`), personality, `workflow_ids`, and `thumbnail_media_id` | `gflow_character_list` and `gflow_character_show` succeed (`status="ok"`) on `flow.google.com` without raising exit 7 |
| S3 | Happy Path (explicit `flow.google.com`) | `GFLOW_CLI_FLOW_HOST=flow.google.com` | `client.list_characters(project_id)` called | Skips `flow.projectInitialData` GET entirely; reads `Zzl0ze` directly | `gflow_character_list` / `gflow_character_show` read via `Zzl0ze` |
| S4 | Boundary: Empty project on `flow.google.com` | Project with 0 characters (`Zzl0ze` slot `[5]` is `null` or contains only non-character entities) | `parse_project_read` / `list_characters` called | Returns `characters=[]` (never raises `WireFormatError`) | `gflow_character_list` returns `status="ok"`, `characters=[]`, `count=0` |
| S5 | Boundary: Project with N characters & workflow image refs | `Zzl0ze` reply with multiple characters (`1` = CHARACTER, `2` = non-character skipped), workflow image slots (`[22]` at slot `[2]`), thumbnail media ID matched by workflow or URL stem | `parse_project_read(payload)` called | Extracts only type-`1` characters with valid UUIDs, ordered `workflow_ids`, `thumbnail_media_id`, personality (`[2][2]`), and voice (`[2][1][0][0]` mapped via `voice_from_wire`) | `gflow_character_list` returns all N characters; no signed URL (`https://`, `fifeUrl`, `/asb/`) appears in payload |
| S6 | Input Validation: Unknown ID or name on `show` | `get_character(project_id, entity_id="missing")` or `name="Missing"` on `flow.google.com` | `Zzl0ze` read succeeds, selector misses | Raises `ConfigurationError` (exit 11) with `character not found` | `gflow_character_show` returns RFC 9457 error envelope (`exit_code=11`) |
| S7 | Input Validation: Ambiguous name on `show` | Two characters share `display_name` in `Zzl0ze` | `get_character(project_id, name="Dup")` | Raises `ConfigurationError` (exit 11) listing matching entity IDs | `gflow_character_show` returns RFC 9457 error envelope (`exit_code=11`) |
| S8 | Malformed Upstream Wire (`Zzl0ze` drift) | `Zzl0ze` returns non-list, empty list, non-UUID `payload[0]`, or non-list non-null `payload[5]`, containing a capability URL `https://lh3.googleusercontent.com/asb/SECRET` | `parse_project_read(payload)` called | Raises `WireFormatError` (exit 7, `route="batchexecute:Zzl0ze"`) with `discovery_head` redacted so `SECRET` never appears in `str(exc)` or `exc.discovery` | `gflow_character_list` / `show` returns `wire-format` error with redacted detail |
| S9 | Dependency Failure: Pinned `labs.google` or non-refusal error | `GFLOW_CLI_FLOW_HOST=labs.google` with HTTP 404, or `auto` with HTTP 500 `ServerError` | `list_characters(project_id)` called | Propagates original error without calling `migrated_character.read_project` | Propagates error envelope |
| S10 | Timing / Cold-tab Timeout on `Zzl0ze` | First `rpc_reply` for `Zzl0ze` raises `TransportTimeoutError` | `read_project(page, project_id)` called | Logs `character.project_read_retry` and retries once with `page.reload()`; if second attempt succeeds, returns `ProjectRead` | Transparently succeeds on retry |
| S11 | State / Route Matching (`is_at`) | Pooled page is at `/project/<id>/character/<eid>` (or `/project/<id>2`) when `read_project`, `ensure_editor`, or `rename_project` runs for `/project/<id>` | `is_at(page.url, target)` evaluated | Returns `False` for sub-routes and sibling prefixes (`True` only for exact path `/project/<id>` or `/project/<id>/` with any query/fragment), forcing `page.goto(target)` instead of staying on `/character/<eid>` | Subsequent character read or generation on the same pooled page navigates to `/project/<id>` |
| S12 | Voice Catalog & Wire Case | `VOICES` constant, `voice_to_wire`, `voice_from_wire`, `client.list_voices` | Inspected / called | 30 preset voices (includes `Enceladus`), `voice_to_wire("Aoede") == "aoede"`, `voice_from_wire("aoede") == "Aoede"`, unknown wire voice preserved verbatim | `gflow_character_voices` returns all 30 voices (`count=30`) without opening a browser |

## Tasks

- [x] **Task 1 — Plan (`PLAN.md`)**
  - **Files:** `docs/superpowers/plans/2026-11-15-987-s2-character-reads/PLAN.md`
  - **Action:** Write this plan with the 5-persona `/gflow:predict` pass, the `/gflow:scenario` table (including MCP twins), and the `is_at` route-matching design, then commit.

- [x] **Task 2 — Red Tests (TDD first)**
  - **Files:**
    - `tests/api/fixtures/zzl0ze_reply.json` (new)
    - `tests/api/transports/test_migrated_character.py` (new)
    - `tests/api/test_client_character_host.py` (new)
    - `tests/api/transports/test_migrated_project.py` (modify — add `is_at` exact-route and `rename_project` sub-route navigation tests)
    - `tests/api/transports/test_migrated_composer.py` (modify — add `ensure_editor` character sub-route navigation test)
    - `tests/mcp/test_character_tools.py` (modify — add migrated-host `gflow_character_list`/`gflow_character_show` tests and tool `description=` truthfulness test)
    - `tests/api/test_character.py` & `tests/cli/test_cli_character.py` (modify — expect 30 voices including `Enceladus`)
  - **Action:** Port and add the failing tests first. Run `pytest` on each target, record the exact red failure reason for every test group, and commit the red tests.

- [x] **Task 3 — Implementation (Green)**
  - **Files:**
    - `src/gflow_cli/api/character.py` (modify — add `Enceladus` to `VOICES`, add `voice_to_wire` and `voice_from_wire`)
    - `src/gflow_cli/api/transports/batchexecute.py` (modify — export `discovery_head`, scrub full JSON string and `lh3.googleusercontent.com/asb/` capability URLs before slicing to 200 chars)
    - `src/gflow_cli/api/transports/_mc_common.py`, `src/gflow_cli/api/transports/_mc_editor_gate.py`, `src/gflow_cli/api/transports/migrated_composer.py` (modify — add `is_at`, export `rpc_reply`, replace `.startswith(target)` in `ensure_editor` and `rename_project` with `is_at`)
    - `src/gflow_cli/api/transports/migrated_character.py` (new — `PROJECT_READ_RPC`, `CharacterImage`, `ProjectRead`, `parse_project_read`, `read_project` with single timeout retry)
    - `src/gflow_cli/api/_flow_workspace.py` & `src/gflow_cli/api/_flow_characters.py` (modify — parameterize `event` in `_labs_project_route_refused`, route `list_characters` to `migrated_character.read_project` on `flow.google.com` or observed refusal, add `list_voices`)
    - `tests/api/test_client_character.py` (modify — attach `c.settings = Settings()` in `_client_with` fixture helper)
  - **Action:** Implement cleanly within `check_size.py` complexity/statement bounds, turn all tests green, and commit.

- [x] **Task 4 — MCP Twin & Documentation**
  - **Files:**
    - `src/gflow_cli/mcp/tools.py` (modify — update `_ENTITIES_DESCRIPTION` and `gflow_character_list` / `gflow_character_show` `description=` strings)
    - `docs/MCP.md`, `docs/REFERENCE_STRATEGIES.md`, `KNOWN_ISSUES.md`, `docs/CHARACTER.md`, `docs/USAGE.md`, `skills/video-production/SKILL.md` (modify)
    - `CHANGELOG.md` (modify — add `[Unreleased]` `### Fixed` entry crediting `@ziyedbe (#987)`)
    - `website/docs/**` & `plugins/**` (regenerate via `scripts/ci/generate_website_docs.py` and `scripts/ci/generate_plugin_skills.py`)
  - **Action:** Update MCP descriptions, docs, and changelog; sync generated mirrors; commit.

- [x] **Task 5 — Full Impeccable Gate Sweep**
  - **Action:** Run all 11 CI/check gates (`check_repo_hygiene.py`, `check_size.py`, `check_doc_links.py`, `check_website_docs_pii.py`, `generate_website_docs.py --check`, `check_council_memory.py`, `generate_plugin_skills.py --check`, `ruff check src tests`, `ruff format --check src tests`, `pyright src`, `pytest -q -n 4 tests/api tests/mcp tests/cli`) and verify exit 0 on every gate.

## Exit Criteria
- [x] All scenario rows S1–S12 covered by automated offline unit/integration tests.
- [x] Red → Green progression recorded for each test suite.
- [x] MCP `description=` and docs accurately describe `gflow_character_list` / `gflow_character_show` on `flow.google.com`.
- [x] All 11 CI gates exit 0.
- [x] Every commit carries `Co-authored-by: ziyedbe <ziyedbecha@hotmail.fr>` and zero AI attribution.
