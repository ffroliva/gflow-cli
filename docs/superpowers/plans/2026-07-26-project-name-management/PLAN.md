# Feature Implementation Plan: Project Naming, Dual-Side Sync & Searchability (#381)

> **For agentic workers:** Run `/gflow:status --feature project-name-management` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Make Google Flow projects first-class, named, searchable containers by establishing dual-side title synchronization (Google Flow UI in browser + local SQLite database), supporting creation-time naming, prompt slugging, and retroactive project renaming, verified by an E2E test suite.

**Architecture:** Dual-side state synchronization. Creation and renaming operations propagate project titles to both Google Flow's tRPC/DOM UI server endpoints (`POST project.createProject`, `POST project.renameProject`) and the local SQLite catalog (`projects` table). Read operations (`queries.py`, `cli_data.py`, `gflow project list`, `gflow_list_projects`) surface `title` across all CLI and MCP channels.

**Predict verdict:** GO — confidence 9/10

**Risk Register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | Title drift between Google Flow UI and local SQLite DB | Update Flow server API / DOM UI first, then commit to local DB within transaction |
| Medium | Overwriting real project title on existing project targeting (`--project <uuid>`) | Only set/update title when explicitly requested via `--project-name` or on fresh project creation |
| Medium | CLI / MCP schema asymmetry when adding `--project-name` | Enforced programmatically via `tests/mcp/test_cli_parity.py` gate |
| High | Headed browser / Flow UI automation selector drift on title renaming | Implement tRPC REST fallback in `FlowApiClient` alongside DOM editor selector |

---

## File structure

### New files
```
src/gflow_cli/cli_project.py
  gflow project subcommand group (list, show, rename, create)
tests/test_cli_project.py
  Unit tests for gflow project CLI subcommands
tests/e2e/test_project_naming_e2e.py
  E2E test suite verifying dual-side sync against Playwright transport / Flow UI
```

### Modified files
```
src/gflow_cli/data/queries.py
  Expose title in ProjectRow and _LIST_PROJECTS_SQL
src/gflow_cli/cli_data.py
  Render TITLE column in _emit_projects_table and JSON outputs
src/gflow_cli/data/repository.py
  Add update_project_title and get_project methods to DataRepository
src/gflow_cli/_cli_helpers.py
  Add slugify_project_name helper function
src/gflow_cli/api/client.py
  Propagate title in create_project and add rename_project method
src/gflow_cli/api/transports/ui_automation.py
  Propagate project_name in _enter_editor and add rename_project DOM handler
src/gflow_cli/cli_image.py
  Add --project-name / --project-title options to t2i and i2i commands
src/gflow_cli/cli_video.py
  Add --project-name / --project-title options to t2v, i2v, r2v commands
src/gflow_cli/cli.py
  Import and register _project_group in main Click application
src/gflow_cli/mcp/tools.py
  Add project_name parameter to gflow_generate_image/video and title in gflow_list_projects
tests/test_data_queries.py
  Assert title in list_projects tests
tests/test_cli_data.py
  Assert TITLE header and title JSON fields in CLI data tests
tests/cli/test_helpers.py
  Unit tests for slugify_project_name helper
tests/mcp/test_cli_parity.py
  Update CLI_TO_MCP and _MCP_EXEMPT for project commands
```

---

## Task 1 — Catalog Read Parity & Repository Layer (Test + Implementation)

**What:** Expose `title` in catalog queries, dataclasses, CLI table rendering, and add repository helpers (`update_project_title`, `get_project`).

**Files:**
- `src/gflow_cli/data/queries.py` — Add `title` to `ProjectRow` and `_LIST_PROJECTS_SQL`
- `src/gflow_cli/cli_data.py` — Update `_emit_projects_table` to render `TITLE`
- `src/gflow_cli/data/repository.py` — Add `update_project_title` and `get_project`
- `tests/test_data_queries.py` — Assert project titles in query results
- `tests/test_cli_data.py` — Assert `TITLE` header and JSON title field

**Steps:**
- [ ] Update `ProjectRow` in `queries.py` to include `title: str | None`.
- [ ] Update `_LIST_PROJECTS_SQL` to select `p.title AS title`.
- [ ] Add `update_project_title(profile_name, flow_project_id, title)` and `get_project(profile_name, flow_project_id)` to `DataRepository` in `repository.py`.
- [ ] Update `_emit_projects_table` in `cli_data.py` to include `TITLE` column.
- [ ] Add unit tests asserting `title` in `test_data_queries.py` and `test_cli_data.py`.

---

## Task 2 — Smart Project Slug Helper & Unit Tests

**What:** Add `slugify_project_name(prompt, prefix)` helper for auto-deriving smart project titles from prompt text.

**Files:**
- `src/gflow_cli/_cli_helpers.py` — Implement `slugify_project_name`
- `tests/cli/test_helpers.py` — Unit tests for `slugify_project_name`

**Steps:**
- [ ] Implement `slugify_project_name` in `_cli_helpers.py` (strips punctuation, lowercases, replaces spaces with dashes, caps length at 40 chars).
- [ ] Add unit test suite in `tests/cli/test_helpers.py` covering normal prompts, empty prompts, special characters, and long string truncation.

---

## Task 3 — Dual-Side API Client & Transport Extension

**What:** Propagate `title` in project creation and implement `rename_project` in `FlowApiClient` and `ui_automation.py` to update Google Flow on the browser/API side.

**Files:**
- `src/gflow_cli/api/routes.py` — Add `RENAME_PROJECT` tRPC endpoint if applicable or document UI endpoint
- `src/gflow_cli/api/client.py` — Update `create_project` to accept and pass `title`, and add `rename_project` method
- `src/gflow_cli/api/transports/ui_automation.py` — Support project naming and renaming in browser page editor

**Steps:**
- [ ] Update `create_project` in `client.py` to send `projectTitle` payload.
- [ ] Add `rename_project(project_id, new_title)` method in `client.py` targeting Google Flow project update endpoint.
- [ ] Update `_enter_editor` in `ui_automation.py` to ensure project titles are set and updated on browser navigation if needed.

---

## Task 4 — Generation Commands & MCP Parity (`--project-name`)

**What:** Add `--project-name` / `--project-title` flags to generation CLI commands (`t2i`, `i2i`, `t2v`, `i2v`, `r2v`) and update MCP tool schemas.

**Files:**
- `src/gflow_cli/cli_image.py` — Add `--project-name` option and integrate `slugify_project_name`
- `src/gflow_cli/cli_video.py` — Add `--project-name` option and integrate `slugify_project_name`
- `src/gflow_cli/mcp/tools.py` — Update `gflow_generate_image`, `gflow_generate_video`, and `gflow_list_projects`
- `tests/mcp/test_cli_parity.py` — Update CLI to MCP parity mappings

**Steps:**
- [ ] Add `--project-name` / `--project-title` option to `_project_and_entity_options` in `cli_image.py`.
- [ ] Thread `project_name` through `t2i`, `i2i`, `_run_t2i`, `_run_i2i`, using `slugify_project_name` as fallback.
- [ ] Add `--project-name` option to `cli_video.py` generation commands.
- [ ] Update `gflow_generate_image` and `gflow_generate_video` MCP tools to accept `project_name: str | None = None`.
- [ ] Update `gflow_list_projects` MCP tool to return `"title": r.title`.
- [ ] Update `tests/mcp/test_cli_parity.py` to register all leaf commands.

---

## Task 5 — `gflow project` Subcommand Group & CLI Integration

**What:** Build `cli_project.py` with `list`, `show`, `rename`, `create` subcommands and register in `cli.py`.

**Files:**
- `src/gflow_cli/cli_project.py` — Subcommands `list`, `show`, `rename`, `create`
- `src/gflow_cli/cli.py` — Register `_project_group`
- `tests/test_cli_project.py` — Unit tests for all project subcommands

**Steps:**
- [ ] Create `src/gflow_cli/cli_project.py` implementing `project` Click group and subcommands `list`, `show`, `rename`, `create`.
- [ ] Register `project` group in `cli.py`.
- [ ] Add comprehensive CLI unit tests in `tests/test_cli_project.py`.

---

## Task 6 — End-to-End Test Suite & Verification (Statement of Done)

**What:** Develop and run `tests/e2e/test_project_naming_e2e.py` to verify dual-side sync against real Playwright transport / Flow UI.

**Files:**
- `tests/e2e/test_project_naming_e2e.py` — E2E test file

**Steps:**
- [ ] Write `test_project_creation_with_custom_name_e2e` verifying title in Flow UI and local DB.
- [ ] Write `test_project_rename_dual_side_e2e` verifying retroactive rename of `"gflow-cli t2i"` project in Flow UI and local DB.
- [ ] Execute `pytest tests/e2e/test_project_naming_e2e.py` against live transport.

---

## Definition of Done (Iron Law)

- [ ] All task steps checked off
- [ ] Dual-side sync verified: Project title creation & rename reflect in Google Flow UI AND internal SQLite DB
- [ ] Retroactive rename of legacy `"gflow-cli t2i"` projects verified on both sides
- [ ] E2E test `tests/e2e/test_project_naming_e2e.py` passes
- [ ] `/gflow:check` green (`ruff`, `pyright`, `pytest` coverage ≥ 80%)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Documentation updated (`docs/CLI.md`, `README.md`)
