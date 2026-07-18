# Asset Tagging (`@` Mentions) Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature asset-tagging` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Users can reference project assets by name inside prompt text (`@CaptainZoro`, `@logo`)
on `gflow video t2v/r2v` and `gflow image i2i`, with each mention staging the corresponding
character/image reference through the existing live-verified attach machinery.

**Architecture:** New pure-logic `services/mentions.py` (grammar parser + resolver over a
pre-fetched asset index); CLI/MCP layers pass prompts through it; staging reuses the shipped
entity context-include and UUID `--ref` primitives — **no transport changes in Phase 2**. Full
contract: [specs/2026-07-18-asset-tagging-design.md](../../specs/2026-07-18-asset-tagging-design.md);
research: [docs/ASSET_TAGGING_RECON.md](../../../ASSET_TAGGING_RECON.md).

**Predict verdict:** pending — run `/gflow:predict` on the spec after the Task 2 spike verdict,
before starting Task 3.

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | Wire serialization is H2 (positional parts) → Option B loses semantics | Task 1–2 spike gates all implementation; H2 → STOP + re-predict |
| High | Image-path `referenceEntities` shape unconfirmed | Spike T-3 captures it; fallback scope = media-only mentions on image path |
| Medium | Name collisions / localized display names | Exit-11 disambiguation with ids; case-insensitive match; ids preferred in manifests |
| Medium | `--tool` prompt expansion corrupts tags | Resolve-before-expand ordering, locked by unit test |
| Medium | Agentic-cohort A/B (#174 include-lands-but-never-submits) | Reuse `ENTITY_ATTACH_DRIFT_HINT` backstops; live e2e on both cohorts if available |
| Low | `@@`/email false positives | Grammar table tests (Task 3) |

---

## File structure

### New files
```
scripts/dev/spike_mention_capture.py
  Live capture spike (T-1..T-5): dropdown DOM, Slate chip node, mention-submit POST bodies
src/gflow_cli/services/mentions.py
  parse_mentions / resolve_mentions / AssetIndex / MentionToken / ResolvedMentions
tests/services/test_mentions_parse.py
  Grammar table tests (escapes, email guard, longest match, unicode)
tests/services/test_mentions_resolve.py
  Resolver tests (unique / none / ambiguous / cap / dedupe / @me)
tests/features/asset_tagging.feature + tests/features/test_asset_tagging_steps.py
  BDD scenarios for the spec § 3.2 behaviour table
```

### Modified files
```
src/gflow_cli/cli_video.py, src/gflow_cli/cli_image.py
  Mention resolution pre-submit; --tag NAME (repeatable); dedupe with --ref/--character
src/gflow_cli/mcp/ (tool schemas + handlers)
  Mirror --tag + mention behaviour (parity contract)
src/gflow_cli/services/ (generation orchestration touchpoints)
  Resolve-before-expand ordering with --tool
docs/ASSET_TAGGING_RECON.md
  H1/H2 verdict + captured payloads (Task 2)
docs/USAGE.md, docs/USER_GUIDE.md, docs/CHARACTER.md, CHANGELOG.md
  Syntax reference, journey snippet, backlog closure, changelog entry
```

---

## Task 1 — Capture spike script

**What:** Commit `scripts/dev/spike_mention_capture.py` implementing recon § 6 T-1…T-5 against a
live authenticated profile (dropdown DOM dump on real `@` keystrokes, Slate chip node dump,
mention-submit POST capture on image + video, ingredient-mention capture).

**Files:**
- `scripts/dev/spike_mention_capture.py` — reuses the passive-capture harness patterns from
  `spike_movie_attach_payload.py`; per-char `keyboard.type` around `@` (never `insert_text`).

**Steps:**
- [ ] T-1 dropdown probe (0 credits) — classic + agentic cohorts
- [ ] T-2 chip node dump (0 credits)
- [ ] T-3 image mention submit capture (1 credit)
- [ ] T-4 video mention submit capture (1 credit, skip if T-3 conclusive)
- [ ] T-5 ingredient mention capture (0 credits)

**Tests created (red):** none — diagnostic script (script-lint gates only).

---

## Task 2 — Record the H1/H2 verdict (GATE)

**What:** Run the spike on the live account; append captured payloads + the H1/H2 verdict to
`docs/ASSET_TAGGING_RECON.md`. **H2 → STOP: re-run `/gflow:predict` before Task 3.** Also record
whether the image endpoint carries `referenceEntities` (sets Task 5 scope).

**Files:**
- `docs/ASSET_TAGGING_RECON.md` — § 3 flips from HYPOTHESIS to VERIFIED; § 6 exit criteria filled.

**Steps:**
- [ ] Spike run on live profile (≤2 credits, recorded)
- [ ] Verdict + payload excerpts committed (no signed URLs, no cookies)
- [ ] `/gflow:predict` run on the spec; verdict recorded in spec + this plan header

**Tests created (red):** none — docs/evidence task.

---

## Task 3 — Unit test scaffold (red)

**What:** Red tests for the grammar and resolver, straight from spec § 3.1–3.3. No production code.

**Files:**
- `tests/services/test_mentions_parse.py` — grammar table
- `tests/services/test_mentions_resolve.py` — resolution semantics

**Steps:**
- [ ] Parse table: token-start rule, `@@` escape, email guard, longest-match with spaces,
      unicode/accented names, de-tagged text output
- [ ] Resolve: unique entity / unique media / no match / ambiguous / `@me` / cap breach / dedupe
      vs explicit flags — each asserting the typed error + message content

**Tests created (red):**
- [ ] `test_parse_*` (≈10 table cases) — grammar contract
- [ ] `test_resolve_*` (≈8 cases) — resolution + error taxonomy (exit-11 mapping)

---

## Task 4 — BDD scaffold (red)

**What:** Red BDD scenarios for the user-visible contract (spec § 3.2 table), Critical scenarios
first: unresolved mention fails pre-credit, ambiguous disambiguation lists ids, cap enforcement
pre-submit.

**Files:**
- `tests/features/asset_tagging.feature`, `tests/features/test_asset_tagging_steps.py`
  (stubs mirror runtime signatures — `[[bdd-stubs-mirror-runtime-signatures]]`)

**Steps:**
- [ ] Feature file with Critical + High scenarios
- [ ] Step stubs wired to fakes (no browser)

**Tests created (red):**
- [ ] `Scenario: unresolved mention aborts before any credited generation`
- [ ] `Scenario: ambiguous mention lists candidate ids`
- [ ] `Scenario: mention count over model reference cap fails pre-submit`

---

## Task 5 — Core implementation: `services/mentions.py`

**What:** Make Tasks 3–4 green. Pure `parse_mentions`; `AssetIndex` built from
`projectInitialData` entities + catalog media rows; `resolve_mentions` returning staged-reference
descriptors (entity vs media) + de-tagged prompt; structlog events; redaction of mention names
under redacted history mode.

**Files:**
- `src/gflow_cli/services/mentions.py` — all logic
- (touch) `src/gflow_cli/data/` read helpers only if an existing query is missing — no schema change

**Steps:**
- [ ] Grammar green (no I/O in parser)
- [ ] Resolver green incl. dedupe + cap check via `reference_cap_for`
- [ ] Events + redaction green
- [ ] `pyright` strict clean

**Tests created (red→green):** Tasks 3–4 suites pass.

---

## Task 6 — CLI wiring: `video t2v/r2v`, `image i2i`, `--tag`

**What:** Resolve mentions pre-submit in the affected commands; stage entity references through
the movie-proven context-include path and media references through the UUID `--ref` path; add
repeatable `--tag NAME` as the explicit form; enforce resolve-before-`--tool`-expansion ordering.
Scope per Task 2: if image-path `referenceEntities` unconfirmed, character mentions on `image` exit
11 with a clear message.

**Files:**
- `src/gflow_cli/cli_video.py`, `src/gflow_cli/cli_image.py`, generation service touchpoints

**Steps:**
- [ ] `t2v`/`r2v` mention staging + cap/dedupe integration
- [ ] `i2i` media-mention staging (+ entity if confirmed)
- [ ] `--tag` flag (help text, kebab-case, golden `--help` snapshots updated)
- [ ] Resolve-before-expand ordering test green

**Tests created (red first, in-task):**
- [ ] CLI-level tests per command (mocked transport) asserting staged references + exit codes

---

## Task 7 — MCP parity

**What:** Mirror mention behaviour + `--tag` in the MCP generation tools (shared service — thin
adapters), update schemas, keep `tests/mcp/test_cli_parity.py` green.

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
- `docs/USAGE.md` (syntax + escaping per command), `docs/USER_GUIDE.md` (journey snippet),
  `docs/CHARACTER.md` (backlog rows → shipped-via-mentions), `docs/INDEX.md` shortcuts,
  `CHANGELOG.md` `[Unreleased]`

**Steps:**
- [ ] All docs updated; `check_doc_links` green
- [ ] Recon doc status header updated

**Tests created:** none — docs gates.

---

## Task 9 — Full gates + live verification

**What:** `/gflow:check` green across the tree; live e2e per verification-ledger norms — one video
generation with a character mention (assert `videoGenerationEntityInputs[].entityId` echo via
`_assert_entities_attached`), one image generation with a media mention (assert zero re-upload
events); credits + evidence recorded in the release's LIVE_VERIFICATION doc.

**Steps:**
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥80%)
- [ ] Live e2e evidence recorded (structlog events + wire echo + file magic/dims)
- [ ] `KNOWN_ISSUES.md` updated if any cohort-specific behaviour surfaced

---

## Phase 3 (follow-up plan, out of this plan's scope)

Movie-manifest mentions; `@me` eligibility re-check; Option A dropdown automation **only** if H2
was proven and output differences are demonstrated.

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Docs updated (`USAGE.md` / `USER_GUIDE.md` / `CHARACTER.md` / recon doc)
- [ ] BDD feature file covers all Critical + High scenarios
- [ ] MCP ↔ CLI parity test green
- [ ] Live e2e evidence recorded per verification-ledger norms
- [ ] No `# TODO` in diff without a tracked issue link
