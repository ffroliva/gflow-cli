# Asset tagging (`@` mentions) — research & integration design

> **Status:** RESEARCH — no wire capture yet · **Branch:** `claude/google-flow-asset-tagging-r3aqjb`
> **Convention:** this is the pre-implementation recon doc for the `@`-mention surface, following the
> `<FEATURE>_RECON.md` pattern ([INDEX § recon docs](INDEX.md)). Wire-payload sections are marked
> **HYPOTHESIS** until a live capture spike confirms them; everything marked **VERIFIED** is grounded in
> shipped gflow code or existing recon docs, not guesses.
> **Related:** [CHARACTER.md](CHARACTER.md) (entity model, `referenceEntities` wire), [MOVIE.md](MOVIE.md)
> (entity-attach mechanism), [AGENT_UI_RECON.md](AGENT_UI_RECON.md) (agentic cohort), [INSTRUCTIONS.md](INSTRUCTIONS.md).

## 1. What the feature is (product research)

In Flow's prompt box, typing **`@`** opens a dropdown of the project's assets. Selecting one inserts a
**mention chip** into the prompt text; the model then anchors on that specific asset instead of inventing a
new one. Three asset classes are taggable:

| Tag | Asset class | gflow equivalent today |
|---|---|---|
| `@CharacterName` | Saved project **Character** (entity) | `gflow character` entities — `referenceEntities` on the wire (VERIFIED, [CHARACTER § 6.6](CHARACTER.md#66-videobatchasyncgeneratevideoreferenceimages-reuse)) |
| `@me` | Personal **Avatar / likeness** (face + voice scan, I/O 2026) | `referenceLikenesses` — **region-gated**; `GET /v1/flow/likeness:checkEligibility` returns `REGION` for our accounts ([CHARACTER § 1.1](CHARACTER.md#11-how-it-works-plain-language)) |
| `@AssetName` | Uploaded/generated **ingredient** (image, object, style ref) | `--ref <path|uuid>` on `video r2v` / `image i2i` — `referenceImages[{mediaId, imageUsageType:IMAGE_USAGE_TYPE_ASSET}]` (VERIFIED wire shape) |

Product-side facts from public sources (Google Flow Help, blog.google "5 tips for Flow", I/O 2026
coverage):

- The dropdown **searches uploaded assets by name** — it is a name→asset resolver over the project's
  asset library, the same library the "Video Ingredients" mode and SceneBuilder draw from.
- `@me` requires the one-time avatar scan (read numbers aloud + head turn); the likeness is stored on the
  Google Account and referenced across Gemini/Flow. Regional gating applies (matches our
  `likeness:checkEligibility` finding).
- Community guidance: consistency holds best when the reference image has a **clean, uncluttered
  background**; drift (clothing/face shifts) still occurs under heavy motion — tagging anchors identity,
  it does not eliminate drift.

Sources: [Google Flow Help — Create videos](https://support.google.com/flow/answer/16353334?hl=en) ·
[blog.google — 5 tips for Flow](https://blog.google/technology/ai/flow-video-tips/) ·
[I/O 2026 avatar coverage](https://nokiapoweruser.com/google-io-gemini-avatar-web-flow-android-update/) ·
[vidau.ai Flow 2026 guide](https://www.vidau.ai/google-flow-ai-update-2026-complete-guide/).
(Several guides are WAF-walled to bots; facts above were cross-checked across at least two sources.)

## 2. What gflow already owns (VERIFIED against shipped code)

The `@` feature is **not a new wire capability** — it is a new *UI affordance* over reference mechanisms
gflow has already reverse-engineered and partially shipped:

1. **Entity references ride today.** `gflow movie run` attaches a CHARACTER entity by opening the
   resource picker, right-clicking the tile addressed as `data-tile-id="fe_id_<entityId>"`, and choosing
   the include-in-prompt action (`PICKER_CONTEXT_INCLUDE`, locale-free icon tier — `ui_automation_video.py`).
   The submit then carries `referenceEntities:[{entityId}]`, echoed back in
   `workflows[].videoData.videoGenerationEntityInputs[].entityId`, asserted by `_assert_entities_attached`
   ([MOVIE § Character consistency](MOVIE.md#character-consistency-how-the-entity-rides)).
2. **Image references ride today.** `gflow video r2v --ref` (local path or in-project media UUID) stages
   `referenceImages` — including UUID-addressed selection of existing assets with zero re-upload
   (live-verified v0.26.0/v0.33.0).
3. **Name→asset resolution exists.** `flow.projectInitialData` returns entities with `displayName`
   ([CHARACTER § 6.5](CHARACTER.md#65-flowprojectinitialdata-listshow)); the local catalog records
   generated-image `display_name` (v0.26.0). This is exactly the lookup the `@` dropdown performs.
4. **The prompt box is a Slate editor.** `_send_prompt` targets
   `div[role="textbox"][data-slate-editor="true"]` and uses `keyboard.insert_text` (one `beforeinput`
   event, Slate-native). A mention chip is a **non-text Slate node** — `insert_text` alone can never
   create one.
5. **Diagnostics are pre-staged.** The image-submit capture already probes for `referenceEntit` in POST
   bodies (`mentions_reference_entities`, `ui_automation.py`) — built for exactly this recon.

**Gaps the `@` feature would close:**

- `gflow video --character` (Phase 3 backlog, [CHARACTER § 14](CHARACTER.md#14-backlog--not-yet-implemented)) — single-command character reuse outside the movie pipeline.
- `gflow image --character` — image-path `referenceEntities` wire shape still uncaptured.
- Inline, per-prompt asset naming (`"@Zoro hands @Mika the sword"`) — today references are flag-level
  (whole-generation), not positional in the prompt text.

## 3. Wire hypothesis (HYPOTHESIS — spike must confirm)

Two candidate serializations when a mention chip is present at submit:

- **H1 — flags-equivalent:** the chip's display text lands as plain text inside
  `structuredPrompt.parts[].text`, and the referenced asset is added to `referenceEntities` /
  `referenceImages` exactly as the picker path does. The chip is UI sugar; position in the prompt is not
  semantically encoded on the wire. → If true, gflow can synthesize identical payloads with the
  **already-live-verified attach primitives** and never needs to drive the dropdown.
- **H2 — positional parts:** `structuredPrompt.parts` gains a dedicated mention part (e.g.
  `{ "entityReference": { "entityId": … } }` or a mediaId-bearing part) interleaved with text parts, so
  the model sees *where* in the sentence the asset is referenced. → If true, faithful support requires
  either driving Flow's dropdown UI (chip built by Flow's JS) or extending the passive-captured body —
  and only the dropdown path survives the reCAPTCHA wall for generation calls
  ([CHARACTER § 11](CHARACTER.md#11-design-decisions-post-predict): self-assembled POSTs 403).
- `@me` almost certainly maps to `referenceLikenesses` (Avatar PR #123 recon) — **held** regardless,
  since our accounts are region-ineligible; treat as detect-and-explain, not implement.

Agent-mode nuance: in the **agentic cohort** ([AGENT_UI_RECON.md](AGENT_UI_RECON.md)) the prompt feeds a
reasoning agent, which may resolve `@Name` text itself even without a chip. The spike should test both
cohorts; classic is the contract-bearing path.

## 4. Integration options

| | Option A — drive the dropdown | Option B — CLI-side mention parsing → existing attach primitives | Option C — direct REST |
|---|---|---|---|
| Mechanism | Type prompt up to `@`, pause, let Flow's dropdown open, select entry (structural selectors), continue typing | Parse `@Name` tokens CLI-side; resolve names via `projectInitialData` + media catalog; attach via shipped picker/UUID primitives; submit prompt with names as plain text | Build `referenceEntities`/mention parts ourselves and POST |
| Fidelity | Exact — Flow's JS builds the chip | Equivalent **iff H1**; loses positional semantics if H2 | n/a |
| New selector surface | High (dropdown container, search behavior, per-type entries, both cohorts, all locales) | **Zero** — reuses `fe_id_<entityId>` tiles + `PICKER_CONTEXT_INCLUDE` + UUID picker, all live-verified | n/a |
| Risk | New drift surface; dropdown is keystroke-timing sensitive (Slate `insert_text` won't trigger it — needs real per-char typing around `@`) | Name-resolution ambiguity (duplicate display names) — solvable with exit-11 disambiguation, same as `character show --name` | **DEAD** — generation POSTs are reCAPTCHA-walled (403, spike-proven) |

**Recommendation: Option B first, gated on a capture spike that decides H1 vs H2.** If H1 holds
(likely — the picker path already produces accepted `referenceEntities` submits), gflow gets `@` mentions
with no new UI automation at all: the feature becomes a *prompt-syntax resolver* in front of
live-verified machinery. Option A is the fallback only if H2 is proven **and** positional semantics
demonstrably change output.

## 5. Proposed CLI surface (Phase 1, post-spike)

Inline mentions in the prompt on generation commands, mirroring Flow's own syntax:

```bash
gflow video t2v "@CaptainZoro walking through a rain-soaked neon city, tracking shot" --project <id>
gflow video r2v "@Zoro hands @Mika the sword" --project <id>          # multi-ref (cap-checked)
gflow image i2i "@logo on a black tee, studio light" --project <id>
gflow movie run film.toml                                              # scene prompts may carry @mentions
```

Rules:

- **Grammar:** `@` + longest match against project asset names (chip names may contain spaces —
  resolver tries greedy longest-match, then falls back to single-token). `@@` escapes a literal `@`;
  email-like tokens (`a@b.com`) are never treated as mentions (require `@` at token start).
- **Resolution order:** CHARACTER entities (`projectInitialData.displayName`, case-insensitive) →
  project media assets by `display_name` (catalog first, live listing fallback). Unknown mention →
  exit 11 `ConfigurationError` listing available names; ambiguous → exit 11 with disambiguation
  (same contract as `character show --name`).
- **`@me`:** check `likeness:checkEligibility` and fail with a clear "avatar likeness is region-gated /
  not supported yet" hint (exit 11). Never silently drop.
- **Semantics:** each resolved mention stages the corresponding reference (entity-attach or UUID
  `--ref`), deduped against explicitly passed `--ref`/`--character` flags; model reference caps
  (`reference_cap_for`) enforced pre-submit. Prompt text keeps the name (minus `@`) so the model sees the
  sentence Flow's chip would render.
- **Provenance:** record resolved mentions in `metadata_json` (ids only, prompts redacted per
  `GFLOW_CLI_HISTORY_PROMPTS`); structlog event per resolution (`mention_resolved`,
  `mention_unresolved`).
- **MCP & CLI symmetry:** mention parsing lives in a shared service (`services/`), so the MCP generation
  tools get it for free; parity enforced by `tests/mcp/test_cli_parity.py` (AGENTS.md § MCP & CLI Schema
  Symmetry).
- Optional explicit form for scripts that don't want magic prompt parsing: `--tag NAME` (repeatable),
  semantically identical to an `@NAME` mention.

## 6. Spike plan (pre-implementation, ~1–2 Veo/Imagen credits)

`scripts/dev/spike_mention_capture.py`, run live on denon82 (same pattern as
`spike_movie_attach_payload.py`):

1. **T-1 (0 credits):** in a crowded project, focus the prompt box, real-keystroke `@` (per-char
   `keyboard.type`, not `insert_text`), dump the dropdown DOM (container role, tile addressing — is it
   `fe_id_<id>` again?, search filtering, asset-type sections). Repeat in the agentic cohort.
2. **T-2 (0 credits):** select a character mention, then dump the Slate document (chip node shape) —
   `document.querySelector('[data-slate-editor]')` innerHTML + Slate data attrs.
3. **T-3 (1 credit, image):** submit with one character mention; capture the POST body
   (`mentions_reference_entities` probe + full `requests[0]` keys). **Decides H1 vs H2** for the image
   path and closes the "image-path `referenceEntities` uncaptured" backlog item.
4. **T-4 (1 credit, video, optional if T-3 is conclusive):** same for `t2v`; diff against the known
   picker-attach body from the movie captures.
5. **T-5 (0 credits):** mention an *uploaded ingredient* (not a character); capture whether it lands in
   `referenceImages` and with which `imageUsageType`.

Exit criteria: H1/H2 verdict recorded here; dropdown selector table (locale-free tiers) appended if —
and only if — Option A is ever needed.

## 7. Risks & open questions

- **H2 positional parts** would force dropdown automation (Option A) — new selector surface on a
  keystroke-sensitive widget, both cohorts, plus locale variance. Mitigated by spike-first gating.
- **Name collisions / renames:** display names are not unique; Flow's dropdown disambiguates visually,
  a CLI cannot — exit-11 disambiguation (by id) is mandatory, and manifests should prefer ids.
- **Agentic cohort drift:** the agentic composer may render mentions differently or resolve them
  server-side; the #174 include-lands-but-never-submits A/B applies to any staged-reference path —
  reuse `ENTITY_ATTACH_DRIFT_HINT` backstops.
- **`@me`:** blocked on likeness eligibility (REGION) — ship detection + honest error only.
- **Prompt-expansion interaction:** `--tool creative-director` rewrites prompts — mentions must be
  extracted **before** expansion and re-validated after (the expander must not invent or destroy tags);
  simplest rule: resolve mentions first, pass the de-tagged text to the tool.
- **Movie style blocks / instructions cards:** `[style]` composition appends text — ensure composition
  never corrupts `@` tokens (escaping tests).

## 8. Phasing

| Phase | Deliverable | Gate |
|---|---|---|
| 0 | This recon doc | — |
| 1 | Capture spike (§6) → H1/H2 verdict recorded here | `/gflow:predict` on the chosen option |
| 2 | Mention resolver service + `video t2v/r2v` + `image i2i` inline mentions (Option B) + MCP parity + docs/tests | `/gflow:plan asset-tagging`, TDD, live e2e per verification-ledger norms |
| 3 | `movie` manifest mentions; `--tag` explicit form; close `video --character` backlog via the same resolver | live e2e |
| 4 (held) | `@me` likeness — revisit when `checkEligibility` stops returning `REGION` | — |
