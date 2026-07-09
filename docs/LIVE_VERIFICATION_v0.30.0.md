# Live Verification — v0.30.0

Release gate evidence for the user-facing changes in v0.30.0, using the 5-layer
ledger (file count · magic bytes · dimensions/shape · structlog invariants ·
user-confirmable artifact). Credit-free wherever possible.

## Scope of v0.30.0

- **Added:** MCP `gflow_generate_video` model/duration/count parameters (CLI↔MCP parity, #125).
- **Fixed:** agentic character-recorder idempotency (DataIntegrityError); `prefer_classic`
  WARNING→INFO on the server-gated agentic cohort.

---

## 1. Agentic-cohort image path + both Fixed items — LIVE, credit-free ✅

Exercised end-to-end on **2026-07-09** against live Flow (account `denon82`, **agentic**
cohort) as part of the parable image-consistency spike. Image generation is **credit-free**
(no Veo credits).

- **File count:** multiple stills downloaded per run (t2i baseline, i2i chain, canonical-ref,
  multi-ref) — 1 file per generation, all present on disk.
- **Magic bytes:** JPEG (`ffprobe`-decodable) for every downloaded still.
- **Dimensions/shape:** **768×1376** (native 9:16) on every generation — the no-crop invariant
  held under the agentic cohort.
- **Structlog invariants:** the two Fixed conditions were **observed live** before the fix and
  are now handled:
  - `ui_driver.prefer_classic.exit_agent_failed` (WARNING) fired on the agentic (`tune`) cohort —
    now logged at INFO (`ui_driver.prefer_classic.cohort_natively_agentic`); best-effort by contract.
  - `character create` on the agentic cohort raised `DataIntegrityError`
    (`UNIQUE constraint failed: assets.profile_name, assets.flow_media_id`) — now idempotent
    (recorder reuses the existing asset id). Covered by regression test
    `tests/data/test_recorder_character.py::test_record_character_completed_duplicate_media_id_is_idempotent`.
- **User-confirmable artifact:** the side-by-side still gallery + scorecard recorded in the
  consuming project's spike results (`out/spike/…` + `docs/superpowers/specs/2026-07-09-parable-image-consistency-spike-results.md`).

## 2. MCP `gflow_generate_video` params (#125) — deferred this cycle, unit-test-covered ⚠️

This feature is exercised only by **video generation**, which **costs Veo credits**, and the
consuming pipeline's explicit goal this cycle is **stills-only (skip video generation)**. Per the
release gate's escape hatch, it is **not live-verified this cycle** for that reason — it is **not
silently omitted**. Coverage:

- Unit/contract tests: `tests/mcp/` (including the MCP↔CLI parity contract
  `tests/mcp/test_cli_parity.py`) — model/duration/count validation and the 400-on-unknown-model
  path are covered without spending credits.
- Recommended follow-up: a credit-spending i2v smoke on the next cycle that budgets Veo credits.

---

**Gate summary:** ruff ✓ · format ✓ · pyright 0 errors ✓ · pytest 2145 passed / 7 skipped ✓ ·
repo-hygiene exit 0 ✓. Agentic image path + both fixes live-verified credit-free; MCP video
feature deferred with documented reason.
