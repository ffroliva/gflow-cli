# Asset Tagging (`@` Mentions) Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature asset-tagging` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Users can reference project assets by name inside prompt text (`@CaptainZoro`, `@logo`)
on `gflow video t2v/r2v` (character mentions) and `gflow image t2i/i2i` (character + media
mentions), with each mention staging the corresponding reference through the existing
live-verified attach machinery.

**Architecture:** New `services/mentions.py` (pure candidate parser + index-based resolver);
CLI/MCP layers pass prompts through it; staging reuses the shipped entity context-include
(video), `--reference-entity` staging (image), and UUID `--ref` selection (image) — **no transport
changes in Phase 2** (video-path media mentions are Phase 3: they need a new r2v UUID-selection
primitive). Full contract:
[specs/2026-07-18-asset-tagging-design.md](../../specs/2026-07-18-asset-tagging-design.md);
research: [docs/ASSET_TAGGING_RECON.md](../../../ASSET_TAGGING_RECON.md).

**Predict verdict:** pending — run `/gflow:predict` on the spec after the Task 2 spike verdict,
before starting Task 3.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | Wire serialization is H2 (positional parts) or spike INCONCLUSIVE → Option B loses semantics | Task 1–2 spike gates all implementation; H2 / INCONCLUSIVE / mixed → STOP + re-predict |
| Medium | Name collisions / localized display names | Exit-11 disambiguation with ids; case-insensitive literal match; entity-shadows-media rule logged |
| Medium | `--tool` prompt expansion corrupts tags | Resolve-before-expand ordering, locked by unit test |
| Medium | Agentic-cohort A/B (#174 include-lands-but-never-submits) | Reuse `ENTITY_ATTACH_DRIFT_HINT` backstops; agentic-cohort live run in Task 9 |
| Medium | Malicious/odd display names (regex metacharacters, ANSI) | Literal matching + id-pattern validation + output sanitization (spec § 7), unit-locked |
| Low | `@@`/email false positives | Grammar table tests (Task 3) |

---

## File structure

### New files
```
scripts/dev/spike_mention_capture.py
  Live capture spike (T-1..T-5): dropdown DOM, mention-submit POST bodies, ingredient capture
src/gflow_cli/services/mentions.py
  parse_mentions / resolve_mentions / AssetIndex / MentionToken / ResolvedMentions
tests/services/test_mentions_parse.py
  Parser table tests (token-start \w guard, @@ escape, candidate spans, unicode)
tests/services/test_mentions_resolve.py
  Resolver tests (longest match / unique / none / ambiguous / shadow / cap / dedupe / @me /
  path scope / metacharacter + ANSI names)
tests/services/test_mentions_observability.py
  Event emission + redacted-mode name hashing + metadata_json.mentions recorder row
tests/features/asset_tagging.feature + tests/features/test_asset_tagging_steps.py
  BDD scenarios for the spec § 4.2 behaviour table
```

### Modified files
```
src/gflow_cli/cli_video.py, src/gflow_cli/cli_image.py
  Mention resolution pre-submit; dedupe with --ref / --reference-entity
src/gflow_cli/mcp/ (tool schemas + handlers)
  Mirror mention behaviour (parity contract)
src/gflow_cli/services/ (generation orchestration touchpoints)
  Resolve-before-expand ordering with --tool
docs/ASSET_TAGGING_RECON.md
  Spike verdict + captured payloads (Task 2)
docs/USAGE.md, docs/USER_GUIDE.md, docs/CHARACTER.md, CHANGELOG.md
  Syntax reference, journey snippet, backlog closure, changelog entry
```

---

## Task 1 — Capture spike script

**What:** Commit `scripts/dev/spike_mention_capture.py` implementing recon § 6 T-1…T-5 against a
live authenticated profile (dropdown DOM dump on real `@` keystrokes; mention-submit POST capture
on image + video, diffed against the already-captured `--reference-entity` and picker-attach
bodies; ingredient-mention capture).

**Files:**
- `scripts/dev/spike_mention_capture.py` — reuses the passive-capture harness patterns from
  `spike_movie_attach_payload.py`; per-char `keyboard.type` around `@` (never `insert_text`).

**Steps:**
- [ ] T-1 dropdown probe (0 credits) — classic cohort (agentic repeat only if H2)
- [ ] T-3 image mention submit capture + `--reference-entity` body diff (1 credit)
- [ ] T-4 video mention submit capture + picker-attach body diff (1 credit, skip if T-3 conclusive)
- [ ] T-5 ingredient mention capture (0 credits)
- [ ] T-2 Slate chip dump — only if T-3/T-4 ambiguous

**Tests created (red):** none — diagnostic script (script-lint gates only).

---

## Task 2 — Record the spike verdict (GATE)

**What:** Run the spike on the live account; append captured payloads + the verdict to
`docs/ASSET_TAGGING_RECON.md`. The verdict is one of **H1 / H2 / INCONCLUSIVE** (recorded per-path
if results are mixed). **H2, INCONCLUSIVE, or mixed → STOP: re-run `/gflow:predict` with the
evidence before Task 3.** Only a clean H1 proceeds directly.

**Files:**
- `docs/ASSET_TAGGING_RECON.md` — § 3 flips from HYPOTHESIS to a recorded verdict; § 6 exit
  criteria filled.

**Steps:**
- [ ] Spike run on live profile (≤2 credits, recorded)
- [ ] Verdict + payload excerpts committed (no signed URLs, no cookies)
- [ ] `/gflow:predict` run on the spec; verdict recorded in spec + this plan header

**Tests created (red):** none — docs/evidence task.

---

## Task 3 — Unit test scaffold (red)

**What:** Red tests for the parser, resolver, and observability contracts, straight from spec
§ 4.1–4.4 and § 6–7. No production code.

**Files:**
- `tests/services/test_mentions_parse.py` — parser table (grammar only, no index)
- `tests/services/test_mentions_resolve.py` — resolution semantics (index-driven)
- `tests/services/test_mentions_observability.py` — events + redaction + provenance

**Steps:**
- [ ] Parse table: `\w`-guard token-start rule, `@@` escape (incl. `@@Zoro` → literal, scanning
      resumes after), email guard, candidate-span extraction, unicode/accented names
- [ ] Resolve: longest-match with spaces (canonical-name de-tag casing), unique entity / unique
      media / no match / cross-kind shadow (entity wins, `shadowed` logged) / same-kind ambiguous /
      `@me` / cap breach / dedupe vs `--ref` and `--reference-entity` / video-path media refusal —
      each asserting the typed error + message content
- [ ] Hostile names: regex-metacharacter names match literally; ANSI/control chars stripped from
      error listings; resolved ids validated against the strict id pattern
- [ ] Observability: `mention_resolved` / `mention_unresolved` emission; name hashed in events +
      `metadata_json.mentions` under `GFLOW_CLI_HISTORY_PROMPTS=redacted` (ids kept)
- [ ] Resolve-before-expand ordering with a stub `--tool` expander

**Tests created (red):**
- [ ] `test_parse_*` (≈10 table cases) — grammar contract
- [ ] `test_resolve_*` (≈12 cases) — resolution + error taxonomy (exit-11 mapping) + hostile names
- [ ] `test_observability_*` (≈4 cases) — events, redaction, provenance row, expander ordering

---

## Task 4 — BDD scaffold (red)

**What:** Red BDD scenarios for the user-visible contract (spec § 4.2 table): Critical —
unresolved mention fails pre-credit, ambiguous disambiguation lists ids, cap enforcement
pre-submit; High — `@me` refusal, video-path media refusal, happy-path entity + media staging.

**Files:**
- `tests/features/asset_tagging.feature`, `tests/features/test_asset_tagging_steps.py`
  (stubs mirror runtime signatures — `[[bdd-stubs-mirror-runtime-signatures]]`)

**Steps:**
- [ ] Feature file with the 3 Critical + 3 High scenarios above
- [ ] Step stubs wired to fakes (no browser)

**Tests created (red):**
- [ ] `Scenario: unresolved mention aborts before any credited generation`
- [ ] `Scenario: ambiguous mention lists candidate ids`
- [ ] `Scenario: mention count over model reference cap fails pre-submit`
- [ ] `Scenario: @me is refused with the region-gating hint`
- [ ] `Scenario: media mention on a video command is refused as Phase 3`
- [ ] `Scenario: entity and media mentions stage references on the image path`

---

## Task 5 — Core implementation: `services/mentions.py`

**What:** Make Tasks 3–4 green. Pure `parse_mentions`; `AssetIndex` built from
`projectInitialData` entities + catalog media rows; `resolve_mentions` returning
staged-reference descriptors (entity vs media) + de-tagged prompt; events; redaction; id-pattern
validation; output sanitization.

**Files:**
- `src/gflow_cli/services/mentions.py` — all logic
- (touch) `src/gflow_cli/data/` read helpers only if an existing query is missing — no schema change

**Steps:**
- [ ] Parser green (no I/O)
- [ ] Resolver green incl. shadow rule, dedupe, cap check via `reference_cap_for`, path scoping
- [ ] Observability suite green (events, redaction, provenance)
- [ ] `pyright` strict clean

**Tests created (red→green):** Tasks 3–4 suites pass.

---

## Task 6 — CLI wiring: `video t2v/r2v`, `image t2i/i2i`

**What:** Resolve mentions pre-submit in the affected commands; stage entity references through
the movie-proven context-include path (video) and the shipped `--reference-entity` staging
(image); stage media references through the shipped UUID `--ref` path (image); enforce
resolve-before-`--tool`-expansion ordering; per-path scope refusals per spec § 2.

**Files:**
- `src/gflow_cli/cli_video.py`, `src/gflow_cli/cli_image.py`, generation service touchpoints

**Steps:**
- [ ] `t2v`/`r2v` character-mention staging + cap/dedupe integration
- [ ] `t2i`/`i2i` character + media mention staging
- [ ] Video-path media refusal (exit 11, Phase-3 message)
- [ ] Help text updated (mention syntax note; golden `--help` snapshots updated)
- [ ] Resolve-before-expand ordering green at the CLI level

**Tests created (red first, in-task):**
- [ ] CLI-level tests per command (mocked transport) asserting staged references + exit codes

---

## Task 7 — MCP parity

**What:** Mirror mention behaviour in the MCP generation tools (shared service — thin adapters),
update schema descriptions, keep `tests/mcp/test_cli_parity.py` green.

**Files:**
- `src/gflow_cli/mcp/` — tool schemas/handlers

**Steps:**
- [ ] Schemas updated; parity test green
- [ ] MCP handler test with a mention-bearing prompt

**Tests created:** parity + handler tests.

---

## Task 8 — Docs + CHANGELOG

**What:** User-facing syntax reference and cross-doc closure.

**Files:**
- `docs/USAGE.md` (syntax + escaping + the per-path scope table), `docs/USER_GUIDE.md` (journey
  snippet), `docs/CHARACTER.md` (`video --character` backlog rows → shipped-via-mentions),
  `docs/INDEX.md` shortcuts, `CHANGELOG.md` `[Unreleased]`

**Steps:**
- [ ] All docs updated; `check_doc_links` green
- [ ] Recon doc status header updated

**Tests created:** none — docs gates.

---

## Task 9 — Full gates + live verification

**What:** `/gflow:check` green across the tree; live e2e per verification-ledger norms.

**Files:**
- `docs/LIVE_VERIFICATION_v<next>.md` — evidence ledger entry
- `KNOWN_ISSUES.md` — only if a cohort-specific behaviour surfaces

**Steps:**
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥80%)
- [ ] Live e2e: video character mention → `videoGenerationEntityInputs[].entityId` echo via
      `_assert_entities_attached`
- [ ] Live e2e: image character mention → `requests[].referenceEntities[].entityId` echo via
      `_assert_image_entities_attached`
- [ ] Live e2e: image media mention → zero re-upload events
- [ ] Live e2e: multi-mention video run (the spec § 1 two-character example)
- [ ] Agentic-cohort run of one of the above (if the cohort is available on the account)
- [ ] Evidence recorded (structlog events + wire echo + file magic/dims)

**Tests created:** none — evidence task.

---

## Phase 3 (follow-up plan, out of this plan's scope)

Movie-manifest mentions; `--tag NAME` explicit flag; **video-path media mentions** (build the r2v
UUID-selection primitive); `@me` eligibility re-check; Option A dropdown automation **only** if H2
was proven and output differences are demonstrated.

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Docs updated (`USAGE.md` / `USER_GUIDE.md` / `CHARACTER.md` / recon doc)
- [ ] BDD feature file covers all Critical + High scenarios (the six named in Task 4)
- [ ] MCP ↔ CLI parity test green
- [ ] Live e2e evidence recorded per verification-ledger norms
- [ ] No `# TODO` in diff without a tracked issue link
