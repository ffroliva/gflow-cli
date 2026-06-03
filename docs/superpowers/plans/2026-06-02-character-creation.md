# Character Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `gflow character` (issue #145) — create reusable, project-scoped characters and reference them in generations for visual consistency.

**Architecture:** Hybrid transport per [[rest-path-capability-matrix]] — structural ops (`flow.createEntity`, `PATCH flowWorkflows`/`flow/entities`, `projectInitialData`) are direct REST/tRPC; **generation is UI-driven** (reCAPTCHA-walled — proven: direct POST → 403) via Flow's native JS + passive-capture, reusing gflow's existing editor automation. The 4-step create flow is a service-layer saga (persist-before-spend). Greenfield off `develop`; pattern-references @kittinan's #123, not stacked on it.

**Tech Stack:** Python 3.13, Playwright (Chrome-strategy), Click CLI, SQLite data layer, structlog, pytest + pytest-bdd. Full design: [`docs/CHARACTER.md`](../../CHARACTER.md); scenarios/acceptance: [`docs/superpowers/character-scenario.md`](../character-scenario.md).

---

## Phasing (multi-subsystem → sequential plans)

Per the writing-plans Scope Check, this feature spans subsystems. Each phase is an independently testable, mergeable slice. **This document fully details Phase 1**; Phases 2–5 are scoped roadmaps, each expanded to full TDD detail (its own `docs/superpowers/plans/` file) when reached.

| Phase | Slice | Deliverable | Credits |
|---|---|---|---|
| **1** | REST foundation + 403 fix | `gflow character list/show/voices`, entity REST client, data persistence, WAF error fix | none (unit/integration) |
| 2 | UI char-gen + create saga | `gflow character create` (face/body), persist-before-spend | live (gen) |
| 3 | Video reuse | `gflow video --character` (referenceEntities, async poll) | live (gen) |
| 4 | Full e2e DoD | live e2e tiers for all Critical+High scenarios | live |
| 5 | Polish/docs | CHANGELOG, USAGE/USER_GUIDE, council review, PR | none |

---

## File structure (whole feature)

- Create `src/gflow_cli/api/character.py` — frozen DTOs (`CharacterImageRequest`, `Character`, `CharacterImageRef`) + `_build_character_batch_body()` (entityContext) + `parse_characters()` (projectInitialData → list[Character]).
- Modify `src/gflow_cli/api/client.py` — add `create_entity()`, `list_characters()`, `get_character()`, `generate_character_image()` (Phase 2), `generate_video_with_entities()` (Phase 3); fix `_raise_for_non_retryable` 403 mapping.
- Modify `src/gflow_cli/api/routes.py` — add `CREATE_ENTITY`, `PROJECT_INITIAL_DATA`, `FLOW_ENTITIES` URL helpers.
- Modify `src/gflow_cli/api/transports/ui_automation.py` — Phase 2: character-editor nav + slot-add structural selector.
- Modify `src/gflow_cli/data/models.py` — append `OperationKind.CHARACTER` (StrEnum, stored as TEXT → **no migration needed for the enum**). A new migration is required ONLY if Phase-2 persistence needs character-specific columns; if so it is `src/gflow_cli/data/migrations/0006_add_character.sql` (next free number — `0005_add_chain_links.sql` exists; migrations are **`.sql`**, mirror `0004_add_scene_output_path.sql`).
- Create `src/gflow_cli/cli_character.py` — `character` Click group (`list`, `show`, `voices`, `create` [P2]); Modify `cli_video.py` — `--character` (P3); register group in the CLI entrypoint.
- Tests: `tests/api/test_character.py`, `tests/api/test_client_character.py`, `tests/test_errors_403.py`, `tests/features/character_create.feature` (+ steps), `tests/e2e/test_character_e2e.py` (P4).

---

## Phase 1 — REST foundation + 403 fix

### Task 1: Fix 403 → `WafRejectionError` (currently mislabeled `AuthExpiredError`)

**Files:**
- Modify: `src/gflow_cli/api/client.py` (`_raise_for_non_retryable`, ~line 1313-1325)
- Test: `tests/test_errors_403.py` (create)

Covers scenario #2 (Critical). `WafRejectionError` already exists in `errors.py`.

- [ ] **Step 1: Write the failing test**
```python
# tests/test_errors_403.py
import pytest
from gflow_cli.api.client import _raise_for_non_retryable
from gflow_cli.errors import AuthExpiredError, WafRejectionError

class _Resp:
    def __init__(self, status): self.status = status

def test_403_maps_to_waf_rejection():
    with pytest.raises(WafRejectionError):
        _raise_for_non_retryable(_Resp(403), "{}", route="batchGenerateImages")

def test_401_still_maps_to_auth_expired():
    with pytest.raises(AuthExpiredError):
        _raise_for_non_retryable(_Resp(401), "{}", route="createEntity")
```

- [ ] **Step 2: Run test to verify it fails**
Run: `.venv\Scripts\python.exe -m pytest tests/test_errors_403.py -v`
Expected: `test_403_maps_to_waf_rejection` FAILS (raises AuthExpiredError, not WafRejectionError).

- [ ] **Step 3: Implement the split** — in `_raise_for_non_retryable`, replace the `if resp.status in (401, 403):` block:
```python
    if resp.status == 401:
        raise AuthExpiredError(
            detail=f"HTTP {resp.status}", status=resp.status, instance=instance, route=route,
        )
    if resp.status == 403:
        # 403 on a Flow route is the reCAPTCHA/WAF wall, NOT auth expiry
        # (direct-REST generation is 403-walled — see docs/CHARACTER.md §11).
        raise WafRejectionError(
            detail=f"HTTP {resp.status}", status=resp.status, instance=instance, route=route,
        )
```
Ensure `WafRejectionError` is imported in client.py.

- [ ] **Step 4: Run tests** — `.venv\Scripts\python.exe -m pytest tests/test_errors_403.py -v` → PASS. Then run the existing auth/transport tests to catch any caller that assumed 403→AuthExpired: `.venv\Scripts\python.exe -m pytest tests/api -k "auth or transport or error" -q`. Fix any now-stale assertions (they were encoding the bug). **Explicitly verify the aisandbox 401-refresh-retry path is unchanged** (scenario #7) — the 401 branch still raises `AuthExpiredError`/`AisandboxAuthError` and the token-refresh-retry still fires; only 403 moved to `WafRejectionError`. (Transports already raise `WafRejectionError` for 403 independently, so this aligns client-level behaviour with them.)

- [ ] **Step 5: Commit** — `git add -A && git commit -m "fix(api): map HTTP 403 to WafRejectionError, not AuthExpiredError (reCAPTCHA wall)"`

### Task 2: `OperationKind.CHARACTER` (enum append — no migration)

**Files:**
- Modify: `src/gflow_cli/data/models.py` (`OperationKind` — append after the existing `SCENE_CREATE` member; there is **no `AVATAR`** member, that PR never merged)
- Test: `tests/data/test_models.py` (or the existing `OperationKind` test file)

`OperationKind` is a `StrEnum` stored as TEXT, so appending a member needs **no schema migration** and does **not** affect `test_exit_code_map_ordering_invariant` (that test is about error-class inheritance in `EXIT_CODE_MAP`, unrelated to `OperationKind`). Character persistence reuses the existing operations recorder with `kind="character"` (entityId + workflowIds carried in the existing metadata columns); a `0006_add_character.sql` migration is added **only if** Phase-2 persistence proves it needs a dedicated column (decide against the real `operations` schema then).

- [ ] **Step 1: failing test**
```python
def test_operation_kind_character_exists():
    from gflow_cli.data.models import OperationKind
    assert OperationKind.CHARACTER.value == "character"
```
- [ ] **Step 2: run → fails** — `.venv\Scripts\python.exe -m pytest tests/data -k character -v`
- [ ] **Step 3: implement** — append `CHARACTER = "character"` to `OperationKind`, immediately after `SCENE_CREATE`.
- [ ] **Step 4: run** — that test PASSES; then `.venv\Scripts\python.exe -m pytest tests/data -q` (incl. the existing migration + ordering-invariant tests) stays green.
- [ ] **Step 5: commit** `feat(data): add OperationKind.CHARACTER`.

### Task 3: `api/character.py` DTOs + `parse_characters()`

**Files:**
- Create: `src/gflow_cli/api/character.py`
- Test: `tests/api/test_character.py`

Covers list/show data shape (scenario #11, #12).

- [ ] **Step 1: failing test** (parse a projectInitialData fixture — build the fixture from `docs/CHARACTER_RECON.md` §6.5 / the captured shape):
```python
from gflow_cli.api.character import parse_characters, Character

def test_parse_characters_filters_to_character_entities():
    payload = {"projectContents": {"entities": [
        {"projectId":"p","entityId":"e1","entityInfo":{"entityType":"CHARACTER","displayName":"Ana",
            "characterInfo":{"imageReferences":[{"workflowId":"w1"}],
                "audioReferences":[{"presetVoiceId":"gacrux"}],"personalityNotes":"brave"}},
            "thumbnailMediaId":"m1"},
        {"projectId":"p","entityId":"e2","entityInfo":{"entityType":"SCENE"}},
    ]}}
    chars = parse_characters(payload)
    assert [c.entity_id for c in chars] == ["e1"]
    assert chars[0].display_name == "Ana"
    assert chars[0].voice == "gacrux"
    assert chars[0].workflow_ids == ["w1"]
```
- [ ] **Step 2: run → fails.**
- [ ] **Step 3: implement** frozen dataclasses + parser:
```python
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Character:
    entity_id: str
    display_name: str
    project_id: str
    workflow_ids: tuple[str, ...]
    voice: str | None
    personality: str | None
    thumbnail_media_id: str | None

def parse_characters(project_initial_data: dict) -> list[Character]:
    out: list[Character] = []
    entities = (project_initial_data.get("projectContents") or {}).get("entities") or []
    for e in entities:
        info = e.get("entityInfo") or {}
        if info.get("entityType") != "CHARACTER":
            continue
        ci = info.get("characterInfo") or {}
        out.append(Character(
            entity_id=str(e.get("entityId")),
            display_name=info.get("displayName") or "",
            project_id=str(e.get("projectId")),
            workflow_ids=tuple(r.get("workflowId") for r in (ci.get("imageReferences") or []) if r.get("workflowId")),
            voice=next((a.get("presetVoiceId") for a in (ci.get("audioReferences") or []) if a.get("presetVoiceId")), None),
            personality=ci.get("personalityNotes"),
            thumbnail_media_id=e.get("thumbnailMediaId"),
        ))
    return out
```
Also add `CharacterImageRequest` (frozen INPUT DTO for the CLI/saga: `prompt`, `aspect`, `model`, `image_reference_index`). **Do NOT add a wire-body builder** — under Option B (§11) generation is UI-driven/passive-capture, so Flow's own JS assembles the `batchGenerateImages` body (incl. `entityContext`); gflow never POSTs it (direct POST is 403-walled). Phase 2 only *reads* the captured response and asserts `parentEntityId == entityId`. **Redaction (scn #16):** the `Character` DTO deliberately carries only ids (`workflow_ids`, `thumbnail_media_id`) — **never signed `fifeUrl`s** — so nothing persisted in Phase 1 contains a `signature=`/`Expires=` URL; add a unit test asserting `parse_characters` never surfaces a signed URL field. (Signed-URL handling for generation results is a Phase-2 persistence + redaction test.)
- [ ] **Step 4: run → PASS.**
- [ ] **Step 5: commit** `feat(api): character DTOs + projectInitialData parser + entityContext body builder`.

### Task 4: client `create_entity()` + `list_characters()` / `get_character()`

**Files:**
- Modify: `src/gflow_cli/api/client.py`, `src/gflow_cli/api/routes.py`
- Test: `tests/api/test_client_character.py` (mock `_post_json`/`_get_json`)

Covers scenarios #11, #6/#7 (auth on these calls).

- [ ] **Step 1: failing test** — mock the client transport, assert `create_entity(project_id)` POSTs `{"json":{"projectId":pid}}` to the createEntity tRPC URL and returns the new `entityId`; assert `list_characters(project_id)` GETs projectInitialData (correct `?input=` encoding) and returns `list[Character]`.
- [ ] **Step 2: run → fails.**
- [ ] **Step 3: implement** (reuse `_post_json` client.py:494 / `_get_json` :587; URLs in routes.py: `LABS_TRPC_BASE`):
```python
async def create_entity(self, project_id: str) -> str:
    data = await self._post_json(
        routes.CREATE_ENTITY_URL, {"json": {"projectId": project_id}},
        content_type="application/json", route_name="createEntity")
    payload = _unwrap_trpc(data)  # add a small tRPC unwrap helper (result.data.json)
    entity_id = payload.get("entityId")
    if not entity_id:
        raise WireFormatError(detail="createEntity returned no entityId", route="createEntity")
    return str(entity_id)

async def list_characters(self, project_id: str) -> list[Character]:
    from urllib.parse import quote
    import json as _json
    inp = quote(_json.dumps({"json": {"projectId": project_id}}, separators=(",", ":")), safe="")
    data = await self._get_json(f"{routes.PROJECT_INITIAL_DATA_URL}?input={inp}", route_name="projectInitialData")
    return parse_characters(_unwrap_trpc(data))

async def get_character(self, project_id: str, *, entity_id: str | None = None, name: str | None = None) -> Character:
    chars = await self.list_characters(project_id)
    if entity_id:
        match = [c for c in chars if c.entity_id == entity_id]
    else:
        match = [c for c in chars if c.display_name == name]
    if not match:
        raise ConfigurationError(detail=f"character not found", remediation_hint="run `gflow character list`")
    if len(match) > 1:
        raise ConfigurationError(detail=f"ambiguous name '{name}'; ids: {[c.entity_id for c in match]}",
                                 remediation_hint="disambiguate with --id")
    return match[0]
```
Add the URL constants to routes.py (`CREATE_ENTITY_URL = f"{LABS_TRPC_BASE}/flow.createEntity"`, `PROJECT_INITIAL_DATA_URL = f"{LABS_TRPC_BASE}/flow.projectInitialData"`, `FLOW_ENTITIES_URL = f"{FLOW_API_BASE}/flow/entities"`). Add `_unwrap_trpc` (mirror `scripts/dev/character_create_spike.py`).
- [ ] **Step 4: run → PASS.**
- [ ] **Step 5: commit** `feat(api): character entity REST client (create/list/get)`.

### Task 5: CLI `gflow character list / show / voices`

**Files:**
- Create: `src/gflow_cli/cli_character.py`; Modify `src/gflow_cli/cli.py` (import the group at top + `main.add_command(character_group)`, mirroring the `_scene_group` registration ~cli.py:328); Modify `.env.template` if any new env.
- Test: `tests/test_cli_character.py` (Click `CliRunner`, mock client)

Covers scenarios #12, #13, #14 (exit 11), language-agnostic `voices`.

- [ ] **Step 1: failing tests** — `character list --project P` prints characters; `character show --project P --name X` exits 11 on collision; `character voices --json` prints preset voice ids. (Mirror `cli_scene.py` structure — read it for the `--project` required option, output formatting, exit-code mapping via `run_with_handlers`.)
- [ ] **Step 2: run → fails.**
- [ ] **Step 3: implement** the `character` Click group mirroring `cli_scene.py`. `--project` required on all; `show` takes `--id`/`--name` (not positional). `voices` returns the preset list (Gemini-TTS ids: `gacrux, aoede, charon, kore, callirrhoe, …` — source from a `VOICES` constant in `api/character.py`; mark TODO to fetch live if Flow exposes an endpoint). Never emit localized strings.
- [ ] **Step 4: run → PASS** + `ruff check`/`ruff format --check` + `pyright src` ([[pyright-src-whole-tree-gate]], [[multiworktree-dev-ci-gotchas]]).
- [ ] **Step 5: commit** `feat(cli): gflow character list/show/voices`.

### Task 6: BDD + Phase-1 acceptance

- [ ] Add `tests/features/character_read.feature` with the show-collision scenario from `character-scenario.md`; wire steps mirroring existing `tests/features/`.
- [ ] Run full scoped suite `.venv\Scripts\python.exe -m pytest tests/api tests/data tests/test_cli_character.py tests/features -q`; `pyright src`; both `ruff check` and `ruff format --check`.
- [ ] Commit `test(character): BDD read scenarios + phase-1 green`.

---

## Phase 2 — UI character generation + create saga (roadmap → own plan)

**Goal:** `gflow character create <name> --project P --face-prompt … [--body-prompt …] [--voice] [--personality]`.
- `client.generate_character_image(project_id, entity_id, req, image_reference_index)`: navigate the UI transport to `/{locale}/tools/flow/project/{pid}/character/{entityId}`, then reuse the existing prompt-submit + **passive-capture** path (NOT direct POST — 403-walled) so Flow's JS sets `entityContext`; capture `media[]`/`workflows[]`. **Assert `workflows[0].parentEntityId == entity_id`** before any PATCH (scenario #5).
- Slot-add structural selector for the body slot (no ligature — anchor by slot-row position; scenario #8).
- Create saga service fn `character_create(...)` in a new `services/`-style module (not in CLI): createEntity → gen face (slot 0) → optional gen body (slot 1, imageInputs REFERENCE=face) → `commit_workflow` primaryMediaId → `PATCH flow/entities` (displayName/personalityNotes/audioReferences). **Persist-before-spend** + recoverable partial saga (scenarios #3, #4); record rows via `OperationRecorder` with `kind=character`.
- Redaction: route `personalityNotes` through prompt-redaction; never store signed URLs (scenarios #15, #16).
- Acceptance: scenario must-covers #1,#3,#4,#5,#15,#16,#18,#21,#22.

## Phase 3 — Video reuse `--character` (roadmap → own plan)

**Goal:** `gflow video … --project P --character E1 [--character E2]` → `referenceEntities`.
- `--character` repeatable (`multiple=True`); validate ids → exit 11 before spend (scenario #14).
- UI-driven (reuse picker: Personagens tab + option + "Incluir no comando" structural anchor — scenario #8) OR the existing video transport with the entity attached; passive-capture `video:batchAsyncGenerateVideoReferenceImages`; reuse the in-tree poll loop (`concatenate_scene`/`_poll_video_status`, `CHECK_VIDEO_STATUS`) → factor `_poll_until`.
- Poll-timeout → exit 9 with entityId+workflowId in hint (scenario #19, no re-spend).
- Acceptance: #14, #19, #20, #25.

## Phase 4 — Full e2e DoD (the merge gate — expanded to its own plan)

**Definition of Done (hard, non-negotiable):** EVERY row in `docs/CHARACTER.md` §13 (and every Critical+High
in `character-scenario.md`) maps to a **named, passing test** of the correct category. No "done" without it.
No mocked-only coverage for credit/WAF/selector/recovery scenarios. This phase gets its own detailed plan;
the contract below is the gate.

**Infra tasks (do first):**
- Register marker `e2e_character` in `pyproject.toml` `[tool.pytest.ini_options] markers` (the repo enforces a `test_marker_registry` invariant — an unregistered marker fails CI). Confirm against the existing `e2e_image`/`e2e_video`/`e2e_data`/`e2e_scene`/`smoke` entries.
- Add opt-in env `GFLOW_CLI_E2E_RUN_CHARACTER` (none exists today); gate live char tests on it, default-off.
- Every live test uses the real-env opt-out (`monkeypatch.delenv("GFLOW_CLI_HOME"); reset_settings()` or the
  established fixture — [[test-isolation-real-env-opt-out]]) and is env-parameterized ([[e2e-tests-parameterize]]).

**Named live/integration tests (one per Critical/High; file `tests/e2e/test_character_e2e.py`):**

| Scenario | Test name | Marker | Live assertion (ledger) |
|---|---|---|---|
| #1/#5 gen via UI binds entity | `test_character_create_binds_parent_entity` | `e2e_image` | response `workflows[0].parentEntityId == entityId` AND `projectId == existing` (not new); read-back `imageReferences[0].workflowId` present |
| #3/#4 persist-before-spend recovery | `test_character_create_partial_saga_recoverable` | `e2e_data` | kill after gen; re-run; assert NO second `batchGenerateImages` fired (one credit) + entity recoverable |
| #2 403 → WAF (not auth) | `test_direct_post_gen_403_is_waf` | `e2e_image` | a self-POST gen path raises `WafRejectionError` (guards the regression; may run vs a recorded 403) |
| #11 list/show read-back | `test_character_list_show_live` | `e2e_data` | created character appears via `projectInitialData` with correct fields (proves the GET `?input=` encoding — currently UNVERIFIED) |
| #12 name collision | `test_character_show_ambiguous_exit_11` | `e2e_data` | two same-named chars → exit 11 + both ids |
| reuse #19 poll-timeout | `test_video_character_poll_timeout_exit_9` | `e2e_video` | exit 9 with entityId+workflowId in hint; no re-spend |
| reuse happy (multi-ref) | `test_video_two_characters_reference_entities` | `e2e_video` | request carries `referenceEntities:[{E1},{E2}]`; polled to done |
| #18 accented round-trip | `test_character_personality_utf8` | `e2e_data` | accented `--personality` persists & reads back intact (PYTHONUTF8) |

Medium/Low scenarios → integration/unit (no live), but each still gets a test; nothing silently dropped.
**Gate:** CI/`/gflow:check` green on the non-live tiers; the live tiers run on denon82 with the opt-in env
and ALL pass (verification ledger evidence recorded) before the PR is marked ready.

## Phase 5 — Polish (roadmap)

CHANGELOG, `docs/USAGE.md`/`USER_GUIDE.md`, `/gflow:doc-review`, `/gflow:branch-review`, PR to develop (credit @kittinan, `Refs #145`).

---

## Self-review (Phase 1 vs spec)

- **Spec coverage:** 403-fix (scn #2)=Task1; data/migration (#17)=Task2; parse/DTOs (#11,#12)=Task3; entity REST + auth (#6,#7,#11)=Task4; CLI exits 11 + voices (#12,#13,#14)=Task5; BDD=Task6. Generation/redaction/reuse/e2e → Phases 2–4 (correctly deferred — generation is credit/UI-bound). ✅ no Phase-1 gap.
- **Placeholder scan:** Phase 1 steps carry real code; the only "read the existing file to mirror" pointers (cli_scene.py, 0004 migration) are deliberate pattern-following in an existing codebase, not logic placeholders.
- **Type consistency:** `Character` fields (entity_id/display_name/workflow_ids/voice/personality) used identically in Task3 parser, Task4 client, Task5 CLI. `create_entity`/`list_characters`/`get_character` names stable across Tasks 4–5.
