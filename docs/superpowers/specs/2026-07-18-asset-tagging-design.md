# Asset tagging (`@` mentions) — design spec

> **Status:** DRAFT — pre-implementation · **Date:** 2026-07-18
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
gflow video r2v "@Zoro hands @Mika the sword" --project <id>
gflow image i2i "@logo on a black tee, studio light" --project <id>
```

Each mention resolves to a project asset (CHARACTER entity or media/ingredient) and stages the
corresponding reference (`referenceEntities` / `referenceImages`) using the attach machinery that is
already live-verified, so the same subject appears consistently across generations.

## 2. Non-goals

- **`@me` avatar likeness** — region-gated (`likeness:checkEligibility` → `REGION`). Ship
  detection + an honest error only (exit 11). Revisit when eligibility opens.
- **Driving Flow's `@` dropdown UI** (Option A) — only if the spike proves H2 *and* positional
  semantics demonstrably change output. Out of scope for this spec.
- **Creating assets from mentions** — an unknown `@Name` never creates a character or uploads
  anything; it fails fast.
- **Cross-project resolution** — mentions resolve inside `--project` only.

## 3. User-visible behaviour (contract)

### 3.1 Mention grammar

- A mention is `@` at **token start** (start-of-string or after whitespace/punctuation other than a
  word character) followed by an asset name.
- **Longest-match resolution:** names may contain spaces. The resolver tries the longest
  greedy match against the project's asset names (case-insensitive), then falls back to the single
  `\w[\w-]*` token. `"@Captain Zoro walks"` matches asset `Captain Zoro` if it exists, else asset
  `Captain`.
- **Escapes:** `@@` produces a literal `@` in the submitted prompt and is never a mention.
  Email-like tokens (`user@example.com` — `@` preceded by a word character) are never mentions.
- **De-tagging:** the submitted prompt text keeps the asset name, minus the `@` (matching what
  Flow's chip renders as text). `"@Zoro runs"` submits as `"Zoro runs"` plus a staged reference.

### 3.2 Resolution order & failure modes

1. CHARACTER entities in the project (`flow.projectInitialData` → `entityInfo.displayName`).
2. Project media assets by display name (local catalog `display_name` first, live project media
   listing as fallback).

| Case | Behaviour |
|---|---|
| Unique match (entity) | Stage entity reference (same wire outcome as movie entity-attach) |
| Unique match (media) | Stage image reference (same wire outcome as `--ref <uuid>`) |
| `@me` | Exit 11 `ConfigurationError` — "avatar likeness is region-gated / not supported yet" |
| No match | Exit 11 — list available asset names for the project (names only, no ids required) |
| Ambiguous (≥2 same-name) | Exit 11 — disambiguation listing ids, mirroring `character show --name` |
| Over model reference cap | Exit 11 pre-submit, citing `reference_cap_for(model)` — no credits spent |

All failures are **pre-submit** (fail-fast before any credited generation), consistent with the
credit-safety mandate.

### 3.3 Interaction with existing flags & tools

- Mentions **dedupe** against explicit `--ref`/`--character` values targeting the same asset — one
  staged reference per asset, however it was named.
- `--tag NAME` (repeatable) is the explicit, non-magic equivalent of an `@NAME` mention for
  scripting; identical resolution and staging path.
- `--tool` prompt expansion (e.g. `creative-director`): mentions are **extracted and resolved
  before** expansion; the tool receives the de-tagged text; resolved references are unaffected by
  the rewrite. The expander can neither invent nor destroy a tag.
- Movie manifests (Phase 3 of the plan): scene `prompt` strings may carry mentions; resolution
  happens at scene-generation time against the run's project.

### 3.4 Observability & provenance

- structlog events: `mention_resolved` (name → kind + id), `mention_ambiguous`,
  `mention_unresolved`, `mention_deduped`.
- Catalog `metadata_json` records resolved mentions as `{name, kind, id}` (ids only; prompt text
  redaction continues to follow `GFLOW_CLI_HISTORY_PROMPTS`).

## 4. Architecture

```
cli_video.py / cli_image.py / cli_movie.py        (thin: pass prompt + project through)
        │
services/mentions.py                              (NEW — pure logic, no I/O in parsing)
  parse_mentions(text)      -> [MentionToken]     # grammar only, unit-testable
  resolve_mentions(tokens, assets) -> ResolvedMentions | typed errors
  AssetIndex                                       # built from projectInitialData + catalog rows
        │
existing attach primitives (UNCHANGED)
  entity  -> picker context-include (fe_id_<entityId>, PICKER_CONTEXT_INCLUDE)  [video]
  media   -> UUID reference selection (r2v --ref path)                          [video/image]
```

Key decisions:

- **Parsing is pure and I/O-free** (`parse_mentions`) so the grammar is exhaustively unit-testable;
  resolution takes a pre-fetched `AssetIndex` so tests never need a transport.
- **No transport changes** for Phase 2. Staging reuses `fe_id_<entityId>` context-include and the
  UUID `--ref` path exactly as shipped; the mention layer only *feeds* them.
- **MCP parity by construction:** the resolver lives in `services/`, and MCP generation tools call
  the same service. CLI flag additions (`--tag`) are mirrored in MCP schemas
  (`tests/mcp/test_cli_parity.py` enforces).
- **Image-path entity references** are gated on the spike: if the image endpoint's
  `referenceEntities` shape is not confirmed, Phase 2 ships image-path mentions for **media assets
  only** and character mentions on the video path, with a clear exit-11 message for the unsupported
  combination.

## 5. Error taxonomy

All mention failures map to existing `ConfigurationError` (exit 11) with problem-details `detail`
naming the mention and the candidates. No new exit codes. Transport-level attach failures keep
their existing classes/hints (`ENTITY_ATTACH_DRIFT_HINT`, #174 backstops).

## 6. Data layer

No schema migration. `metadata_json` gains an optional `mentions` array (additive, readers ignore
unknown keys). Redaction: names may be user-PII-adjacent — under
`GFLOW_CLI_HISTORY_PROMPTS=redacted`, mention **names** are hashed like prompt fields; ids are kept.

## 7. Security & privacy

- No new secrets, no new endpoints, no signed URLs persisted.
- Mention resolution adds no shell/subprocess surface; names are never interpolated into shell or
  selectors as raw strings (tile addressing stays id-based: `fe_id_<entityId>`).
- `@me` fails closed.

## 8. Testing strategy

- **Unit (red-first):** grammar table tests (escapes, email guard, longest match, unicode names,
  `@@`), resolver tests (unique/none/ambiguous/cap/dedupe), tool-expansion ordering.
- **BDD:** scenarios for the § 3.2 table (Critical: unresolved-mention fail-fast pre-credit;
  ambiguous disambiguation; cap enforcement).
- **MCP parity:** `--tag` mirrored; parity test green.
- **Live e2e (verification-ledger norms):** one video generation with a character mention proving
  `videoGenerationEntityInputs[].entityId` echo (reuse `_assert_entities_attached`), one image
  generation with a media mention proving zero re-upload — both on the standing live account,
  credits recorded.

## 9. Spike gate (must complete before implementation Tasks 3+)

`scripts/dev/spike_mention_capture.py` per [ASSET_TAGGING_RECON § 6](../../ASSET_TAGGING_RECON.md)
(T-1…T-5, ≤2 credits): dropdown DOM dump, Slate chip node shape, mention-submit POST capture on
image + video paths. **Exit criterion: an H1/H2 verdict recorded in the recon doc.**
H1 → this spec proceeds unchanged. H2 → STOP and re-run `/gflow:predict` with the positional-parts
evidence before any implementation task.

## 10. Rollout & docs

- `USAGE.md`: mention syntax on each supported command + escaping rules; `USER_GUIDE` journey
  snippet for character-consistent multi-clip work.
- `CHARACTER.md`: close the "video --character not implemented" backlog rows by pointing at the
  mention/`--tag` path.
- `CHANGELOG.md` `[Unreleased]` entry; recon doc status flipped to VERIFIED-with-capture.
