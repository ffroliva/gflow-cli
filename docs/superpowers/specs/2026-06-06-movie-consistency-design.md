# Design Spec — Movie Consistency System (Stickman & beyond)

**Date:** 2026-06-06
**Status:** Draft v2 — incorporates LLM council review (awaiting user review)
**Branch:** `pr162`
**Supersedes parts of:** PR #162 (`gflow movie` v1) — see `PR162_MOVIE_CHARACTER_REVIEW.md`

> v2 reflects the brainstorm pivot (gflow **generates assets + a handoff manifest**; composition is **downstream**) and the 5-lens LLM council. Where this doc once said "chain by default / `[assemble] → final.mp4`", that is **reversed**: the default is independent r2v clips, composition is downstream, i2v-chaining is backlog.

---

## 1. Context & problem

`gflow movie` (PR #162) makes multi-scene videos from a `movie.toml`, but characters **diverge**: it creates a Flow Character entity, downloads its face/body images, then **re-uploads them as generic `referenceImages`** every scene. Flow treats each upload as unrelated data → the "same" Stickman drifts. The native identity field `referenceEntities:[{entityId}]` is documented + live-verified but unused; the `entity_id` is even saved in run-state and ignored.

We want a **consistency system**: a reusable character (appearance **and** voice) reused across scenes, a guiding/meta style applied to every prompt, structured best-practice prompts, optional ≤2-speaker dialogue, and a clean **handoff** so downstream tools (Remotion, ffmpeg) do the actual film composition. gflow's job is to **generate consistent, ordered clips + a versioned manifest** — not to be a video editor.

**Release-blocker (P0):** `cli_movie.py:344` calls `asyncio.sleep(5)` with **no `import asyncio`** → `NameError` aborts every multi-scene run after scene 1 (outside the try/except); on resume the completed-scene append makes the first new scene hit it → **resume makes zero progress**. Escaped CI because no test drives `_run_movie` with ≥2 scenes.

### Verified Flow facts (code + live UI/HAR)
- i2v **Frames** sub-mode = exactly **2 image slots** (start/end). No entity slot.
- r2v **Elementos** sub-mode reference cap is **model-dependent**: `veo_3_1_lite/fast` = **3**, `omni_flash` = **7** (`video.py:128-137`). UI "10 créditos" is *cost*, not image count.
- i2v "frames" and r2v "references" are **mutually exclusive** per generation (`video.py:245-252`).
- `referenceEntities` on `video:batchAsyncGenerateVideoReferenceImages` is **live-verified to fire** (HAR `labs.google23.har`, 2026-06-02; `docs/CHARACTER.md §6.6`). The character entity stores `audioReferences[].presetVoiceId` (`docs/CHARACTER.md §2/§6.4`).
- Flow durations: **4/6/8/10 s** (`movie_manifest._VALID_DURATIONS`).
- `gflow video chain` (`chain.py`, `chain_repo`, migration `0005`) is a **shipped, standalone, user-facing command** — unrelated to `movie`.

### ✅ Spike-verified 2026-06-06 (live, denon82 / project 6ba50219; scripts/dev/spike_movie_*.py)
- **`referenceEntities:[{entityId}]` fires from the UI** → HTTP 200, response `videoGenerationMode: VIDEO_GENERATION_MODE_REFERENCE_TO_VIDEO`. Native identity path is real end-to-end. P2 transport selectors captured (picker `#add-menu-input`; **Personagens** = `accessibility_new` ligature; **"Incluir no comando"** button).
- **Voice is an INDEPENDENT resource**, not an entity property at submit time: wire carries `referenceAudio:[{mediaId:"<voiceName>"}]` (e.g. `"alnilam"`) attached from the **"Vozes"** picker (`voice_selection` ligature), *separate from* `referenceEntities`. Voices are named CDN resources (alnilam, vega, …).
- **Audio is on-by-default, no toggle:** `mediaGenerationContext.audioFailurePreference: "BLOCK_SILENCED_VIDEOS"` on every request (⚠️ a silenced result is *rejected* → a real failure mode to handle).
- **Dialogue = plain prompt text:** `structuredPrompt.parts[].text` = `Stickman says: "…"`. No special dialogue field.
- **Model `veo_3_1_r2v_lite`** carries r2v + audio. `referenceImages` (old, broken approach) and `referenceEntities` (fix) hit the **same** endpoint `video:batchAsyncGenerateVideoReferenceImages`; both can co-carry `referenceAudio`. `referenceImages` shape = `[{mediaId, imageUsageType:"IMAGE_USAGE_TYPE_ASSET"}]`.

### ⚠️ Remaining unverified — confirm in P2 live e2e (not blockers; wire is proven)
- **Embedded-voice path:** a character *created with* a voice (`audioReferences[].presetVoiceId`) carries vocal identity through `referenceEntities` **alone** (no separate `referenceAudio`). Strongly expected (docs §2) but not yet seen on the wire (spike entities were voiceless). This is the **preferred** path — verify by creating a voiced character and generating with only `referenceEntities`.
- **Identity-hold** across multiple entity-ref generations; **voice consistency** when the same voice mediaId is reused; **2-speaker** (2 entities + 2 voices) quality. All empirical-quality, validated in the P2 e2e.

---

## 2. Goals & non-goals

### Goals
1. gflow **generates consistent, ordered scene clips + a versioned handoff manifest**; downstream composes.
2. **Structured guiding style** + per-scene overrides, assembled by a **pure deterministic composer**.
3. **Character + named variants**; identity **text** (P1) or native **`referenceEntities`** (P2). Voice is a character-creation property carried by the entity.
4. **Native identity** so the character stops diverging — the headline value (P2).
5. **Dialogue** generated by Veo from the prompt: single-speaker first; ≤2-speaker (a conversation) gated behind a spike.
6. Fix the **P0 `asyncio` bug** + close the multi-scene/resume test gap.

### Non-goals (forward-aware, NOT built here — seams kept open)
| Future capability | Seam left open |
|---|---|
| Narrator / off-screen voice-over track | downstream (Remotion); distinct from in-clip dialogue. |
| Character **registry/library** reused across movies | `identity="entity"` + entity_id persisted **registry-shaped** (addressable rows) now. |
| Multi-element composition (character **+** other element images, multi-select) | `referenceEntities` + `referenceImages` are both lists in the DTO. |
| Agent-mode composer | out (only `_exit_agent_mode` exists). |
| i2v continuity **chaining** | `chain.py` stack stays intact/standalone; movie integration deferred. |
| Imagen `t2i` purpose-built anchors; sub-4 s micro-shot trimming | deferred. |

---

## 3. Architecture

A **single pure module** (council: not a 6-file package — matches the repo's flat-file convention; promote later only if it grows). No I/O; the orchestrator is the only Flow-touching component.

```
gflow_cli/composition.py
    StyleSpec        # global guiding dimensions (all optional, flat)
    Character        # name, appearance, identity, variants, voice
    Scene            # title, setting, characters, dialogue, framing, camera, …
    FRAMING          # controlled vocabulary
    compose_prompt(style, scene) -> str      # pure, deterministic, canonical order
    build_handoff(manifest, state) -> dict   # pure projection → the manifest (§7)
```

**Consumers:** `movie_manifest.py` (parse `movie.toml` → these types) and `cli_movie.py` (orchestrate generation, emit manifest, optional `--stitch`).

**Data flow (one scene = one clip):**
```
movie.toml ─▶ MovieManifest (validate) ─▶ resolve Character+variant
           ─▶ compose_prompt(style, scene) ─▶ final prompt
           ─▶ GenerateVideoRequest(prompt, mode=R2V, reference_entities|images, …)
           ─▶ ui_automation transport ─▶ clip + MovieState.save()
end of run ─▶ build_handoff(manifest, state) ─▶ <stem>-handoff.json   (+ --stitch preview)
```

**Boundary test:** `compose_prompt`/`build_handoff` are `(data) -> value` — no TOML, Flow, or browser knowledge. Fully unit-testable before any transport work.

---

## 4. Data model

```
Movie
 ├─ project, title, output_dir, schema_version
 ├─ continuity (default: "independent")     # "independent" = cuts; chaining is backlog
 ├─ StyleSpec (global guiding prompt)
 ├─ Character[]                              # reusable, registry-shaped
 └─ Scene[]                                  # SCENE = CLIP = one generation
      ├─ id (stable; explicit or content-hash), title
      ├─ setting?, framing (vocab), action (required), camera? (movement)
      ├─ characters[] (present; ≤ model ref cap)
      ├─ dialogue[]  ({speaker, line}; ≤2; speaker ∈ characters)
      ├─ variant? (single-char shorthand) / per-character variant (multi)
      ├─ duration?, model?, aspect? (inherited if unset)
      └─ type (R2V when characters present; else T2V)
```

- **Authoring leads with flat `scene = clip`.** A nested "shots" grouping is an *optional advanced* form (deferred unless needed) — **not** a mandatory level and **not** tied to chaining.
- **`StyleSpec`** (all optional, flat): `look`, `palette`, `environment`, `camera`, `lighting`, `mood`, `negative`. Simple precedence **scene → global** per field (no layered-override machinery). Empty fields omitted.
- **`Character`**: `name`, `appearance`, `identity ∈ {text,entity}`, `variants: dict[str,str]`, `voice?`, `face_prompt?`/`body_prompt?` (entity), `model?`. Voice assigned at character creation; carried by the entity (P2).
- **Variants** are appearance *deltas* merged onto base appearance.
- **Naming caveat:** `movie.toml`'s `scene` is **not** the existing `gflow scene` command (Add-Clip timeline). Document loudly.

### Character binding within a scene
`variant`/`speaker`/`line` shorthand binds to a single character — valid only when the scene has exactly one character (or the one named by `speaker`). Multi-character scenes use the explicit per-character table (`variant`/`line` bind by `name`); **rejected at parse time** if shorthand is used with >1 character.

---

## 5. `movie.toml` schema (annotated — leads with the flat/simple form)

```toml
schema_version = 1
title      = "The Stickman Journey"
project    = "6ba50219-…"
output_dir = "./out/stickman"

[movie]
continuity = "independent"      # default: cuts. (chaining = backlog)

[style]                         # global guiding prompt — every field optional, reused verbatim
look     = "minimalist hand-drawn black-ink line art"
palette  = "monochrome with selective color accents"
camera   = "eye-level, steady"
negative = "no text, no logos, no watermark, no clutter"

[[characters]]
name       = "Stickman"
appearance = "simple stickman: round head, smiley face, T-shirt, shorts"
identity   = "text"             # "text" (P1) | "entity" (P2 → referenceEntities)
voice      = "warm, upbeat male voice"   # carried by the entity (P2); best-effort until spike-verified
# face_prompt / body_prompt     # used only when identity = "entity"
  [characters.variants]
  white      = "drawn in solid white lines on a dark background"
  silhouette = "filled black silhouette"

[[scenes]]                      # SCENE = CLIP
id       = "summit-wide"
setting  = "mountain peak above a sea of clouds, sunset sky"
framing  = "wide"
action   = "crests the ridge and stops, gazing out"
camera   = "slow push-in"       # movement within one generation (not a cut)
characters = ["Stickman"]
variant  = "silhouette"
duration = 8

[[scenes]]
id       = "summit-line"
setting  = "mountain peak, sunset"
framing  = "close-up"
action   = "turns to camera, beaming"
characters = ["Stickman"]
variant  = "silhouette"
speaker  = "Stickman"           # single-speaker (P1)
line     = "We finally made it to the top!"
duration = 8

# Preview only (NOT a deliverable): gflow movie run movie.toml --stitch
```

---

## 6. Composer semantics (`compose_prompt`)

**Canonical slot order**, each filled by **scene → global → omit**:

```
1. ACTION       scene.action  (required)
2. SUBJECT      character.appearance + resolved variant delta(s)
3. SETTING      scene.setting → style.environment
4. STYLE        style.look
5. COLOR        style.palette
6. LIGHTING     scene.lighting → style.lighting
7. FRAMING+CAM  scene.framing (vocab) + scene.camera → style.camera
8. MOOD         scene.mood → style.mood
9. DIALOGUE     attributed block (§6.1)  [only if any speaker/line]
10. NEGATIVE    style.negative + scene.negative  (MERGED; trailing "Avoid: …")
```

**Rules:** R1 `negative` **merges** (global+scene). R2 variants **always prompt-applied**, even with `identity="entity"` (entity images are fixed; restyle is prompt-only). R3 precedence scene>global. R4 empty slots omitted. The exact assembled string is an **implicit contract** → golden-file tested; reordering requires deliberate golden updates.

### 6.1 Dialogue block
- **Single speaker (P1):** `Stickman (warm, upbeat male voice) says: "We finally made it!"`
- **Two speakers (spike-gated):** ordered attributed block, array order = speaking order:
  ```
  Dialogue:
  Stickman (warm, upbeat male voice): "We made it!"
  Dog (high, excited): "Woof!"
  ```
- Quotes escaped on compose. No `line` = silent (visual only). **Soft warning** above 2 speakers (don't hard-block).

---

## 7. Handoff manifest — the external contract (council #1 priority)

The manifest is gflow's **primary deliverable** and a **first-class, output-only, versioned public contract** — NOT the resume `-state.json`, NOT a dump of the SQLite catalog. It is a **pure derivation of `MovieState` + `MovieManifest`** (`build_handoff`), regenerated every run (so there's still exactly **one** mutable store; no third-store drift).

**Contract principles:**
1. `schema_version` (integer) is the first key; consumers gate on it. **Additive-only** within a major (new optional fields don't bump major; renames/removals/semantic changes do). Policy documented in `docs/MOVIE.md`.
2. **Flat, ordered `clips[]`** with stable `id` + explicit `index` (timeline is the contract; never array-position-implicit). Authoring tree is NOT leaked.
3. **Relative, POSIX-style paths** (relative to `output_dir`) — portable bundle (Windows/POSIX split is real here). Inline `duration_seconds`/`width`/`height` so downstream never probes media.
4. **No secrets / no PII:** never signed URLs (`fifeUrl`), tokens, session ids, or redaction-suppressed prompt text (honor `GFLOW_CLI_HISTORY_PROMPTS`). Flow-internal ids (`mediaId`/`entityId`/`projectId`) quarantined under **`x_gflow`** and are **never load-bearing** for consumers.
5. **`consistency_method: "entity" | "text" | "degraded"` per clip** — downstream/users SEE when identity consistency was lost (e.g., entity attach failed → backstop) instead of discovering it on screen.
6. P2 fields (`identity_mode`, `dialogue[].voice`, character `entity_id`) **reserved optional from v1** so P2 is additive, not breaking.
7. Shipped with an in-repo **JSON Schema** + a **golden round-trip test** in CI.

**Example `out/stickman/movie-handoff.json`:**
```json
{
  "schema_version": 1,
  "generator": { "name": "gflow-cli", "version": "0.14.0" },
  "movie": { "title": "The Stickman Journey", "output_dir": ".", "total_duration_seconds": 16.0 },
  "style": { "look": "minimalist hand-drawn black-ink line art", "palette": "monochrome + accents", "negative": "no text, no logos" },
  "characters": [
    { "name": "Stickman", "identity": "entity", "voice": "warm, upbeat male voice", "x_gflow": { "entity_id": "ent_…" } }
  ],
  "clips": [
    { "id": "summit-wide", "index": 0, "file": "clips/summit-wide.mp4",
      "duration_seconds": 8.0, "width": 1080, "height": 1920, "framing": "wide",
      "characters": ["Stickman"], "consistency_method": "entity", "dialogue": [],
      "prompt": "…", "status": "completed",
      "x_gflow": { "media_id": "m_…", "operation_id": "op_…", "project_id": "6ba5…" } },
    { "id": "summit-line", "index": 1, "file": "clips/summit-line.mp4",
      "duration_seconds": 8.0, "framing": "close-up", "characters": ["Stickman"],
      "consistency_method": "entity",
      "dialogue": [ { "speaker": "Stickman", "voice": "warm, upbeat male voice", "line": "We finally made it!" } ],
      "prompt": "…", "status": "completed", "x_gflow": { "media_id": "m_…", "operation_id": "op_…" } }
  ],
  "stitch": { "performed": false, "output": null }
}
```

---

## 8. Identity (text vs entity) & voice

- **P1 `identity="text"`**: appearance + variant fold into SUBJECT. No entity, no transport change. Good — often better — for a stickman.
- **P2 `identity="entity"`** (the headline fix, MVP done-gate):
  - DTO: add `reference_entities: tuple[str, ...] = ()` **and** `reference_audio: str | None = None` to `GenerateVideoRequest`; **relax** `__post_init__` so R2V is valid with `reference_images` **or** `reference_entities` (today R2V requires images, `video.py:249-251`). Keep frames-XOR-references intact. Wire: `requests[].referenceEntities:[{entityId}]`, `requests[].referenceAudio:[{mediaId}]`, `mediaGenerationContext.audioFailurePreference:"BLOCK_SILENCED_VIDEOS"`.
  - **Reference-cap budgeting (parse-time):** reject `len(reference_entities)+len(reference_images) > cap(model)` (3 veo_3.1 / 7 omni).
  - Transport: new `_attach_character_entities(page, names, out_dir)` on the **already-checked-out composer page** (mirror `_attach_references`; never check out a 2nd page — size-1 pool deadlock). Open picker (`ADD_MEDIA_BUTTON`) → **Personagens** tab (`accessibility_new` ligature) → select by name → **"Incluir no comando"**. Selectors **captured 2026-06-06** (`#add-menu-input` search; ligature tabs); still re-verify on a non-EN locale before merge. ⚠️ picker shows name+thumbnail, **not entityId** — disambiguate identically-named entities (the movie creates unique names per manifest; for the registry, pick most-recent or surface the id).
  - **Pin to `ui_automation`** — REST transports (`evaluate_fetch`/`bearer`) silently drop unknown fields → text-only false success (`rest-transports-drop-ui-fields`). **Backstop:** assert the captured payload's `referenceEntities` contains the expected `entityId`; on miss → set `consistency_method="degraded"` and raise/warn loudly (no silent degrade).
  - Pre-flight: verify each entity exists in the project (fail loud, replacing the silent drop in `_collect_refs`). Persist entity rows **registry-shaped** (addressable) for the future library.
- **Voice (corrected by spike — two paths, embedded preferred):**
  - **Embedded (default, recommended):** create the character *with* a voice (entity `audioReferences[].presetVoiceId`, free REST PATCH at create time). Then `referenceEntities` alone carries appearance **and** voice → one reusable resource locks both. *Character creation must learn to set a voice (it currently cannot — every existing entity has `voice:null`).* Verify the "entity-alone carries voice" wire in P2 e2e.
  - **Attached (override/fallback):** `reference_audio` = a named voice mediaId (e.g. `"alnilam"`) → `requests[].referenceAudio`. Used for voiceless legacy entities or a per-scene voice override. This is what the spike exercised and is wire-verified.
  - **Voice listing:** voices are named CDN resources in the "Vozes" picker; the plan includes enumerating them (recon/`gflow voices`) so a manifest can name a voice. `Character.voice` resolves to either the embedded `presetVoiceId` or a `reference_audio` mediaId.
  - **Silenced-block handling:** with `audioFailurePreference: BLOCK_SILENCED_VIDEOS`, a clip that generates no audio is *rejected* by Flow — treat as a real failure (surface, don't silently pass).

---

## 9. Framing vocabulary
Validated set → slot 7: `establishing · wide · full · medium · medium-close · close-up · extreme-close-up · over-the-shoulder · POV`. Unknown values rejected at parse time.

## 10. Composition & `--stitch` (the non-goal guard)
- **Default run = generate clips + emit `movie-handoff.json`. No final composition.**
- **`--stitch` (off by default) = throwaway preview only**: a single ffmpeg/scene-concat pass, **hard-concat, no transitions/audio-mix/re-encode options**. Does **not** use `chain.py`. Help text + spec state the non-goal explicitly so future PRs don't grow it into an editor. Real composition (layers, audio, narrator, transitions) = downstream via the manifest.
- Durations: native **4/6/8/10 s** only. Sub-4 s trimming = backlog.

## 11. Error handling
- **P0:** add `import asyncio`; move the reCAPTCHA cooldown so a failure can't silently abort the whole run.
- **Resume** keyed on the **stable `scene.id`** (not the editable title — renaming must not re-burn credits). `MovieState.VERSION` bumped + tolerant `from_dict` (graceful-degrade on old files). Record-before-download discipline retained (`recorder`).
- Per-scene failure: `--continue-on-error` marks failed + continues; `--fail-fast` stops (default-on for early runs). A `--plan`/dry-run prints the **credit estimate** before any spend.
- Reused as-is (verified sound): 401→`AuthExpiredError`, 403→`WafRejectionError`, silent reCAPTCHA→`TimeoutError`, listeners freed in `finally`.

## 12. Phasing (one spec, dependency-ordered; **P2 is the MVP/announce gate**)
- **P0 — Hotfix (ship now, standalone):** `import asyncio` + 2-scene **and** resume regression tests.
- **P1 — Composition core + manifest:** `composition.py` (StyleSpec, Character+variants, Scene, framing, `compose_prompt`, `build_handoff`), `movie.toml` parser → core types, **manifest as versioned contract frozen at v1 (JSON Schema + golden test, P2 fields reserved)**, generate-only default + `--stitch` preview, single-speaker dialogue, `identity="text"`. Full unit coverage. *Not the announce gate.*
- **P1.5 — Pre-plan spike: DONE 2026-06-06** (see §1 spike-verified). Confirmed: `referenceEntities` fires, voice = `referenceAudio` resource, audio on-by-default, dialogue-as-text, model `veo_3_1_r2v_lite`, picker selectors. Remaining empirical-quality checks (identity-hold, voice consistency, 2-speaker, embedded-voice-alone) fold into the **P2 live e2e**, not a separate spike.
- **P2 — Native identity (MVP done-gate):** `reference_entities` DTO + validation relax + cap budgeting + `_attach_character_entities` + payload backstop + `consistency_method` + voice-on-creation + orchestrator wiring (`identity="entity"`) + ≤2-speaker dialogue **iff** the spike passed. Live non-EN e2e proving non-divergence.

**Backlog (tracked):** i2v continuity chaining, narrator track, character registry UI, multi-element composition syntax, Agent mode, Imagen anchors, micro-shot trimming.

## 13. Validation rules (parse-time)
- `schema_version` present + supported.
- `scene.id` unique + stable; `dialogue[].speaker` ∈ scene `characters`; `variant` ∈ character `variants`; `framing` ∈ vocab.
- shorthand `variant`/`speaker`/`line` rejected when scene has >1 character (require per-character table).
- `len(reference_entities)+len(reference_images) ≤ cap(model)`.
- `identity="entity"` requires `face_prompt`.
- model/mode (#125) guards reused. Duration ∈ {4,6,8,10}.

## 14. Testing strategy
- **Pure composer/handoff (P1, exhaustive):** golden strings for slot order, precedence (R3), variant folding (R2), negative merge (R1), empty-slot omission (R4), quote escaping, single/2-speaker dialogue; **manifest golden round-trip + JSON-Schema validation**; redaction honored; relative-POSIX paths; no-secret assertions.
- **Validation:** every §13 rule, happy + failure (incl. cap overflow, shorthand-with-multi-char).
- **Orchestrator:** multi-scene run (catches P0), resume by `scene.id`, partial-failure continue/fail-fast.
- **Transport (P2):** `referenceEntities` lands in the captured payload; backstop raises + sets `degraded` when it doesn't; pinned to `ui_automation`.
- **Live e2e** per DoD (non-EN locale, happy + error/exit paths) before P2 "done."

## 15. Verification status
**Resolved by the 2026-06-06 spike (§1):** `referenceEntities` wire + selectors; voice = `referenceAudio` named resource; audio on-by-default (`audioFailurePreference`); dialogue-as-text; model `veo_3_1_r2v_lite`; image-vs-entity contrast.
**Deferred to P2 live e2e:** embedded-voice-rides-`referenceEntities`-alone; identity-hold across entity gens; voice consistency on mediaId reuse; 2-speaker quality. **Enumerate the Vozes voice list** (recon/`gflow voices`) so manifests can name voices.

## 16. Affected / new files
- **New:** `gflow_cli/composition.py`; `docs/schemas/movie-handoff.schema.json`; tests under `tests/composition/`, `tests/cli/` (multi-scene/resume), `tests/api/transports/` (entity attach).
- **Changed:** `movie_manifest.py` (core types; `scene.id`; state VERSION bump), `cli_movie.py` (P0 fix; generate-only + `build_handoff`; `--stitch`; entity wiring), `api/video.py` (`reference_entities` + cap validation), `api/transports/ui_automation_video.py` (`_attach_character_entities` + backstop), `docs/MOVIE.md`/`docs/INDEX.md`.
- **Untouched (council):** `chain.py`, `data/chain_repo.py`, migration `0005`, `gflow video chain`. Reused: `media.py`, `data/recorder.py`.

## 17. Council resolutions (record)
Interface-Architect 🟢, Strategist 🟢, Veo-Realist 🟡, Systems-Architect 🟡, Red-Teamer 🟡→🔴 (externalization). All resolutions adopted: manifest-as-versioned-contract (derived from MovieState); spike-gate dialogue/voice, single-speaker first; flat clips output + flat scene=clip authoring (shots optional, resume by stable id); structured-but-flat optional style with simple precedence; single `composition.py` module; keep `gflow video chain` untouched; `--stitch` = ffmpeg preview only; P2 = MVP gate; reference-cap budgeting; reconcile continuity default (`independent`) and drop `[assemble] → final.mp4` as a deliverable.
