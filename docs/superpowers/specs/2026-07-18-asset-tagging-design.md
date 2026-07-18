# Asset tagging (`@` mentions) — design spec

> **Status:** DRAFT — pre-implementation · **Date:** 2026-07-18 · **Council-reviewed:** 2026-07-18
> (internal 8-dimension council on PR #344; must-fixes folded in — see the PR thread for the verdict)
> **Research base:** [docs/ASSET_TAGGING_RECON.md](../../ASSET_TAGGING_RECON.md) (product research,
> wire hypothesis H1/H2, options analysis). This spec fixes the design for the recommended
> **Option B** (CLI-side mention resolver over existing attach primitives) and defines the contract
> the implementation plan ([plans/2026-07-18-asset-tagging/PLAN.md](../plans/2026-07-18-asset-tagging/PLAN.md))
> executes. Wire-dependent details are gated on the capture spike (§ 9).
> **Predict:** pending — run `/gflow:predict` on this spec before Task 3 of the plan.

## 1. Goal

Let users reference project assets by name directly inside prompt text — Flow's `@` tagging syntax —
on gflow generation commands:

```bash
gflow video t2v "@CaptainZoro walking through a rain-soaked neon city" --project <id>
gflow video r2v "@Zoro hands @Mika the sword" --project <id>   # character mentions (multi-ref, cap-checked)
gflow image i2i "@logo on a black tee, studio light" --project <id>   # character or media mentions
```

Each mention resolves to a project asset (CHARACTER entity or media/ingredient) and stages the
corresponding reference (`referenceEntities` / `referenceImages`) using attach machinery that is
already live-verified, so the same subject appears consistently across generations.

## 2. Scope by path (Phase 2)

| Path | Character mentions | Media/ingredient mentions |
|---|---|---|
| `video t2v` / `r2v` | ✅ — entity attach (movie-proven picker context-include) | ❌ Phase 3 — requires the missing r2v UUID-selection primitive ([recon § 2 item 2](../../ASSET_TAGGING_RECON.md)) |
| `image t2i` / `i2i` | ✅ — shipped `--reference-entity` staging (wire captured 2026-06-08, `api/image.py`) | ✅ — shipped UUID `--ref` selection (v0.26.0, zero re-upload) |

A mention whose resolved kind is unsupported on the invoked path → exit 11 with a message naming the
Phase-3 limitation. This keeps the Phase-2 promise honest: **no transport changes**.

## 3. Non-goals

- **`@me` avatar likeness** — region-gated (`likeness:checkEligibility` → `REGION`). Ship
  detection + an honest error only (exit 11). Revisit when eligibility opens.
- **`--tag NAME` explicit flag** — deferred to Phase 3 with the rest of the ergonomic extensions
  (recon § 8). Inline `@` syntax plus `@@` escaping covers scripting; a second spelling of the same
  feature doubles the CLI/MCP/parity surface for no named user.
- **Driving Flow's `@` dropdown UI** (Option A) — only if the spike proves H2 *and* positional
  semantics demonstrably change output.
- **Creating assets from mentions** — an unknown `@Name` never creates a character or uploads
  anything; it fails fast.
- **Cross-project resolution** — mentions resolve inside `--project` only.

## 4. User-visible behaviour (contract)

### 4.1 Mention grammar

- A mention starts at `@` **not preceded by a word character** (`\w`) and not part of `@@`. This one
  rule covers token-start and the email guard (`user@example.com` is never a mention).
- **Escape:** `@@` consumes both characters and emits a single literal `@`; scanning resumes *after*
  the escape, so `@@Zoro` is the literal text `@Zoro` — never a mention.
- **Candidate extraction vs matching (layering):** `parse_mentions(text)` is pure and index-free — it
  emits a `MentionToken` per mention site carrying the site's position and the **maximal candidate
  span** (text from `@` up to the next mention site or end-of-sentence punctuation). Name matching is
  the **resolver's** job: `resolve_mentions` greedily matches the longest prefix of the candidate span
  against the project's asset names (case-insensitive, **literal string comparison — never
  names-as-regex**), falling back to the single `\w[\w-]*` token. `"@Captain Zoro walks"` matches
  asset `Captain Zoro` if it exists, else asset `Captain`. Names-with-spaces tests therefore live in
  the resolver suite, not the parser suite.
- **De-tagging:** the submitted prompt text replaces the matched span with the **canonical stored
  asset name** (so `@zoro` submits as `Zoro`), matching what Flow's chip renders. Unmatched text after
  the name is untouched.

### 4.2 Resolution order & failure modes

1. CHARACTER entities in the project (`flow.projectInitialData` → `entityInfo.displayName`).
2. Project media assets by display name (local catalog `display_name` first, live project media
   listing as fallback).

Entities are matched first, so on a **cross-kind exact-name tie the entity wins**; the exit-0 result
still logs the shadowed media asset's id (`mention_resolved` includes a `shadowed` field) so the
behaviour is observable. Same-kind ties are ambiguous.

| Case | Behaviour |
|---|---|
| Unique match (entity) | Stage entity reference (same wire outcome as movie entity-attach / image `--reference-entity`) |
| Unique match (media) | Stage image reference (image path: same wire outcome as UUID `--ref`) |
| Media match on a video command | Exit 11 — "media mentions on the video path are Phase 3" |
| `@me` | Exit 11 `ConfigurationError` — "avatar likeness is region-gated / not supported yet" |
| No match | Exit 11 — list available asset names for the project (names only; control characters stripped — § 8) |
| Ambiguous (≥2 same-kind same-name) | Exit 11 — disambiguation listing ids, mirroring `character show --name` |
| Over model reference cap | Exit 11 pre-submit, citing `reference_cap_for(model)` — no credits spent |

All failures are **pre-submit** (fail-fast before any credited generation), consistent with the
credit-safety mandate.

### 4.3 Interaction with existing flags & tools

- Mentions **dedupe** against explicit flags that stage the same asset — `--ref` (both paths) and
  `--reference-entity` (image path): one staged reference per asset, however it was named.
- `--tool` prompt expansion (e.g. `creative-director`): mentions are **extracted and resolved
  before** expansion; the tool receives the de-tagged text; resolved references are unaffected by
  the rewrite. The expander can neither invent nor destroy a tag.
- Movie manifests (Phase 3): scene `prompt` strings may carry mentions; resolution happens at
  scene-generation time against the run's project.

### 4.4 Observability & provenance

- structlog events: `mention_resolved` (kind + id + optional `shadowed`; **name included only when
  history mode is not redacted** — § 6) and `mention_unresolved`. Ambiguity and dedupe outcomes
  surface through the exit-11 problem details and the staged-reference count respectively — no
  dedicated events until something consumes them.
- Catalog `metadata_json` records resolved mentions as `{name, kind, id}` (ids always; name subject
  to § 6 redaction).

## 5. Architecture

```
cli_video.py / cli_image.py                       (thin: pass prompt + project through)
        │
services/mentions.py                              (NEW — parsing pure; resolution over a prefetched index)
  parse_mentions(text)      -> [MentionToken]     # § 4.1 candidate extraction, I/O-free
  resolve_mentions(tokens, index) -> ResolvedMentions | typed errors
  AssetIndex                                       # built from projectInitialData + catalog rows
        │
existing attach primitives (UNCHANGED)
  entity  -> video: picker context-include (fe_id_<entityId>, PICKER_CONTEXT_INCLUDE)
             image: --reference-entity staging (shipped, wire captured 2026-06-08)
  media   -> image: UUID --ref selection (shipped v0.26.0)          [video-path media: Phase 3]
```

Key decisions:

- **Parsing is pure and I/O-free**; resolution takes a pre-fetched `AssetIndex` so tests never need
  a transport. The parser/resolver split follows § 4.1's layering exactly.
- **No transport changes in Phase 2** — guaranteed by the § 2 scope table.
- **MCP parity by construction:** the resolver lives in `services/`, and MCP generation tools call
  the same service (`tests/mcp/test_cli_parity.py` enforces).

## 6. Error taxonomy & data layer

All mention failures map to existing `ConfigurationError` (exit 11) with problem-details `detail`
naming the mention and the candidates. No new exit codes. Transport-level attach failures keep their
existing classes/hints (`ENTITY_ATTACH_DRIFT_HINT`, #174 backstops).

No schema migration. `metadata_json` gains an optional `mentions` array (additive). Redaction: under
`GFLOW_CLI_HISTORY_PROMPTS=redacted`, mention **names are hashed** exactly like prompt fields — in
`metadata_json` **and** in structlog events; ids are kept in both.

## 7. Security & privacy

- No new secrets, no new endpoints, no signed URLs persisted.
- **Resolved ids are validated against the strict media-UUID / entity-id pattern before reaching any
  selector or wire field** (the `fe_id_{entity_id}` locator interpolates the id — validate first).
- **Names are data, never patterns or code:** matching is literal string comparison (no
  names-as-regex — metacharacter names must be inert); names are never interpolated into CSS
  selectors or shell.
- **Terminal output sanitization:** asset names echoed in error listings are stripped of
  ANSI/control characters (display names in shared projects are semi-untrusted).
- `@me` fails closed.

## 8. Testing strategy

- **Unit (red-first):** parser table tests (token-start/`\w` guard, `@@` escape incl. `@@Zoro`,
  candidate-span extraction, unicode names); resolver tests (longest-match with spaces, unique /
  none / ambiguous / cross-kind shadow / cap / dedupe / `@me` / path-scope refusal, **names
  containing regex metacharacters and ANSI sequences**); event + redaction tests (`mention_resolved`
  name-hashing under redacted mode, `metadata_json.mentions` recorder row); resolve-before-expand
  ordering with `--tool`.
- **BDD:** scenarios for the § 4.2 table — Critical: unresolved-mention fail-fast pre-credit,
  ambiguous disambiguation, cap enforcement; High: `@me` refusal, video-path media refusal,
  happy-path entity + media staging.
- **MCP parity:** parity + handler test with a mention-bearing prompt.
- **Live e2e (verification-ledger norms):** video generation with a character mention proving the
  `videoGenerationEntityInputs[].entityId` echo (`_assert_entities_attached`); image generation with
  a character mention proving the `requests[].referenceEntities[].entityId` echo
  (`_assert_image_entities_attached`); image generation with a media mention proving zero re-upload.
  A multi-mention video run (the § 1 two-character example) closes the ledger.

## 9. Spike gate (must complete before implementation Tasks 3+)

`scripts/dev/spike_mention_capture.py` per [ASSET_TAGGING_RECON § 6](../../ASSET_TAGGING_RECON.md)
(T-1…T-5, ≤2 credits): dropdown DOM dump and mention-submit POST captures on image + video paths,
diffed against the already-captured `--reference-entity` / picker-attach bodies.
**Exit criterion: a verdict recorded in the recon doc — H1, H2, or INCONCLUSIVE (per-path if
mixed).** H1 → this spec proceeds unchanged. H2, INCONCLUSIVE, or mixed → STOP and re-run
`/gflow:predict` with the evidence before any implementation task.

## 10. Rollout & docs

- `USAGE.md`: mention syntax on each supported command + escaping rules + the § 2 scope table;
  `USER_GUIDE` journey snippet for character-consistent multi-clip work.
- `CHARACTER.md`: close the "video --character not implemented" backlog rows by pointing at the
  mention path (the stale "image-path uncaptured" bullet is already corrected in this PR).
- `CHANGELOG.md` `[Unreleased]` entry; recon doc status flipped to VERIFIED-with-capture.
