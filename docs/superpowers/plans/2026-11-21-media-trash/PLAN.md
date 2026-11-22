# Media Trash and Restore Implementation Plan (`#1363`)

> **For agentic workers:** Run `/gflow:status --feature 2026-11-21-media-trash` to find
> the next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Provide `gflow media trash <id>` and `gflow media restore <id>` (plus MCP twins
`media_trash` and `media_restore`) so users and agents can move generated images, videos, or
scenes to Flow's restorable Trash and restore them cleanly via the `batchexecute` wire RPC
`pGCYOe` at zero credit cost, without browser UI dependencies.

**Architecture:**
- **Wire Transport (`src/gflow_cli/api/transports/_media_trash_wire.py`, `src/gflow_cli/api/flow_wire.py`):**
  Constructs and sends `pGCYOe` batchexecute mutation payloads using the profile's saved session
  cookies and `at` token directly. Falls back to browser page context evaluation when session
  cookies are unreadable or expired.
- **Client Seam (`src/gflow_cli/api/client.py`):** Exposes `FlowApiClient.trash_media(media_id, project_id)`
  and `FlowApiClient.restore_media(media_id, project_id)` methods.
- **Domain Service (`src/gflow_cli/services/media_trash.py`):** Owns catalog lookup by media UUID,
  catalog asset ID, or workflow UUID via `DataRepository`, prevents cross-profile ambiguity
  (requiring `--profile` when multiple profiles match), coordinates the wire mutation, and updates
  local catalog asset state.
- **Thin CLI Adapter (`src/gflow_cli/cli_media.py`, registered in `src/gflow_cli/cli.py`):** Exposes
  `@spends_credits(False)` Click command group `gflow media` with `trash` and `restore` subcommands.
  Enforces interactive confirmation prompt unless `--yes` / `-y` is passed; fails with exit 2
  in non-interactive environments without `--yes`.
- **Thin MCP Adapter (`src/gflow_cli/mcp/media_trash_tools.py`, re-exported in `src/gflow_cli/mcp/tools.py`):**
  Exposes MCP tool handlers `media_trash` (requiring `confirm: bool = True`) and `media_restore`.
  Keeps tool bodies in a sibling module to respect `check_size.py` limits in `mcp/tools.py`.

---

## Predict verdict: GO — confidence 8.2/10

Five adversarial personas evaluated the proposal before implementation. No persona returned `STOP`.
Full analysis recorded in [`PREDICT.md`](PREDICT.md).

### Persona findings
1. **Architect — GO (8/10):** Placement adheres to modular boundaries (transport -> client -> service -> CLI/MCP). Wire-first transport is mandatory because Flow's Angular CDK virtual scroller unmounts off-screen tiles, making UI click automation unviable.
2. **Security / reCAPTCHA — GO (9/10):** `pGCYOe` requires no reCAPTCHA token (`has_token_slot: false`) and spends 0 credits. Operation is soft deletion to restorable Trash, not irreversible purge.
3. **Performance / Playwright — GO (9/10):** Direct wire call runs in ~500–800 ms vs ~50 s for a full Playwright page and DOM hover cycle. Zero headless Chromium overhead when cookies are valid.
4. **CLI and MCP UX / Cross-platform — CAUTION (7/10):** Trashing is destructive to project views; must require `--yes` on CLI (or interactive prompt) and explicit `confirm=True` on MCP. Ambiguous catalog matches across profiles must be rejected with `DataStoreError` (exit 16).
5. **Devil's Advocate — GO (8/10):** Shipping both `trash` and `restore` uses the identical RPC (`pGCYOe`) with minor shape adjustments, guaranteeing users and e2e tests can safely undo operations. Task 1 is capturing the literal 17-character field mask string.

---

## Risk register

| Severity | Risk | Mitigation |
|---|---|---|
| **Critical** | Accidental trashing in scripts or non-interactive shells | Require `--yes` / `-y` in CLI; fail fast with exit 2 if non-interactive without `--yes`. |
| **Critical** | Autonomous agent accidentally trashing assets via MCP | Require `confirm: bool = True` on `media_trash` MCP tool; return Problem Details if false. |
| **High** | Cross-profile identifier collision | `DataRepository` lookup checks matching profiles; raises `DataStoreError` (exit 16) demanding `--profile` if ambiguous. |
| **High** | Field mask string mismatch | Task 1 runs `--show-mask` to extract the literal 17-character field mask before wiring constants. |
| **Medium** | Stale session cookies causing wire rejection | Gracefully fall back to browser page context evaluation (`FlowApiClient`) when direct wire returns 401/redirect. |

---

## File structure

### New files
```text
src/gflow_cli/cli_media.py
  CLI commands for `gflow media trash` and `gflow media restore`.
src/gflow_cli/services/media_trash.py
  Domain service resolving identifiers, verifying profile scope, and dispatching wire mutations.
src/gflow_cli/api/transports/_media_trash_wire.py
  Wire payload builders and response frame parsers for pGCYOe trash and restore.
src/gflow_cli/mcp/media_trash_tools.py
  MCP tool handlers for media_trash and media_restore.
tests/services/test_media_trash.py
  Unit tests for media trash service, ambiguity guards, and payload encoding.
tests/cli/test_cli_media.py
  CLI integration tests for argument validation, confirmation prompt, and JSON output.
tests/mcp/test_media_trash_tools.py
  MCP tool tests for confirmation requirements and parity.
tests/features/test_media_trash_steps.py
  pytest-bdd step implementations for tests/features/media_trash.feature.
tests/e2e/test_media_trash_e2e.py
  Live zero-credit end-to-end verification test on flow.google.com.
```

### Modified files
```text
src/gflow_cli/cli.py
  Register the `media` Click group.
src/gflow_cli/api/client.py
  Add `trash_media` and `restore_media` methods to FlowApiClient.
src/gflow_cli/api/flow_wire.py
  Add pGCYOe allowlist and direct mutation helper.
src/gflow_cli/mcp/tools.py
  Re-export media_trash and media_restore tool handlers.
docs/USAGE.md
  Document `gflow media trash` and `gflow media restore`.
docs/MCP.md
  Document `media_trash` and `media_restore` MCP tools.
docs/FLOW_WIRE_RPCS.md
  Update pGCYOe entry with exact payload shapes and field mask.
CHANGELOG.md
  Record media trash and restore in [Unreleased].
```

---

## Task 1 — Field mask capture and unit test scaffold (test scaffold)

**What:** Extract the literal unredacted 17-character field mask string from Flow's `pGCYOe`
request using `scripts/dev/spike_1363_trash.py --show-mask`, define it as a constant in
`src/gflow_cli/api/transports/_media_trash_wire.py`, and write red unit tests in
`tests/services/test_media_trash.py`.

**Files:**
- `src/gflow_cli/api/transports/_media_trash_wire.py`
- `tests/services/test_media_trash.py`

**Steps:**
- [x] Run `python scripts/dev/spike_1363_trash.py --show-mask` or inspect Flow frontend bundle to record the literal 17-character field mask string. (Captured 2026-11-21: `metadata.archived`)
- [x] Define constants and payload builder signatures (implemented in Task 3).
- [x] Write `build_trash_payload(media_id, project_id)` and `build_restore_payload(media_id, project_id)`.
- [x] Write `parse_trash_response(frame)` and `parse_restore_response(frame)` asserting slot `[0][0][3][2]`.
- [x] Create `tests/services/test_media_trash.py` asserting exact payload shapes, field mask lists, and response parsing.

**Tests created (red):**
- [x] `test_build_trash_payload_matches_measured_shape` — asserts exact inner `pGCYOe` list structure for trash.
- [x] `test_build_restore_payload_matches_measured_shape` — asserts exact inner `pGCYOe` list structure for restore.
- [x] `test_parse_trash_response_validates_trashed_state` — asserts `[0][0][3][2] is True` indicates successful trash.
- [x] `test_parse_restore_response_validates_restored_state` — asserts `[0][0][3][2] is None` indicates successful restore.

---

## Task 2 — BDD step bindings scaffold (test scaffold)

**What:** Implement pytest-bdd step definitions in `tests/features/test_media_trash_steps.py`
binding all offline scenarios from `tests/features/media_trash.feature`.

**Files:**
- `tests/features/test_media_trash_steps.py`

**Steps:**
- [x] Create `tests/features/test_media_trash_steps.py` referencing `tests/features/media_trash.feature`.
- [x] Implement step definitions for catalog mocking, CLI execution, exit code assertions, and RPC payload matching.
- [x] Mark tests as failing (red) until service and CLI layers are implemented in Tasks 3–5.

**Tests created (red):**
- [x] `test_media_trash_bdd_scenarios` — executes all offline BDD scenarios from `tests/features/media_trash.feature`.

---

## Task 3 — Wire mutation and `FlowApiClient` integration

**What:** Connect `_media_trash_wire.py` to `FlowApiClient` and `flow_wire.py` to execute
`pGCYOe` directly with session cookies or fall back to browser page context evaluation.

**Files:**
- `src/gflow_cli/api/flow_wire.py`
- `src/gflow_cli/api/client.py`
- `tests/services/test_media_trash.py`

**Steps:**
- [x] In `src/gflow_cli/api/flow_wire.py`, add `pGCYOe` to `_ALLOWED_RPCIDS` and implement `direct_trash_media` and `direct_restore_media`.
- [x] In `src/gflow_cli/api/client.py`, add `trash_media(self, media_id: str, project_id: str) -> dict[str, Any]` and `restore_media(self, media_id: str, project_id: str) -> dict[str, Any]`.
- [x] Implement fallback to Playwright page context evaluation (`page.evaluate`) if direct HTTP wire returns 401/redirect or session cookies are stale.
- [x] Update unit tests in `tests/services/test_media_trash.py` to cover wire dispatch and fallback mocking.

**Tests created (green):**
- [x] `test_direct_trash_media_success` — verifies successful direct HTTP wire POST of `pGCYOe`.
- [x] `test_direct_trash_media_fallback_on_stale_cookie` — verifies fallback to page context on 401.

---

## Task 4 — Service layer and catalog identifier resolution

**What:** Implement `src/gflow_cli/services/media_trash.py` to resolve user-supplied IDs to
canonical `flow_media_id` and `flow_project_id`, prevent cross-profile ambiguity, and update
local catalog state.

**Files:**
- `src/gflow_cli/services/media_trash.py`
- `tests/services/test_media_trash.py`

**Steps:**
- [x] Create `src/gflow_cli/services/media_trash.py` defining `trash_media_service(...)` and `restore_media_service(...)`.
- [x] Use `DataRepository.get_asset_by_any_id` / `resolve_asset` to accept `flow_media_id`, local asset ID, or `flow_workflow_id`.
- [x] Raise `DataStoreError` (exit 16) when the identifier matches assets across multiple profiles and `--profile` is unset.
- [x] Dispatch wire mutation via `FlowApiClient`.
- [x] On success, update asset metadata in `DataStore` (if asset is cataloged locally) and return structured result dataclass.

**Tests created (green):**
- [x] `test_media_trash_service_resolves_local_id` — resolves asset ID to flow_media_id.
- [x] `test_media_trash_service_refuses_cross_profile_ambiguity` — raises exit 16 when ambiguous.
- [x] `test_media_trash_service_updates_local_catalog_status` — marks catalog record trashed.

---

## Task 5 — CLI command surface (`gflow media trash` and `gflow media restore`)

**What:** Create `src/gflow_cli/cli_media.py` implementing `media trash` and `media restore`,
wire into `src/gflow_cli/cli.py`, and add confirmation guards.

**Files:**
- `src/gflow_cli/cli_media.py`
- `src/gflow_cli/cli.py`
- `tests/cli/test_cli_media.py`

**Steps:**
- [x] Create `src/gflow_cli/cli_media.py` with `@click.group("media")` and subcommands `trash` and `restore`.
- [x] Add options: `--profile`, `--yes` / `-y` (for trash), `--json`, `-o` (`json` / `id` / `plain`).
- [x] In `trash`, check `sys.stdin.isatty()`: prompt `Move media <id> to Flow's Trash? [y/N]: ` if interactive and `--yes` not passed; raise `click.BadParameter` (exit 2) if non-interactive without `--yes`.
- [x] Register `media` group in `src/gflow_cli/cli.py`.
- [x] Write integration tests in `tests/cli/test_cli_media.py` testing `--help`, `--yes`, interactive prompt abort, and JSON stdout formatting.

**Tests created (green):**
- [x] `test_cli_media_trash_requires_yes_in_non_interactive_shell` — asserts exit 2 without `--yes`.
- [x] `test_cli_media_trash_with_yes_succeeds` — asserts exit 0 and JSON output.
- [x] `test_cli_media_restore_succeeds` — asserts exit 0 and JSON output.
- [x] `test_cli_media_trash_interactive_abort` — asserts exit 0 and "Aborted." message on 'n'.

---

## Task 6 — MCP surface parity (`media_trash` and `media_restore`)

**What:** Create `src/gflow_cli/mcp/media_trash_tools.py`, expose tools in `src/gflow_cli/mcp/tools.py`,
require `confirm: bool = False`, and update parity test suites.

**Files:**
- `src/gflow_cli/mcp/media_trash_tools.py`
- `src/gflow_cli/mcp/tools.py`
- `tests/mcp/test_media_trash_tools.py`
- `tests/mcp/test_cli_parity.py`

**Steps:**
- [x] Create `src/gflow_cli/mcp/media_trash_tools.py` declaring `media_trash(media_id: str, profile: str | None = None, confirm: bool = False)` and `media_restore(media_id: str, profile: str | None = None)`.
- [x] Enforce `confirm=True` on `media_trash`: return Problem Details error if `False`.
- [x] In `src/gflow_cli/mcp/tools.py`, import and register both tools under `@server.tool` and `@_guarded`.
- [x] Add tool definitions to `tests/mcp/test_cli_parity.py` to maintain 100% leaf parity.
- [x] Write unit tests in `tests/mcp/test_media_trash_tools.py` verifying tool invocation and confirmation refusal.

**Tests created (green):**
- [x] `test_mcp_media_trash_refuses_unconfirmed` — returns Problem Details error when `confirm=False`.
- [x] `test_mcp_media_trash_confirmed_succeeds` — succeeds when `confirm=True`.
- [x] `test_mcp_media_restore_succeeds` — restores media via MCP.
- [x] `test_cli_mcp_parity_includes_media_trash_and_restore` — passes `test_cli_parity.py`.

---

## Task 7 — Documentation and changelog update

**What:** Update project documentation and release changelog to describe media trash and restore commands.

**Files:**
- `docs/USAGE.md`
- `docs/MCP.md`
- `docs/FLOW_WIRE_RPCS.md`
- `CHANGELOG.md`

**Steps:**
- [x] In `docs/USAGE.md`, add a section for `gflow media trash` and `gflow media restore` with examples and confirmation options.
- [x] In `docs/MCP.md`, document `media_trash` and `media_restore` tools, parameters, and confirmation contract.
- [x] In `docs/FLOW_WIRE_RPCS.md`, record the verified literal field mask for `pGCYOe`.
- [x] In `CHANGELOG.md`, add entry under `[Unreleased]` for media trash and restore commands.
- [x] Run `python scripts/ci/check_doc_links.py` to ensure all cross-references resolve.

---

## Task 8 — Live E2E test verification

**What:** Implement `tests/e2e/test_media_trash_e2e.py` verifying the full trash and restore cycle
against a live Flow account at zero credit cost.

**Files:**
- `tests/e2e/test_media_trash_e2e.py`

**Steps:**
- [x] Create `tests/e2e/test_media_trash_e2e.py` marked `@pytest.mark.e2e @pytest.mark.e2e_image`.
- [x] Pick an existing generated image from the e2e profile's project.
- [x] Run `gflow media trash <id> --yes --json`, verify exit 0 and status "trashed".
- [x] Run `gflow media restore <id> --json`, verify exit 0 and status "restored".
- [x] Verify balance before and after run confirms zero credits were charged.

---

## Task 9 — Quality gates and repo hygiene

**What:** Execute all repository quality gates and verify clean CI readiness.

**Files:**
- (all touched files)

**Steps:**
- [x] Run `uv run ruff check src tests`
- [x] Run `uv run ruff format --check src tests`
- [x] Run `uv run pyright src`
- [x] Run `uv run python -m pytest -q --cov=gflow_cli tests/services/test_media_trash.py tests/cli/test_cli_media.py tests/mcp/test_media_trash_tools.py tests/features/test_media_trash_steps.py`
- [x] Run `uv run python scripts/ci/check_repo_hygiene.py`
- [x] Run `uv run python scripts/ci/check_size.py`
- [x] Run `uv run python scripts/ci/check_doc_links.py`

---

## Definition of done

- [x] All 9 tasks checked off in sequence.
- [x] Field mask literal captured and verified.
- [x] `gflow media trash` and `gflow media restore` functional on CLI and MCP.
- [x] Interactive confirmation prompt and `--yes` flag enforced.
- [x] Cross-profile ambiguity rejected with exit 16.
- [x] Unit, BDD, and E2E tests green.
- [x] Parity verified via `tests/mcp/test_cli_parity.py`.
- [x] `check_doc_links.py`, `check_size.py`, and `check_repo_hygiene.py` clean.
- [x] Documentation and CHANGELOG.md updated.

---

## Decision log

- **2026-11-21:** Decided to implement wire-first mutation (`pGCYOe`) rather than UI automation clicks. Rationale: The live spike proved that Flow's Angular CDK virtual scroller unmounts off-screen tiles, making DOM selector lookups for items deeper in project history impossible without fragile scrolling hacks. `pGCYOe` operates directly on `<media_id>` and `<project_id>` with no DOM dependencies.
- **2026-11-21:** Decided to implement both `media trash` and `media restore` together in this milestone. Rationale: Both operations use the identical RPC (`pGCYOe`) with minor shape adjustments. Providing `restore` ensures e2e tests can clean up after themselves and prevents users from being locked out of recovering accidentally trashed items from the CLI.
- **2026-11-21:** Decided to require an explicit `confirm: bool = True` parameter on MCP `media_trash`. Rationale: Prevents autonomous agents from accidentally trashing media assets without intentional confirmation.
- **2026-11-21 (TODO / Live-unverified items):**
  - **Restoring a never-trashed item:** Verify live Flow API response when restoring an active item (does Flow return error or succeed idempotently?).
  - **Trailing-null trimming in replies:** Parser tolerant of missing trailing elements implemented in `parse_mutation_response`; verify stability across varied live project sizes.
  - **Project-cover `o8DA4` thumbnail reset:** Verify whether trashing a media asset used as project thumbnail automatically clears/resets the thumbnail.
  - **Idempotency:** Verify live Flow behavior when trashing an already-trashed item or restoring an already-restored item.
