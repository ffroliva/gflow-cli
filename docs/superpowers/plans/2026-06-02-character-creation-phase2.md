# Character Creation — Phase 2 Implementation Plan (UI char-gen + create saga)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> ⚠️ **WORKSPACE:** All work happens in the worktree `C:\development\github\gflow-cli\.claude\worktrees\character-creation` on branch `feature/character-creation`. Subagents spawn in the PRIMARY checkout — pin every subagent to the worktree abs path + `git -C "<worktree>"`. Trust the worktree venv `.venv\Scripts\python.exe -m pyright src` over IDE diagnostics ([[multiworktree-dev-ci-gotchas]]).
>
> ⚠️ **CREDIT/LIVE GATE:** Stage 0 spike **T-B** (~1 credit) and Task 12 (live e2e) require a headed Chrome-strategy profile on denon82 and spend real credits. They are **NOT autonomous** — the human runs them / authorizes the spend. Every other task is credit-free (unit/integration with mocked transport).

**Goal:** Ship `gflow character create <name> --project P --face-prompt … [--body-prompt …] [--voice] [--personality]` — a persist-before-spend create saga that generates a character's reference image(s) via Flow's UI (passive-capture, reCAPTCHA-walled) and binds them to a REST-created CHARACTER entity.

**Architecture:** Hybrid transport ([[rest-path-capability-matrix]]). `createEntity` (tRPC), `commit_workflow` + `patch_entity` (Bearer REST) are direct; **generation is UI-driven passive-capture** — navigate to the existing project's character editor (`/{locale}/tools/flow/project/{pid}/character/{entityId}`), submit the prompt through Flow's own JS so it sets `entityContext` and passes the WAF, and capture the `batchGenerateImages` response. Direct POST is **403-walled** (proven, spike v2). The saga is a service-layer orchestration with persist-before-spend + recoverable partial state.

**Tech Stack:** Python 3.13, Playwright (Chrome-strategy), Click, SQLite data layer (`OperationRecorder`), structlog, pytest + pytest-bdd. Full design: [`docs/CHARACTER.md`](../../CHARACTER.md) §4/§6/§11/§12; scenarios: [`docs/superpowers/character-scenario.md`](../character-scenario.md). Phase-1 plan: [`2026-06-02-character-creation.md`](2026-06-02-character-creation.md).

---

## Scope

**In:** the create saga (`createEntity → gen face → [gen body] → commit_workflow → patch_entity`), persist-before-spend recovery, `character create` CLI, redaction wiring, unit/integration coverage, and a live e2e of the happy saga.

**Out (→ Phase 3):** video `--character` reuse (`referenceEntities`, the resource picker, `GENERATE_VIDEO_REFERENCE_IMAGES`, Spike T-C). Keep Phase 2 a clean, independently-testable slice.

**Phase-2 acceptance scenarios** (from character-scenario.md, must-cover before this slice is "done"): **#1** (direct-REST gen blocked — code invariant), **#3** (saga fail after createEntity — recoverable), **#4** (credit spent then 403 on retry — no double-bill), **#5** (workflow missing `parentEntityId` — error, don't PATCH foreign workflow), **#15** (personalityNotes redaction), **#16** (signed-URL never persisted), **#18** (accented text round-trip on Windows), **#21** (headless profile → clear error), **#22** (sequential face+body, no parallel mint on one profile). Live behaviour of #1/#3/#4/#5 is fully proven only by Task 12 e2e ([[feature-dod-full-e2e]] — Phase 4 is the global merge gate; this slice's e2e is the down-payment).

---

## File structure (whole phase)

- **Modify** `src/gflow_cli/api/character.py` — add `face_media_id: str | None = None` to `CharacterImageRequest`; add a `CharacterCreateResult` frozen DTO (entity_id, project_id, workflow_ids, primary_media_ids, name, voice).
- **Modify** `src/gflow_cli/api/routes.py` — add `character_editor_url(locale, project_id, entity_id)`.
- **Modify** `src/gflow_cli/api/client.py` — add `patch_entity(...)` (PATCH flow/entities) and `generate_character_image(...)` (drives the UI transport, asserts `parentEntityId`); fix the stale `_raise_for_non_retryable` docstring (line ~1438).
- **Modify** `src/gflow_cli/api/transports/ui_automation.py` — add `_enter_character_editor(page, project_id, entity_id, locale)` and a `generate_character_images(...)` entry that reuses the existing listener/capture/parse chain but navigates to the character editor instead of `_enter_editor`, filters the listener on the **existing** project id, and surfaces `workflows[].parentEntityId`.
- **Modify** `src/gflow_cli/data/recorder.py` — add `record_character_started(...)` (STARTED + entityId) and `record_character_completed(...)` (SUCCEEDED + workflowIds/mediaIds; personality via `prompt_fields`; metadata via `redact_metadata`).
- **Create** `src/gflow_cli/services/character_create.py` — the saga orchestrator `character_create(client, recorder, *, project_id, name, face, body=None, voice=None, personality=None) -> CharacterCreateResult` (persist-before-spend, recoverable).
- **Modify** `src/gflow_cli/cli_character.py` — add the `create` command + `_run_create` coroutine.
- **Tests:** `tests/api/test_character.py` (DTO), `tests/api/test_routes_character.py`, `tests/api/test_client_patch_entity.py`, `tests/api/test_client_generate_character.py`, `tests/api/transports/test_ui_character_editor.py`, `tests/data/test_recorder_character.py`, `tests/services/test_character_create_saga.py`, `tests/cli/test_cli_character_create.py`, `tests/features/character_create.feature` (+steps), `tests/e2e/test_character_create_e2e.py` (Task 12).
- **Fixtures:** `tests/api/fixtures/character_gen_response.json` (sourced from Spike T-B), `tests/api/fixtures/patch_entity_response.json` (Spike T-D).

---

## Stage 0 — Live spikes (GATE: run before writing fixtures)

> These pin the **real** wire/DOM shapes so unit fixtures are not guessed. T-A/T-D are credit-free; **T-B spends ~1 credit and needs human authorization**. Save captured artifacts under `tests/api/fixtures/` (responses) and append DOM findings to `docs/CHARACTER.md §12`. **Do not write the Task-5 / Task-3 fixtures until T-B / T-D have run.** Until then, Tasks 1, 2, 7 (which have no live dependency) may proceed.

### Spike T-A — character-editor DOM (0 credits)
- [ ] On denon82, with a headed Chrome-strategy profile, REST-create a throwaway entity then navigate to `/{locale}/tools/flow/project/{pid}/character/{entityId}`. Run `scripts/dev/dump_character_selectors.js` (already committed) against the page.
- [ ] Capture: the "editor ready" wait anchor (a stable structural selector present only once the editor mounts), the prompt-box selector (confirm it equals `PROMPT_INPUT_SELECTORS[0]`), the submit button, and the **slot-add** `div[role=button]` (no ligature — record its structural position relative to the slot row). Record the slot container structure + max slot count.
- [ ] Append findings to `docs/CHARACTER.md §12` with confidence upgraded from ⚠️low to the real anchor. **Acceptance:** every selector Task 4 needs has a verified structural anchor.

### Spike T-B — passive-capture with `entityContext` (~1 CREDIT — human-authorized)
- [ ] Navigate to the character editor of an **existing** project's fresh entity, type a face prompt, submit via Flow's JS, and passive-capture the `aisandbox-pa…flowMedia:batchGenerateImages` response (reuse the existing `_attach_batch_response_listener` mechanism or a standalone dev script).
- [ ] **Assert `workflows[0].parentEntityId == entityId`** (the spike-v1-404 guard). Save the full response JSON to `tests/api/fixtures/character_gen_response.json` (redact any `fifeUrl`/signed query params to placeholder tokens before committing — never commit signed URLs). Record the captured `media[].name`, `workflows[].name`, `workflows[].metadata.primaryMediaId`.
- [ ] **Acceptance:** a real `character_gen_response.json` exists with `parentEntityId` present; Task 5's fixture is now real. (If `parentEntityId` is **absent** in the live capture, STOP — the whole Option-B binding assumption is wrong; escalate to re-design before any further Phase-2 code.)

### Spike T-D — `PATCH flow/entities` response body (0 credits)
- [ ] On a live run, capture the response body of `PATCH https://aisandbox-pa.googleapis.com/v1/flow/entities` (the call Task 3 implements). Save to `tests/api/fixtures/patch_entity_response.json` (redact signed URLs). **Acceptance:** the echoed `entityInfo` shape is recorded; Task 3's read-back assertion is real.

---

## Phase 2 — Tasks

### Task 1: `CharacterImageRequest.face_media_id` + `CharacterCreateResult` DTO

**Files:** Modify `src/gflow_cli/api/character.py`; Test `tests/api/test_character.py`.

No live dependency — proceed any time.

- [ ] **Step 1: failing test**
```python
def test_character_image_request_carries_face_media_id():
    from gflow_cli.api.character import CharacterImageRequest
    req = CharacterImageRequest(prompt="a knight", image_reference_index=1, face_media_id="m-face")
    assert req.face_media_id == "m-face"
    assert req.image_reference_index == 1

def test_character_create_result_fields():
    from gflow_cli.api.character import CharacterCreateResult
    r = CharacterCreateResult(entity_id="e1", project_id="p", workflow_ids=("w1",),
                              primary_media_ids=("m1",), name="Ana", voice="gacrux")
    assert r.entity_id == "e1" and r.workflow_ids == ("w1",)
```
- [ ] **Step 2: run → fails** — `.venv\Scripts\python.exe -m pytest tests/api/test_character.py -k "face_media_id or create_result" -v`
- [ ] **Step 3: implement** — add `face_media_id: str | None = None` as the last field of `CharacterImageRequest`; add:
```python
@dataclass(frozen=True)
class CharacterCreateResult:
    entity_id: str
    project_id: str
    workflow_ids: tuple[str, ...]
    primary_media_ids: tuple[str, ...]
    name: str
    voice: str | None = None
```
Export both via `__all__`.
- [ ] **Step 4: run → PASS**; `.venv\Scripts\python.exe -m pytest tests/api/test_character.py -q`; `pyright src`; ruff check + format --check.
- [ ] **Step 5: commit** `feat(api): CharacterImageRequest.face_media_id + CharacterCreateResult`.

### Task 2: `character_editor_url` route helper

**Files:** Modify `src/gflow_cli/api/routes.py`; Test `tests/api/test_routes_character.py`.

No live dependency.

- [ ] **Step 1: failing test**
```python
def test_character_editor_url():
    from gflow_cli.api import routes
    assert routes.character_editor_url("pt", "pid-1", "eid-1") == \
        "https://labs.google/pt/tools/flow/project/pid-1/character/eid-1"
```
(Confirm the real host/prefix against an existing labs.google URL helper in routes.py — match the exact base used by other UI URLs; adjust the expected string to the real base.)
- [ ] **Step 2: run → fails.**
- [ ] **Step 3: implement** `character_editor_url(locale: str, project_id: str, entity_id: str) -> str` mirroring the existing UI-URL builders in routes.py.
- [ ] **Step 4: run → PASS**; pyright src; ruff.
- [ ] **Step 5: commit** `feat(api): character_editor_url route helper`.

### Task 3: `client.patch_entity()` (PATCH flow/entities)

**Files:** Modify `src/gflow_cli/api/client.py`; Test `tests/api/test_client_patch_entity.py`. **Fixture from Spike T-D.**

Covers Step 3 of the saga. Reuses `_patch_json` (`client.py:599`).

- [ ] **Step 1: failing test** (mock `_patch_json`): assert `patch_entity` PATCHes `routes.FLOW_ENTITIES_URL` with body `{"entity": {...}, "updateMask": "..."}` where the entity carries `displayName`, `characterInfo.personalityNotes`, `characterInfo.audioReferences=[{presetVoiceId: voice}]`, `characterInfo.imageReferences=[{workflowId: w} for w in workflow_ids]`, and the `updateMask` lists exactly the provided fields. Assert read-back parses the T-D fixture without error.
```python
async def test_patch_entity_builds_update_mask_for_provided_fields(...):
    captured = {}
    async def fake_patch(url, body, *, route_name=None):
        captured["url"], captured["body"] = url, body
        return json.loads((FIXTURES / "patch_entity_response.json").read_text())
    client._patch_json = fake_patch  # type: ignore[assignment]
    await client.patch_entity(project_id="p", entity_id="e", display_name="Ana",
                              workflow_ids=["w1"], voice="gacrux", personality="brave")
    assert captured["url"] == routes.FLOW_ENTITIES_URL
    body = captured["body"]
    assert body["entity"]["entityInfo"]["displayName"] == "Ana"
    assert body["entity"]["entityInfo"]["characterInfo"]["imageReferences"] == [{"workflowId": "w1"}]
    assert "entityInfo.displayName" in body["updateMask"]
    assert "entityInfo.characterInfo.imageReferences" in body["updateMask"]
```
Add a test that omitting `voice`/`personality` omits them from both the body and the updateMask.
- [ ] **Step 2: run → fails.**
- [ ] **Step 3: implement** `patch_entity(self, *, project_id, entity_id, display_name, workflow_ids, voice=None, personality=None) -> None`. Build `entityInfo` + `characterInfo` only with provided fields; assemble `updateMask` from the same set (comma-joined `entityInfo.…` paths). POST via `await self._patch_json(routes.FLOW_ENTITIES_URL, body, route_name="patchEntity")`.
- [ ] **Step 4: run → PASS**; `pytest tests/api -q`; pyright src; ruff.
- [ ] **Step 5: commit** `feat(api): patch_entity (PATCH flow/entities)`.

### Task 4: `ui_automation` — character-editor navigation + capture entry

**Files:** Modify `src/gflow_cli/api/transports/ui_automation.py`; Test `tests/api/transports/test_ui_character_editor.py`. **Selectors from Spike T-A.**

Reuses `_attach_batch_response_listener` (1390), `_await_captured` (1473), `_images_from_responses` (455), `_send_prompt` (866), `_configure_generation_settings`.

- [ ] **Step 1: failing test** — with a fake Playwright `page` (mirror the existing transport unit-test doubles in `tests/api/transports/`), assert `_enter_character_editor(page, project_id="p", entity_id="e", locale="pt")` calls `page.goto(routes.character_editor_url("pt","p","e"))` and waits on the **T-A editor-ready anchor**, and that `generate_character_images` attaches the listener filtered on `project_id="p"` (the existing project, NOT a new one), submits the prompt, awaits capture, and returns the parsed `(images, workflows)` including each workflow's `parentEntityId`.
- [ ] **Step 2: run → fails.**
- [ ] **Step 3: implement**
  - `async def _enter_character_editor(self, page, *, project_id, entity_id, locale)`: `await page.goto(character_editor_url(...))`; `await self._dismiss_blocking_overlays(page)`; wait for the T-A ready anchor.
  - Extend the response-parse path so captured `workflows[]` retain `parentEntityId` (add it to whatever struct `_images_from_responses`/the workflow extraction returns; if that helper currently drops workflow metadata, add a sibling `_workflows_from_responses(responses) -> list[dict]` returning `name`, `metadata.primaryMediaId`, `parentEntityId`).
  - `async def generate_character_images(self, *, project_id, entity_id, request, image_reference_index, locale, slot_inputs=None) -> tuple[list[GeneratedImage], list[dict]]`: lock; `_enter_character_editor`; `_configure_generation_settings(request)`; if `image_reference_index >= 1` perform the **slot-add** interaction (T-A structural anchor) and attach the face as a reference input; `_attach_batch_response_listener(page, project_id=project_id)`; `_send_prompt`; `_await_captured`; parse; return images + workflows.
- [ ] **Step 4: run → PASS**; pyright src; ruff. (No live browser in unit tests — fully faked page.)
- [ ] **Step 5: commit** `feat(ui): character-editor navigation + passive-capture entry`.

### Task 5: `client.generate_character_image()` + `parentEntityId` assertion

**Files:** Modify `src/gflow_cli/api/client.py`; Test `tests/api/test_client_generate_character.py`. **Fixture from Spike T-B.**

Covers Step 1 of the saga + scenarios **#1** (routes through UI transport) and **#5** (`parentEntityId` mismatch → error).

- [ ] **Step 1: failing tests** (mock the UI transport with the T-B fixture):
  - happy: `generate_character_image(project_id="p", entity_id="e", req=…, image_reference_index=0)` returns `(workflow_id, media_id)` taken from the captured `workflows[0]`/`primaryMediaId`, and the transport was invoked (proving it did NOT self-POST — scenario #1).
  - mismatch: when the captured `workflows[0].parentEntityId != "e"`, it raises `WireFormatError` (or a dedicated `EntityBindingError`) and does NOT return — scenario #5.
  - headless: when the resolved profile is non-Chrome-strategy/headless, it raises a clear `ConfigurationError` BEFORE navigating — scenario #21. (Mirror how existing gen methods guard the profile — [[real-browser-auth-mandatory]].)
```python
async def test_generate_character_image_asserts_parent_entity(...):
    fixture = json.loads((FIXTURES / "character_gen_response.json").read_text())
    transport.generate_character_images = make_fake(returns_from=fixture)  # parentEntityId == "e"
    wf, media = await client.generate_character_image(project_id="p", entity_id="e",
                                                      req=CharacterImageRequest(prompt="x"),
                                                      image_reference_index=0)
    assert wf == fixture["workflows"][0]["name"]
    assert transport.generate_character_images.called

async def test_generate_character_image_rejects_foreign_workflow(...):
    # fixture mutated so parentEntityId == "OTHER"
    with pytest.raises(WireFormatError):
        await client.generate_character_image(project_id="p", entity_id="e", req=..., image_reference_index=0)
```
- [ ] **Step 2: run → fails.**
- [ ] **Step 3: implement** `generate_character_image(self, *, project_id, entity_id, req, image_reference_index) -> tuple[str, str]`: resolve locale; guard the profile (headed Chrome-strategy) else `ConfigurationError`; call `self._transport.generate_character_images(...)`; take `workflows[0]`; **if `workflows[0].get("parentEntityId") != entity_id` → raise `WireFormatError`** (never commit/PATCH a foreign workflow); return `(workflow_name, primary_media_id)`.
- [ ] **Step 4: run → PASS**; pytest tests/api -q; pyright src; ruff. Also **fix the stale docstring** at `client.py:~1438` (`401/403 → AuthExpiredError`) to reflect 403→WafRejectionError.
- [ ] **Step 5: commit** `feat(api): generate_character_image with parentEntityId binding guard`.

### Task 6: `recorder.record_character_*` (persist-before-spend)

**Files:** Modify `src/gflow_cli/data/recorder.py`; Test `tests/data/test_recorder_character.py`.

Covers persist-before-spend recovery (**#3/#4**) + redaction (**#15/#16**). Pattern: `record_started_video` (recorder.py:367).

- [ ] **Step 1: failing tests** (real temp DB via the data-layer test fixtures, `_isolate_settings`):
  - `record_character_started(profile_name, profile_dir, project, *, entity_id, name) -> str` inserts an `OperationRecord(mode=CHARACTER, status=STARTED)` carrying `entity_id` in metadata, returns the row id; nothing about credits yet.
  - `record_character_completed(*, row_id, workflow_ids, primary_media_ids, voice, personality, media_metadata)` updates status→SUCCEEDED, stores workflow/media ids, routes `personality` through `prompt_fields(personality, mode=self.prompt_mode)` (redacted mode → no plaintext, hash set — scenario #15), and routes `media_metadata` through `redact_metadata` so **no `signature=`/`Expires=`/`fifeUrl`** is stored (scenario #16).
  - Assert (redacted mode): query the DB and confirm the personality plaintext is absent; assert no stored JSON contains `signature=`.
- [ ] **Step 2: run → fails.**
- [ ] **Step 3: implement** both methods following `record_started_video`/`record_completed_video`. Reuse `prompt_fields` (redaction.py:19) and `redact_metadata` (redaction.py:28).
- [ ] **Step 4: run → PASS**; `pytest tests/data -q` (migrations + ordering invariants stay green); pyright src; ruff.
- [ ] **Step 5: commit** `feat(data): record_character_started/completed (persist-before-spend + redaction)`.

### Task 7: create saga service (`services/character_create.py`)

**Files:** Create `src/gflow_cli/services/character_create.py` (+ `services/__init__.py` if absent); Test `tests/services/test_character_create_saga.py`.

The orchestration core. Covers **#3** (resume after createEntity), **#4** (no double-bill on retry), **#22** (sequential face+body), **#5** (binding guard propagates).

- [ ] **Step 1: failing tests** (mock `client` + `recorder`; assert ordering and recovery, not live):
  - happy path (face only): calls in order `create_entity` → `record_character_started` → `generate_character_image(idx=0)` → `commit_workflow` → `patch_entity` → `record_character_completed`; returns `CharacterCreateResult` with the workflow/media ids.
  - face+body: after face, calls `generate_character_image(idx=1, …face_media_id set…)` then commits both; asserts the two gens are **sequential** (not gathered) — scenario #22.
  - **recovery (#3/#4):** given a recorder that reports an existing STARTED row with an `entity_id` and a recorded `workflow_id` for the same `(project, name)`, the saga **reuses** the entity/workflow and does **not** call `generate_character_image` again (no second credit); it resumes at the commit/PATCH step. Assert `generate_character_image` call-count == 0 on resume.
  - binding error (#5): if `generate_character_image` raises `WireFormatError`, the saga records FAILED (entity+nothing committed), does NOT PATCH, and re-raises.
- [ ] **Step 2: run → fails.**
- [ ] **Step 3: implement** `async def character_create(client, recorder, *, project_id, name, face, body=None, voice=None, personality=None) -> CharacterCreateResult`:
  1. **Resume check:** ask the recorder for an incomplete CHARACTER op matching `(project_id, name)`. If found with an `entity_id`, reuse it (and any recorded `workflow_id`s) instead of re-creating/re-generating.
  2. Else `entity_id = await client.create_entity(project_id)`; `row_id = recorder.record_character_started(...)` **before** any gen.
  3. If no face workflow recorded yet: `wf0, m0 = await client.generate_character_image(project_id, entity_id, face, image_reference_index=0)`; `await client.commit_workflow(wf0, project_id=project_id, primary_media_id=m0)`. Persist `wf0`/`m0` to the row immediately (so a crash before PATCH is recoverable).
  4. If `body` provided and no body workflow recorded: build a `CharacterImageRequest` with `image_reference_index=1, face_media_id=m0`; `wf1, m1 = await client.generate_character_image(... idx=1)`; commit. (Sequential — never parallelize on one profile.)
  5. `await client.patch_entity(project_id=project_id, entity_id=entity_id, display_name=name, workflow_ids=[wf0, *([wf1] if body else [])], voice=voice, personality=personality)`.
  6. `recorder.record_character_completed(row_id=row_id, workflow_ids=…, primary_media_ids=…, voice=voice, personality=personality, media_metadata=…)`; return `CharacterCreateResult(...)`.
  On any exception after step 2: record FAILED (preserving entity/workflow ids) and re-raise — the partial state is recoverable on the next run ([[persist-ephemeral-render-for-recovery]]).
- [ ] **Step 4: run → PASS**; pyright src; ruff.
- [ ] **Step 5: commit** `feat(services): character create saga (persist-before-spend, recoverable)`.

### Task 8: CLI `gflow character create`

**Files:** Modify `src/gflow_cli/cli_character.py`; Test `tests/cli/test_cli_character_create.py`.

Covers **#18** (accented `--personality`/`--face-prompt` round-trip), **#21** (headless → exit error), language-agnostic output.

- [ ] **Step 1: failing tests** (CliRunner, mock the saga service / client):
  - `character create --project P --name X --face-prompt "..."` invokes the saga and prints the resulting entity id + workflow ids; `--json` emits `{"status":"ok","character":{...}}` via `json_output.emit`.
  - accented `--personality "Café à Façade"` reaches the saga intact (assert the value passed through) — scenario #18; document `PYTHONUTF8=1` on Windows.
  - headless/non-chrome profile → the `ConfigurationError` from Task 5 maps to a non-zero exit (assert exit code matches the profile-error mapping; mirror existing gen-command profile errors) — scenario #21.
- [ ] **Step 2: run → fails.**
- [ ] **Step 3: implement** the `create` command per the Part-B8 skeleton (`--project/--name/--face-prompt` required; `--body-prompt/--voice/--personality/--aspect/--model/--profile/--json` optional). `_run_create` opens `async with FlowApiClient(...)`, opens an `OperationRecorder`, builds a face `CharacterImageRequest`, and calls `character_create(...)`. Wrap in `run_with_handlers(lambda: _run_create(...), cli_command="character create", as_json=as_json)`.
- [ ] **Step 4: run → PASS**; `pytest tests/cli -q`; pyright src; ruff check + format --check.
- [ ] **Step 5: commit** `feat(cli): gflow character create`.

### Task 9: redaction + observability hardening

**Files:** Touch `services/character_create.py`, `recorder.py` as needed; Test `tests/services/test_character_create_redaction.py`.

Tighten **#15/#16** end-to-end and add structlog events.

- [ ] **Step 1: failing test** — run the saga (mocked client) in `prompt_mode="redacted"` with a `personality` containing PII and a captured media metadata containing a signed `fifeUrl`; after completion, query the temp DB and assert: (a) personality plaintext ABSENT, hash present; (b) no stored value contains `signature=`/`Expires=`/`fifeUrl`. Assert structlog emitted `character.create_started` (with entity_id) and `character.create_completed` (with workflow count) — no prompt/personality plaintext in the log event.
- [ ] **Step 2: run → fails** (if any leak).
- [ ] **Step 3: implement** — ensure every personality write goes through `prompt_fields`; every media/entity metadata write through `redact_metadata`; add the two `logger.info` events (cheap, but these ARE credit-spending ops so info-level is appropriate, mirroring video gen). Redact prompt/personality in logs.
- [ ] **Step 4: run → PASS**; pyright src; ruff.
- [ ] **Step 5: commit** `feat(character): end-to-end redaction + create observability`.

### Task 10: BDD + Phase-2 acceptance sweep

**Files:** Create `tests/features/character_create.feature` + steps; Test sweep.

- [ ] Add BDD scenarios mirroring `tests/features/character_read.feature` harness: (a) happy create prints entity+workflow ids; (b) **resume after partial saga** does not re-spend (mock recorder returns an incomplete row → assert no second gen) — scenario #3/#4; (c) **foreign-workflow** gen → error, no PATCH — scenario #5. All against mocked client/recorder (no live).
- [ ] Add an explicit **guardrail test for scenario #1**: assert there is NO code path in `services/character_create.py` / `client.generate_character_image` that calls `batch_generate_images_url` / `_post_json` directly for character gen (i.e. character gen always goes through the UI transport). A `grep`-style structural assertion or a test that fails if `generate_character_image` ever invokes `_post_json`.
- [ ] Run the full Phase-2 scoped sweep: `.venv\Scripts\python.exe -m pytest tests/api tests/data tests/services tests/cli tests/features -q`; `pyright src` (0); `ruff check` + `ruff format --check`.
- [ ] **Commit** `test(character): BDD create scenarios + phase-2 green`.

### Task 11: docs + scenario back-fill

**Files:** Modify `docs/CHARACTER.md` (mark §11/§12 selectors live-verified from spikes; document the create CLI), `docs/INDEX.md` if needed; update `docs/superpowers/character-scenario.md` status column for the now-covered scenarios.

- [ ] Update CHARACTER.md with the verified selectors/wire shapes from Stage 0, the `gflow character create` CLI surface, and a sequence diagram for the saga. Mark Phase-2 scenarios as covered (with test names). Enforce language-agnostic (no localized strings) ([[docs-first-class-living-spec]]).
- [ ] **Commit** `docs(character): create saga + verified selectors/protocol`.

### Task 12: LIVE e2e (denon82 — CREDIT GATE, human-run)

**Files:** Create `tests/e2e/test_character_create_e2e.py`.

> The down-payment on the [[feature-dod-full-e2e]] gate (full DoD is Phase 4). **Not autonomous — runs on denon82 with the opt-in env, spends ~1–2 credits.**

- [ ] Register marker `e2e_character` in `pyproject.toml [tool.pytest.ini_options] markers` (the repo enforces a `test_marker_registry` invariant — unregistered marker fails CI). Add opt-in env `GFLOW_CLI_E2E_RUN_CHARACTER` (default-off). Every live test uses the real-env opt-out ([[test-isolation-real-env-opt-out]]) + is env-parameterized ([[e2e-tests-parameterize]]).
- [ ] `test_character_create_binds_parent_entity` (`@e2e_character`/`e2e_image`): live `gflow character create … --face-prompt …` on an existing project → assert the captured `workflows[0].parentEntityId == entityId` AND `projectId == existing` (not a new project); read back via `projectInitialData` and assert `imageReferences[0].workflowId` present. Verification ledger evidence recorded ([[verification-ledger-5-layer]]).
- [ ] `test_character_create_partial_saga_recoverable` (`@e2e_data`): kill after the gen step; re-run; assert NO second `batchGenerateImages` fired (one credit) + entity recoverable — scenarios #3/#4.
- [ ] `test_character_personality_utf8` (`@e2e_data`): accented `--personality` persists & reads back intact (PYTHONUTF8) — scenario #18.
- [ ] **Gate:** CI/`/gflow:check` green on non-live tiers; the live tiers pass on denon82 with the opt-in env, evidence recorded, before this slice is called done.
- [ ] **Commit** `test(character): live e2e — create saga binds entity, recovers, utf8`.

---

## Self-review (Phase 2 vs spec)

- **Scenario coverage:** #1=Task5+Task10 guardrail; #3/#4=Task7 recovery + Task12 live; #5=Task5 binding guard + Task7/Task10; #15=Task6/Task9; #16=Task6/Task9; #18=Task8/Task12; #21=Task5/Task8; #22=Task7 sequential. ✅ no Phase-2 must-cover gap (video reuse #14/#19/#20/#25 correctly deferred → Phase 3).
- **Live-fixture honesty:** Tasks 3 and 5 depend on Spike T-D / T-B captures; the plan gates them explicitly. Writing those fixtures before the spikes = the synthetic-fixture trap ([[e2e-exposes-synthetic-fixture-bugs]]) — forbidden here.
- **Type consistency:** `CharacterImageRequest(prompt, aspect, model, image_reference_index, face_media_id)`, `CharacterCreateResult(entity_id, project_id, workflow_ids, primary_media_ids, name, voice)`, `generate_character_image(...) -> (workflow_id, media_id)`, `patch_entity(*, project_id, entity_id, display_name, workflow_ids, voice, personality)`, `character_create(...) -> CharacterCreateResult` — names stable across Tasks 1/3/5/7/8.
- **Reuse:** `commit_workflow`, `_patch_json`, `_attach_batch_response_listener`, `_await_captured`, `_send_prompt`, `prompt_fields`, `redact_metadata`, `run_with_handlers`, `json_output.emit` are reused, not reinvented. `_poll_until` deferred to Phase 3 (no poll in the synchronous capture path).
- **Placeholder scan:** spike-dependent values are real data dependencies with a named source file, not "TODO" — acceptable. No logic placeholders.
