# Movie P2 — Native Identity (`referenceEntities`) + Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `gflow movie` lock character identity (and voice) using Flow's native `referenceEntities` + `referenceAudio` instead of re-uploaded images — the MVP "the character stops diverging" gate.

**Architecture:** Extend `GenerateVideoRequest` with `reference_entities` and `reference_audio`; relax R2V validation and add per-model reference-cap budgeting. Add a `ui_automation` transport step `_attach_character_entities` that drives the spike-captured resource picker (Personagens → "Incluir no comando"; Vozes for voice). A submit-time backstop asserts `referenceEntities` actually rode the wire (else mark the clip `degraded` and fail loudly). The orchestrator resolves `identity="entity"` characters to their `entity_id` and voice, sets `consistency_method`, and the handoff records it. Character creation learns to embed a voice (preferred path).

**Tech Stack:** Python 3.13, Playwright (UI automation), pytest. Tests: `.venv/Scripts/python.exe -m pytest`. Live e2e on a chrome-strategy profile (`denon82`).

**Spike-verified wire (2026-06-06):** `requests[].referenceEntities:[{entityId}]`, `requests[].referenceAudio:[{mediaId}]` (e.g. `"alnilam"`), `mediaGenerationContext.audioFailurePreference:"BLOCK_SILENCED_VIDEOS"`, model `veo_3_1_r2v_lite`, endpoint `video:batchAsyncGenerateVideoReferenceImages`. Picker selectors: `ADD_MEDIA_BUTTON`; Personagens tab = `accessibility_new` ligature; search `#add-menu-input`; **"Incluir no comando"** button; Vozes tab = `voice_selection` ligature.

**Depends on:** P1 merged.

---

## File Structure

- Modify: `src/gflow_cli/api/video.py` — DTO `reference_entities`/`reference_audio` + validation + cap budgeting.
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py` — `_attach_character_entities`, `_attach_reference_audio`, submit-body fields, backstop.
- Modify: `src/gflow_cli/composition.py` — `consistency_method` plumbing in `build_handoff`.
- Modify: `src/gflow_cli/cli_movie.py` — resolve `identity="entity"` → `reference_entities` + voice; set `consistency_method`; pre-flight entity existence.
- Modify: `src/gflow_cli/services/character_create.py` + `api/client.py` — embed voice at creation (PATCH `audioReferences.presetVoiceId`).
- Create: `scripts/dev/spike_movie_voice_list.py` — enumerate Vozes voice ids (credit-free recon).
- Test: `tests/api/test_video_request.py`, `tests/api/transports/test_ui_automation_video.py`, `tests/cli/test_cli_movie.py`.

---

### Task 1: DTO — `reference_entities` + `reference_audio` + relaxed R2V + cap budgeting

**Files:**
- Modify: `src/gflow_cli/api/video.py` (`GenerateVideoRequest` fields ~198-200; `__post_init__` ~238-268)
- Test: `tests/api/test_video_request.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_video_request.py` (create if absent):

```python
import pytest

from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel


def test_r2v_valid_with_entities_only() -> None:
    req = GenerateVideoRequest(
        prompt="x", mode=Mode.R2V, aspect=Aspect.LANDSCAPE,
        model=VideoModel.VEO_3_1_LITE, reference_entities=("ent-1",),
    )
    assert req.reference_entities == ("ent-1",)


def test_r2v_valid_with_audio() -> None:
    req = GenerateVideoRequest(
        prompt="x", mode=Mode.R2V, aspect=Aspect.LANDSCAPE,
        model=VideoModel.VEO_3_1_LITE, reference_entities=("ent-1",),
        reference_audio="alnilam",
    )
    assert req.reference_audio == "alnilam"


def test_r2v_requires_images_or_entities() -> None:
    with pytest.raises(ValueError, match="reference_images or reference_entities"):
        GenerateVideoRequest(prompt="x", mode=Mode.R2V, aspect=Aspect.LANDSCAPE,
                             model=VideoModel.VEO_3_1_LITE)


def test_cap_budget_counts_entities_plus_images() -> None:
    # veo_3_1 cap = 3
    with pytest.raises(ValueError, match="reference cap"):
        GenerateVideoRequest(
            prompt="x", mode=Mode.R2V, aspect=Aspect.LANDSCAPE,
            model=VideoModel.VEO_3_1_LITE,
            reference_entities=("a", "b"),
            reference_images=(__import__("pathlib").Path("x.png"),) * 2,
        )
```

(Use the actual `Aspect`/`Mode`/`VideoModel` enum members present in `api/video.py`; adjust `LANDSCAPE`/`VEO_3_1_LITE` names if the source uses different identifiers — check the enum definitions first.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_video_request.py -v`
Expected: FAIL — `TypeError: unexpected keyword 'reference_entities'`.

- [ ] **Step 3: Add the fields + validation**

In `src/gflow_cli/api/video.py`, add fields to `GenerateVideoRequest` (next to `reference_images`):

```python
    reference_entities: tuple[str, ...] = ()   # R2V — Flow CHARACTER entity ids
    reference_audio: str | None = None         # R2V — voice resource mediaId (e.g. "alnilam")
```

In `__post_init__`, update the R2V branch. Replace the existing `if not self.reference_images:` requirement with:

```python
        if self.mode is Mode.R2V:
            if not self.reference_images and not self.reference_entities:
                msg = "R2V request requires reference_images or reference_entities"
                raise ValueError(msg)
            if self.start_image or self.end_image:
                msg = "R2V request must not carry start/end frames"
                raise ValueError(msg)
            # Per-model reference cap counts BOTH images and entities.
            total_refs = len(self.reference_images) + len(self.reference_entities)
            if self.model is not None:
                cap = reference_cap_for(self.model)
                if total_refs > cap:
                    msg = (
                        f"reference cap exceeded: {total_refs} refs "
                        f"(images+entities) > {cap} for {self.model.value}"
                    )
                    raise ValueError(msg)
```

Also extend the I2V guard so entities are rejected there too (mirror the `reference_images` rejection): in the I2V branch add `or self.reference_entities` to the "must not carry references" check.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_video_request.py -v`
Expected: PASS (4 tests). Also run the existing video DTO tests: `.venv/Scripts/python.exe -m pytest tests/api -k video -q` — fix any that assumed R2V always needs images.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/video.py tests/api/test_video_request.py
git commit -m "feat(video): reference_entities + reference_audio DTO fields + cap budgeting"
```

---

### Task 2: Transport — `_attach_character_entities` + `_attach_reference_audio`

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`
- Test: `tests/api/transports/test_ui_automation_video.py`

These drive the spike-captured picker on the **already-checked-out composer page** (never check out a 2nd page — size-1 pool deadlock). Both run in `references` sub-mode, settings panel closed, mirroring `_attach_references`.

- [ ] **Step 1: Write the failing test (selector contract, mocked page)**

Add to `tests/api/transports/test_ui_automation_video.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin


@pytest.mark.asyncio
async def test_attach_character_entities_uses_personagens_and_include() -> None:
    page = MagicMock()
    # locator(...).first.click() chain
    loc = MagicMock()
    loc.first = loc
    loc.click = AsyncMock()
    loc.wait_for = AsyncMock()
    loc.fill = AsyncMock()
    page.locator.return_value = loc
    page.wait_for_timeout = AsyncMock()
    page.keyboard = MagicMock(press=AsyncMock())

    await VideoGenerationMixin._attach_character_entities(page, ["Stickman"], out_dir=None)

    # It must have opened the picker, hit the Personagens tab (accessibility_new),
    # searched, and clicked "Incluir no comando".
    selectors = " ".join(str(c.args[0]) for c in page.locator.call_args_list)
    assert "accessibility_new" in selectors
    assert "add-menu-input" in selectors
    assert "Incluir no comando" in selectors
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/api/transports/test_ui_automation_video.py -k attach_character_entities -v`
Expected: FAIL — `AttributeError: ... has no attribute '_attach_character_entities'`.

- [ ] **Step 3: Implement the attach helpers**

In `src/gflow_cli/api/transports/ui_automation_video.py`, add module-level selector constants near `ADD_MEDIA_BUTTON`:

```python
# Resource picker (spike-verified 2026-06-06, locale-agnostic via ligatures/id).
PICKER_SEARCH_INPUT = "#add-menu-input"
PICKER_PERSONAGENS_TAB = "[role='tab']:has(i.google-symbols:text-is('accessibility_new')), button:has(i.google-symbols:text-is('accessibility_new'))"
PICKER_VOZES_TAB = "[role='tab']:has(i.google-symbols:text-is('voice_selection')), button:has(i.google-symbols:text-is('voice_selection'))"
PICKER_INCLUDE_BUTTON = "button:has-text('Incluir no comando')"
```

Add these methods to `VideoGenerationMixin`:

```python
    @staticmethod
    async def _attach_character_entities(
        page: "Page", names: list[str], *, out_dir: "Path | None"
    ) -> None:
        """R2V: attach each named character via the resource picker
        (Personagens -> select -> 'Incluir no comando'), injecting referenceEntities.

        Runs on the open composer page in references sub-mode. Disambiguation note:
        the picker lists characters by name+thumbnail, not entityId — selection is
        by display name; the submit-time backstop verifies the right entity rode.
        """
        for name in names:
            add = page.locator(ADD_MEDIA_BUTTON).first
            await add.wait_for(state="visible", timeout=8000)
            await add.click()
            await page.wait_for_timeout(800)
            await page.locator(PICKER_PERSONAGENS_TAB).first.click()
            await page.wait_for_timeout(400)
            search = page.locator(PICKER_SEARCH_INPUT).first
            await search.fill(name)
            await page.wait_for_timeout(600)
            # Select the first matching character tile by visible name.
            tile = page.locator(f"button:has-text('{name}'), [role='option']:has-text('{name}')").first
            await tile.click()
            await page.wait_for_timeout(300)
            await page.locator(PICKER_INCLUDE_BUTTON).first.click()
            await page.wait_for_timeout(600)
            log.info("ui_automation_video.character_entity_attached", name=name)

    @staticmethod
    async def _attach_reference_audio(
        page: "Page", voice_id: str, *, out_dir: "Path | None"
    ) -> None:
        """R2V: attach a voice resource via the Vozes picker -> 'Incluir no comando'."""
        add = page.locator(ADD_MEDIA_BUTTON).first
        await add.wait_for(state="visible", timeout=8000)
        await add.click()
        await page.wait_for_timeout(800)
        await page.locator(PICKER_VOZES_TAB).first.click()
        await page.wait_for_timeout(400)
        search = page.locator(PICKER_SEARCH_INPUT).first
        await search.fill(voice_id)
        await page.wait_for_timeout(600)
        tile = page.locator(f"button:has-text('{voice_id}'), [role='option']:has-text('{voice_id}')").first
        await tile.click()
        await page.wait_for_timeout(300)
        await page.locator(PICKER_INCLUDE_BUTTON).first.click()
        await page.wait_for_timeout(600)
        log.info("ui_automation_video.reference_audio_attached", voice=voice_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/api/transports/test_ui_automation_video.py -k attach_character_entities -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/transports/ui_automation_video.py tests/api/transports/test_ui_automation_video.py
git commit -m "feat(transport): attach character entities + voice via resource picker"
```

---

### Task 3: Wire entities/audio into the locked generate flow + backstop

**Files:**
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py` (`_generate_video_locked` R2V branch ~1406; backstop after `_await_generate_response` ~1425)
- Test: `tests/api/transports/test_ui_automation_video.py`

- [ ] **Step 1: Write the failing test (backstop)**

Add:

```python
def test_backstop_raises_when_entity_missing_from_payload() -> None:
    from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
    from gflow_cli.errors import WireFormatError
    captured = {"url": "video:batchAsyncGenerateVideoReferenceImages", "status": 200,
                "body": {"requests": [{"referenceImages": [{"mediaId": "x"}]}]}}  # no referenceEntities!
    with pytest.raises(WireFormatError, match="referenceEntities"):
        VideoGenerationMixin._assert_entities_attached(captured, expected=["ent-1"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/api/transports/test_ui_automation_video.py -k backstop -v`
Expected: FAIL — no `_assert_entities_attached`.

- [ ] **Step 3: Implement backstop + branch wiring**

Add the backstop static method:

```python
    @staticmethod
    def _assert_entities_attached(generate_resp: dict, *, expected: list[str]) -> None:
        """Defense-in-depth: confirm referenceEntities actually rode the wire.

        REST transports silently drop unknown DTO fields; a UI miss would degrade
        to a text/image-only clip reported as success. Raise loudly instead.
        """
        if not expected:
            return
        body = generate_resp.get("body") or {}
        reqs = body.get("requests") or []
        got = []
        for r in reqs if isinstance(reqs, list) else []:
            for e in (r.get("referenceEntities") or []):
                if isinstance(e, dict) and e.get("entityId"):
                    got.append(e["entityId"])
        missing = [e for e in expected if e not in got]
        if missing:
            raise WireFormatError(
                detail=(
                    f"referenceEntities not in submit payload (expected {expected}, "
                    f"got {got}); entity attach failed — refusing to report success"
                ),
                route="video:batchAsyncGenerateVideoReferenceImages",
            )
```

In `_generate_video_locked`, in the `elif request.mode is Mode.R2V:` block, attach entities/images/audio:

```python
        elif request.mode is Mode.R2V:
            if request.reference_entities:
                await VideoGenerationMixin._attach_character_entities(
                    page, list(request.reference_entities), out_dir=out_dir
                )
            if request.reference_images:
                await VideoGenerationMixin._attach_references(
                    page, list(request.reference_images), out_dir=out_dir
                )
            if request.reference_audio:
                await VideoGenerationMixin._attach_reference_audio(
                    page, request.reference_audio, out_dir=out_dir
                )
```

After `generate_resp = await VideoGenerationMixin._await_generate_response(generate_captured)` (the existing line), add:

```python
            if request.reference_entities:
                VideoGenerationMixin._assert_entities_attached(
                    generate_resp, expected=list(request.reference_entities)
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/api/transports/test_ui_automation_video.py -k backstop -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/transports/ui_automation_video.py tests/api/transports/test_ui_automation_video.py
git commit -m "feat(transport): wire R2V entities/audio + submit-time entity backstop"
```

---

### Task 4: Embed voice at character creation (preferred path)

**Files:**
- Modify: `src/gflow_cli/api/client.py` (entity PATCH; mirror `spike_patch_entity.py` body shape)
- Modify: `src/gflow_cli/services/character_create.py` (accept + persist voice)
- Modify: `src/gflow_cli/composition.py` (`Character.voice` already exists — no change)
- Test: `tests/api/test_client_character.py` (or the existing client-character test module)

- [ ] **Step 1: Write the failing test**

Add a test asserting that when a voice is supplied, the create flow issues a PATCH with `entityInfo.characterInfo.audioReferences` carrying the `presetVoiceId`:

```python
@pytest.mark.asyncio
async def test_character_create_embeds_voice() -> None:
    from unittest.mock import AsyncMock
    from gflow_cli.api.client import FlowApiClient
    client = FlowApiClient.__new__(FlowApiClient)  # bypass __init__ for unit isolation
    client._patch_json = AsyncMock(return_value={})  # type: ignore[attr-defined]
    await client.set_character_voice(project_id="p", entity_id="e", preset_voice_id="alnilam")
    body = client._patch_json.call_args.args[1]
    assert body["entity"]["entityInfo"]["characterInfo"]["audioReferences"][0]["presetVoiceId"] == "alnilam"
    assert "audioReferences" in body["updateMask"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_client_character.py -k embeds_voice -v`
Expected: FAIL — no `set_character_voice`.

- [ ] **Step 3: Implement `set_character_voice` + thread through create**

In `src/gflow_cli/api/client.py` add (mirroring the existing `patch_entity`/`spike_patch_entity` shape):

```python
    async def set_character_voice(self, *, project_id: str, entity_id: str, preset_voice_id: str) -> None:
        """Embed a preset voice on a CHARACTER entity (free REST PATCH)."""
        body = {
            "entity": {
                "projectId": project_id,
                "entityId": entity_id,
                "entityInfo": {"characterInfo": {"audioReferences": [{"presetVoiceId": preset_voice_id}]}},
            },
            "updateMask": "entityInfo.characterInfo.audioReferences",
        }
        await self._patch_json(routes.FLOW_ENTITIES_URL, body, route_name="setCharacterVoice")
```

In `services/character_create.py`, accept an optional `voice: str | None` and, after the entity is created + images bound, call `client.set_character_voice(...)` when `voice` is set; include the voice in `CharacterCreateResult`. In `cli_movie._create_character`, pass `char_def.voice` (the `Character.voice` from the manifest).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/api/test_client_character.py -k embeds_voice -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/client.py src/gflow_cli/services/character_create.py tests/api/test_client_character.py
git commit -m "feat(character): embed preset voice at creation (audioReferences.presetVoiceId)"
```

---

### Task 5: Orchestrator — resolve entity identity + voice; set consistency_method

**Files:**
- Modify: `src/gflow_cli/cli_movie.py`
- Modify: `src/gflow_cli/composition.py` (`build_handoff` honors a per-scene `consistency_method`)
- Test: `tests/cli/test_cli_movie.py`

- [ ] **Step 1: Write the failing test**

```python
    async def test_entity_identity_sets_reference_entities(self, tmp_path: Path) -> None:
        from gflow_cli.cli_movie import _run_movie
        from gflow_cli.composition import Character, Scene, StyleSpec
        from gflow_cli.movie_manifest import CharacterState, MovieManifest, MovieState

        manifest = MovieManifest(
            title="T", project="p", style=StyleSpec(),
            characters={"Stickman": Character(name="Stickman", identity="entity", voice="alnilam", face_prompt="round head")},
            scenes=(Scene(id="s1", action="walks", characters=("Stickman",), duration=8),),
        )
        state = MovieState(title="T", project="p")
        # entity already created (skip character phase)
        state.characters["Stickman"] = CharacterState(entity_id="ent-9", image_paths=[])
        state_path = tmp_path / "m-state.json"
        seen = {}

        async def fake_generate(**kwargs):
            req = kwargs.get("req") or kwargs.get("request")
            seen["entities"] = tuple(getattr(req, "reference_entities", ()))
            seen["audio"] = getattr(req, "reference_audio", None)
            return _make_video_result()

        with (
            patch("gflow_cli.cli_movie.get_settings"),
            patch("gflow_cli.cli_movie.OperationRecorder") as rec,
            patch("gflow_cli.cli_movie.FlowApiClient", return_value=_mock_client_cm()),
            patch("gflow_cli.cli_movie._generate_scene", new=AsyncMock(side_effect=fake_generate)),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            rec.open.return_value = MagicMock()
            await _run_movie(manifest=manifest, state=state, state_path=state_path,
                             profile_name="default", profile_dir=tmp_path / "p",
                             out_dir=tmp_path / "out", continue_on_error=True)

        assert seen["entities"] == ("ent-9",)
```

(If `_generate_scene` composes the request internally rather than receiving it, assert instead that it was called with the resolved `reference_entities=("ent-9",)` kwarg — match the actual signature you implement.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_movie.py -k entity_identity -v`
Expected: FAIL — orchestrator doesn't yet pass `reference_entities`.

- [ ] **Step 3: Implement resolution + consistency_method**

In `cli_movie.py`, replace `_collect_refs` usage so that for each scene character with `identity="entity"`:
- look up `state.characters[name].entity_id`; if missing → **pre-flight fail loud** (`raise ConfigurationError`/log + mark scene failed) rather than silently dropping;
- collect `entity_id`s → `reference_entities`;
- resolve voice: if the entity was created with an embedded voice, no `reference_audio` needed; else if `Character.voice` set, pass it as `reference_audio` (the resolved voice mediaId);
- set `mode=Mode.R2V`.
For `identity="text"` characters, keep folding appearance into the prompt (P1 behavior), `consistency_method="text"`.

Build `GenerateVideoRequest(... reference_entities=tuple(...), reference_audio=voice_or_none)` and record the per-scene `consistency_method` ("entity" when entities attached and backstop passed; "text" otherwise; "degraded" if the backstop raised but `--continue-on-error` kept going). Pass `consistency_method` into `SceneState` (add the field) or a side map consumed by `build_handoff`.

Update `composition.build_handoff` to read the recorded `consistency_method` per scene (default "text").

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_cli_movie.py -k entity_identity -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/cli_movie.py src/gflow_cli/composition.py tests/cli/test_cli_movie.py
git commit -m "feat(movie): resolve entity identity + voice; record consistency_method"
```

---

### Task 6: Voice-list recon helper + docs

**Files:**
- Create: `scripts/dev/spike_movie_voice_list.py`
- Modify: `docs/MOVIE.md` (or create), `docs/INDEX.md`

- [ ] **Step 1: Write the voice-list recon spike (credit-free)**

Create `scripts/dev/spike_movie_voice_list.py` modeled on `scripts/dev/spike_movie_entity_recon.py` but opening the **Vozes** tab (`voice_selection` ligature) and dumping the voice tiles' visible names (the `referenceAudio` mediaIds, e.g. `alnilam`, `vega`). Reuse `_spike_common.build_client`, `default_out_path`, `resolve_profile_dir`. ASCII-only console prints (Windows cp1252) and `PYTHONUTF8=1` when running. Credit cost: 0.

- [ ] **Step 2: Run it (supervised, headed)**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe scripts/dev/spike_movie_voice_list.py --profile denon82 --project 6ba50219-0fb5-4471-a96e-83257784dfd8 --locale pt`
Expected: a JSON dump in `scripts/dev/_spike_out/` listing voice names.

- [ ] **Step 3: Document the voice list + the embedded-vs-attached model in `docs/MOVIE.md`**

Write the user-facing `movie.toml` reference: style fields, characters (incl. `voice`), scenes (incl. `framing`, `speaker`/`line`), the handoff manifest schema pointer (`docs/schemas/movie-handoff.schema.json`), and the captured voice names. Add a link in `docs/INDEX.md`.

- [ ] **Step 4: Commit**

```bash
git add scripts/dev/spike_movie_voice_list.py docs/MOVIE.md docs/INDEX.md
git commit -m "docs(movie): voice-list recon + movie.toml reference + handoff schema link"
```

---

### Task 7: Live e2e — the MVP gate

**Files:**
- Create: `tests/e2e/test_movie_consistency_e2e.py` (gated behind a live-run marker/env var, mirroring existing live e2e tests)

This is the "character stops diverging" proof. It is credit-spending and runs only when explicitly enabled (e.g. `GFLOW_LIVE_E2E=1`), on `denon82`, locale `pt`.

- [ ] **Step 1: Write the e2e (skipped unless live)**

The test: build a 2-scene `movie.toml` reusing an existing voiced entity (create one with `set_character_voice` first), run `gflow movie run` (generate-only), then assert via the handoff:
- both clips `status == "completed"`,
- both clips `consistency_method == "entity"`,
- the captured submit payloads carried `referenceEntities` (use the gen-capture harness or a response listener),
- the downloaded clips exist with non-zero size + correct magic bytes (verification ledger).
Manual confirmation step (printed): user verifies in gallery that the character is the same across both clips and the voice is audible/consistent (and runs a 2-speaker case). Record the outcome in the test log.

- [ ] **Step 2: Run the e2e live (supervised)**

Run: `GFLOW_LIVE_E2E=1 PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/e2e/test_movie_consistency_e2e.py -v -s`
Expected: PASS + user gallery confirmation that identity + voice hold. **This is the spec's P2 done-gate** (spec §12): do not announce the feature until this passes.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_movie_consistency_e2e.py
git commit -m "test(movie): live e2e gate — entity identity + voice consistency"
```

---

## Self-Review

**Spec coverage:** §8 (DTO `reference_entities`+`reference_audio`, relax R2V, cap budgeting → Task 1; `_attach_character_entities` + selectors + pin to ui_automation + backstop → Tasks 2-3; pre-flight entity existence + registry-shaped persistence → Task 5; embedded-voice-preferred + attached fallback + silenced-block handling → Tasks 3-4; voice listing → Task 6). §7 (`consistency_method` entity/degraded values → Tasks 3,5). §12 (P2 = MVP gate, live e2e → Task 7). §15 (remaining empirical checks settled in the e2e → Task 7; enumerate Vozes list → Task 6).

**Placeholder scan:** The transport selectors are concrete (spike-captured). The two soft spots — exact enum identifiers in `api/video.py` (Task 1) and the precise `_generate_scene`/`_create_character` signatures (Tasks 3,5) — are flagged with "check the source / match the actual signature" because they depend on P1's final shape; every code block is otherwise complete. The e2e (Task 7) intentionally includes a human gallery-confirmation step (credit-spending live verification can't be fully automated).

**Type consistency:** `reference_entities: tuple[str,...]`, `reference_audio: str | None` are identical across Tasks 1,3,5. `_attach_character_entities(page, names: list[str], *, out_dir)` and `_assert_entities_attached(generate_resp, *, expected)` signatures match between definition (Tasks 2-3) and call sites (Task 3). `set_character_voice(*, project_id, entity_id, preset_voice_id)` consistent Tasks 4. `consistency_method` values `{text, entity, degraded}` match the spec §7 / handoff schema (P1 Task 5).

---

## ⚠️ Council Review Fixes (MUST apply — these SUPERSEDE conflicting task text above)

A 4-lens plan-review council audited this plan against the live codebase. Apply these corrections while executing.

**C0 — Commit hygiene:** Never `git add -A`/`.` — stage only the files each task lists (the worktree root has untracked stray files). Fixtures under `tmp_path` only.

**C1 — Task 1 retarget the edit (BLOCKER): the R2V validation is NOT in `__post_init__`.** In `src/gflow_cli/api/video.py`, `__post_init__` (line ~202) only dispatches to helpers. Apply the changes in:
- `_validate_mode_symmetry` (lines ~248-254): the current R2V check `if not self.reference_images: raise ValueError("R2V request requires at least one reference image")` → change to require images OR entities and **use this exact message** (the Task 1 test asserts `match="reference_images or reference_entities"`):
  ```python
  if not self.reference_images and not self.reference_entities:
      msg = "R2V request requires reference_images or reference_entities"
      raise ValueError(msg)
  ```
  Also add `or self.reference_entities` to the I2V "must not carry references" rejection in the same method.
- `_validate_r2v_caps` (lines ~256-275): count entities + images against the per-model cap and **use a message containing `reference cap`** (the test asserts `match="reference cap"`):
  ```python
  total_refs = len(self.reference_images) + len(self.reference_entities)
  if self.model is not None:
      cap = reference_cap_for(self.model)
      if total_refs > cap:
          msg = f"reference cap exceeded: {total_refs} refs (images+entities) > {cap} for {self.model.value}"
          raise ValueError(msg)
  ```
Enum names are verified real: `Aspect.LANDSCAPE`, `Mode.R2V`, `VideoModel.VEO_3_1_LITE` (cap=3), `reference_cap_for`, `MAX_REFERENCE_IMAGES`. Drop the "adjust if different" hedge in the Task 1 test.

**C2 — Task 4 is REDUNDANT: voice-embed already exists. Replace it.** `client.patch_entity(*, project_id, entity_id, ..., voice=None, ...)` (`api/client.py:~1359`) ALREADY writes `entityInfo.characterInfo.audioReferences=[{presetVoiceId: voice}]`, and `services/character_create.py` ALREADY accepts `voice` (line ~52) and calls `patch_entity(..., voice=voice)` (line ~244) + returns it in `CharacterCreateResult`. So do NOT add `set_character_voice`. The real gap is one wire-up:
- P1's `Character` already has a `voice` field. In `cli_movie._create_character` (`cli_movie.py:~411-444`), the `character_create(...)` call (line ~435) does NOT pass `voice` — add `voice=char_def.voice`.
- Replace the Task 4 test with one asserting `_create_character` forwards the manifest character's `voice` into `character_create` (patch `gflow_cli.cli_movie.character_create` with an `AsyncMock`, assert it was awaited with `voice="alnilam"`).

**C3 — Task 5 PIN `consistency_method` persistence to a `SceneState` field (not a side map).** In `movie_manifest.py`:
- Add `consistency_method: str = "text"` to `SceneState`.
- Include it in `to_dict()` and `from_dict()` (`.get("consistency_method", "text")`). `MovieState.VERSION` is already 2 (P1); old files load via the `.get` default.
- The orchestrator sets it when building `SceneState` after a successful generate (`"entity"` if entities attached + backstop passed; `"text"` otherwise; `"degraded"` only if the backstop raised but `--continue-on-error` kept going).
- `build_handoff` reads `ss.consistency_method` (replacing P1's hardcoded `"text"`).
- Add a `SceneState` round-trip test asserting `consistency_method` survives `to_dict`→`from_dict`.

**C4 — Task 5 `_generate_scene` target is now fixed (per P1 C5):** it receives `reference_entities`/`reference_audio` kwargs. `_run_movie` resolves them: for each scene character with `identity=="entity"`, look up `state.characters[name].entity_id` (pre-flight **fail loud** if missing — `raise ConfigurationError`/mark scene failed, replacing the old silent drop), collect into `reference_entities`; resolve voice → `reference_audio` only if the entity was NOT created with an embedded voice (embedded is preferred — see C2). The test in Task 5 Step 1 should assert `_generate_scene` is awaited with `reference_entities=("ent-9",)`.

**C5 — Task 7 live e2e MUST opt out of the autouse settings-isolation fixture.** `tests/conftest.py::_isolate_settings` is autouse and forces `GFLOW_CLI_HOME`/`GFLOW_CLI_DB_PATH` to throwaway tmp dirs — a live run would hit an empty catalog/wrong profile (memory: `test-isolation-real-env-opt-out`, PR #114). The e2e must `monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)` + `delenv("GFLOW_CLI_DB_PATH", raising=False)` + call `reset_settings()` so it resolves the real `denon82` profile. Add an explicit **numbered prerequisite step**: create a voiced character entity first (credit-spending) before the 2-scene run. **Human-gated:** a headless subagent CANNOT run Task 7 (Google auth/reCAPTCHA rejects bundled Chromium) — the user runs it + confirms in the gallery; do not let a subagent invoke it.

**C6 — Human-gated vs subagent-safe (for the executor):** Tasks 1, 3 (unit tests mock the page/capture), 4, 5 are subagent-safe/headless. Task 2 is subagent-safe to WRITE (selectors are spike-captured) but cannot be live-verified without a browser. Task 6 Step 2 (voice-list spike) and Task 7 (e2e) are **headed + supervised** — the user runs them (`PYTHONUTF8=1`, ASCII-only prints).
