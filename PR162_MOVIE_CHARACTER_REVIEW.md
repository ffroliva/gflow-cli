# PR #162 Review — Movie Orchestration Character Consistency

**Branch:** `pr162` · **Scope:** `gflow movie` orchestrator (`cli_movie.py`, `movie_manifest.py`) + video transport
**Verdict:** Sound foundation, correct project consolidation, but the headline goal (consistent character) is **not achievable with the current r2v-by-uploaded-image approach** — and there is a **release-blocking `NameError` that aborts every multi-scene run after scene 1.**

---

## TL;DR

| # | Objective | Finding |
|---|-----------|---------|
| 0 | *(not in brief)* | 🔴 **CRITICAL** — `asyncio.sleep(5)` at `cli_movie.py:344` with **no `import asyncio`** → `NameError` on the 2nd scene of every movie, outside the try/except → whole run aborts after 1 scene. |
| 1 | Native character integration | ✅ **Feasible & protocol-ready.** Flow's wire has a native field `referenceEntities:[{entityId}]` on `video:batchAsyncGenerateVideoReferenceImages` (live-verified, `docs/CHARACTER.md §6.6/§8`). The entity_id is *already saved* in state but never used. Needs a new transport path (composer resource-picker → "Personagens") + DTO field. |
| 2 | r2v vs i2v / chain | ✅ The chaining engine **already exists** (`chain.py`, `media.extract_last_frame`, `gflow video chain`). It's just not wired into `movie.toml`. r2v+entity = identity; i2v-chain = motion continuity; **mutually exclusive per generation** (DTO enforces frames XOR references). |
| 3 | Project consolidation | ✅ **Correct and it's the enabler.** `manifest.project` is threaded to character-create *and* every scene; `_enter_editor` deep-links to that project. This is exactly the precondition that makes native `referenceEntities` selection possible. |
| 4 | Error handling (`generate_resp`/403) | ✅ **Already fixed.** `generate_resp={}` bound before `try` (`:1421`); 403→`WafRejectionError`, silent→`TimeoutError`; listeners freed in `finally`. No UnboundLocal remains. |

---

## 0. 🔴 CRITICAL — `asyncio` is not imported (multi-scene runs crash)

`cli_movie.py:342-345`:

```python
# reCAPTCHA cooldown
if completed_scene_ids:
    await asyncio.sleep(5)      # NameError: name 'asyncio' is not defined
```

- `asyncio` appears **exactly once** in the file (the usage); it is **not** in the import block (`:17-44`).
- `completed_scene_ids` is non-empty from the 2nd scene onward (appended at `:373`, and on resume at `:332`).
- The call sits **outside** the per-scene `try/except` (`try` starts at `:346`), so the `NameError` propagates out of `_run_movie` → caught by `run_with_handlers` → **the entire movie aborts after exactly one scene**, regardless of `--continue-on-error`.
- On *resume* it's worse: the first already-completed scene appends to `completed_scene_ids`, so the very first new scene hits the cooldown and crashes → **resume can make zero forward progress.**

**Why it escaped CI:** the only async-orchestrator test (`tests/cli/test_cli_movie.py:368 test_happy_path_no_characters`) uses a **single** scene. No test drives `_run_movie` with ≥2 scenes, so the cooldown branch is never executed. (The `out/stickman-v3/` artifacts predate this regression — file mtime is later than the renders.)

**Fix (one line):** add `import asyncio` to the import block. **Then add a 2-scene orchestrator test** so this can't regress (see §6).

---

## 1. Native character integration — the real fix for divergence

### Current behaviour (confirms the brief)
- `character_create` downloads face/body to **local disk**; `CharacterState` stores both `entity_id` *and* `image_paths` (`cli_movie.py:454-457`).
- `_collect_refs` (`cli_movie.py:470-483`) returns **only `image_paths`** for r2v. `entity_id` is captured and then ignored.
- `_generate_scene` passes those local paths as `reference_images`; the transport's `_attach_references` (`ui_automation_video.py:1092`) **re-uploads each file** via the "Add Media" dialog → fresh media IDs every scene → Flow sees unrelated assets → the Stickman drifts. **Exactly the diagnosed flaw.**

### The native path exists and is verified
From `docs/CHARACTER.md §6.6` (live-verified 2026-06-02, `labs.google23.har`):

```json
"video:batchAsyncGenerateVideoReferenceImages" → {
  "referenceImages":   [ { "mediaId", "imageUsageType":"IMAGE_USAGE_TYPE_ASSET" } ],
  "referenceEntities": [ { "entityId" } ]      // ← native character identity
}
```

- `referenceEntities` is a **list** → multi-character per scene (`VIDEO_MODEL_CAPABILITY_MULTI_REFERENCE`).
- UI flow: composer → resource picker (*"Pesquisar recursos"*) → **Personagens** tab → select → **"Incluir no comando"** → injects `referenceEntities`.
- CLI `gflow video … --character <id>` is documented **backlog Phase 3 — unimplemented** (`docs/CHARACTER.md §8`). So this is *new automation*, not a refactor of existing code.

### Proposed refactor

**(a) DTO** — `api/video.py`, `GenerateVideoRequest`:
```python
reference_entities: tuple[str, ...] = ()   # R2V — Flow CHARACTER entity ids
```
⚠️ Relax `__post_init__`: today R2V **requires** `reference_images` (`video.py:249-252`). Change to *require `reference_images` **or** `reference_entities`* (either anchors the clip). Keep frames XOR references.

**(b) Transport** — new `_attach_character_entities(page, names, out_dir)` that, in `references` sub-mode, opens the resource picker, switches to the Characters/"Personagens" tab, selects each character **by display name**, clicks "Include in prompt". In `_generate_video_locked` R2V branch (`:1406`): if `request.reference_entities` → use entity attach; else fall back to `_attach_references`. (Both *may* coexist per the wire.)
- **Defense-in-depth (mirror the #125 backstop):** after submit, assert the captured generate payload's `referenceEntities` contains the expected `entityId`; if it's missing, raise rather than report a false success — otherwise an entity that silently failed to attach degrades to a plain text-only clip with no warning.

**(c) Orchestrator** — `_collect_refs` → return entity refs when `CharacterState.entity_id` is present, image paths only as fallback. Pass `reference_entities=(...entity_ids...)` into `GenerateVideoRequest`. Selection is **by name** (what the picker shows); `entity_id` is the verification key. No `movie.toml` change needed — `characters = ["Stickman"]` already names them; the orchestrator maps name→entity_id from state.

**(d) Pre-flight guard** — before the scene loop, verify each named entity actually exists in the project (`client.get_character(entity_id=...)`). Currently `_collect_refs` silently **drops** a missing character (`log.warning` + `continue`, `:478`) → the scene generates with **no refs at all** → guaranteed divergence with no hard failure. Fail loud instead.

---

## 2. r2v vs i2v for journey continuity — the chain engine already exists

**Key constraint:** R2V "references" and I2V "frames" are **separate, mutually-exclusive composer sub-modes** (`_switch_video_sub_mode`, `:903`) and the DTO enforces it (`video.py:245-252`). You **cannot** have character-entity identity *and* a seeded start-frame in the *same* generation.

What each buys you:
- **r2v + `referenceEntities`** → strong **identity** consistency per clip; hard cuts between scenes.
- **i2v chain** (scene N+1 start = scene N's last frame) → **motion/visual continuity**; but link 0 has no identity anchor and identity drifts frame-to-frame.

**You already have the chain machinery** — it's just not exposed in `movie.toml`:
- `media.extract_last_frame(src, dst, *, offset_ms=0)` (`media.py:31`)
- `chain.run_chain(...)` + `ChainLinkSpec`/`ChainLinkResult`/`FrameExtractor` protocols (`chain.py:74-185`) — record-before-extract, crash-resumable.
- `chain_repo.ChainLinkRecorder` + migration `0005_add_chain_links.sql`.
- `gflow video chain` (`cli_video.py:324 _run_chain`): "link 0 as T2V, every later link as I2V seeded by the previous clip's last frame."

**Recommendation:** add an opt-in chain mode to `movie.toml` that routes through the existing `chain.run_chain()` rather than reimplementing it (e.g. top-level `mode = "chain"`, or scene-level `chain_from = "<previous scene title>"`). Then give honest guidance in docs:

> For a **journey where the character must stay the same**, prefer **r2v + native character** on *every* scene (each clip independently anchored to the entity → best identity, accepts cuts). Use **chain** when smooth motion handoff matters more than identity, accepting drift. They can be mixed per-scene but never combined within one clip.

The strongest practical journey today = r2v+entity on each scene; chaining is complementary, not a substitute, for the *consistency* goal specifically.

---

## 3. Project consolidation — verified, and it's load-bearing

`manifest.project` (single value) is passed to `_create_character(project_id=…)` (`:309`) **and** `_generate_scene(project_id=…)` (`:356`); `_enter_editor` deep-links via `routes.project_editor_url(locale, project_id)` → `page.goto` (`ui_automation.py:815-818`); characters are created at the project's character-editor URL (`:2275`). So every character and every scene live in **one** project. ✅

This is precisely why the native-character refactor (§1) works: the entity must be in the **active** project to appear in the composer's "Personagens" picker. The consolidation you added is the enabler — keep it, and add the §1(d) guard so a missing entity fails loudly instead of silently degrading.

---

## 4. Error handling (`generate_resp` / `responses` / 403) — already correct

- `generate_resp: dict[str, Any] = {}` is bound **before** the `try` (`ui_automation_video.py:1421`) → no access-before-assignment on any failure path.
- `_await_generate_response` raises a clear `TimeoutError` when reCAPTCHA fails *silently* (no response captured, `:1172-1179`).
- `_parse_generate_response` maps **401→`AuthExpiredError`**, **403→`WafRejectionError`**, other non-200→`WireFormatError` (`:1245-1262`).
- Both response listeners are removed in `finally` (`:1484-1488`); `generate_handler`/`status_handler` are bound before the `try`.
- `responses` only appears in comments — no live variable by that name. **No remaining UnboundLocal/TypeError on the 403 path.** Nothing to fix here.

---

## 5. Other observations (non-blocking)

- **Dry-run vs reality mismatch:** the plan prints `refs=[<names>]` (`:260`) but r2v actually consumes image *paths*; after §1 this becomes accurate again (names = entities). Minor.
- `CharacterState`/`SceneState` are non-frozen dataclasses — intentional (mutated in place); fine.
- Summary stitch hint uses `flow_operation_id or media_id` (`:371`) — good.

## 6. Testing gap (ties to the DoD: "full e2e covering all scenarios")

- No test drives `_run_movie` with **≥2 scenes** → the cooldown/`asyncio` path and the resume-append path are unexercised. Add: a 2-scene happy path (mock `_generate_scene`) that would have caught §0, and a resume test (one scene pre-completed in state).
- Add a unit test asserting `_collect_refs` (post-refactor) returns entity ids when present, and a transport test asserting `referenceEntities` lands in the captured payload.
- Per project DoD memory, a movie feature isn't done until a live e2e covers happy + all error/exit paths on a real project.

---

## Suggested sequencing

1. **Hotfix now:** `import asyncio` + 2-scene orchestrator test. (Unblocks the feature as-is, even with uploaded-image refs.)
2. **Identity fix:** DTO `reference_entities` + validation relax + transport entity-attach + payload backstop + orchestrator wiring + missing-entity guard.
3. **Journey mode (opt-in):** wire `movie.toml` → existing `chain.run_chain()`; document the identity-vs-continuity tradeoff.
