# PLAN — Issue #341: persist failed generations (error tracking as a first-class DB citizen)

**Date:** 2026-07-18 · **Branch:** `feature/issue-341-error-tracking` · **Base:** `develop`
**Predict verdict:** GO-with-conditions (council: Architect GO 7, Security CAUTION 8, Perf/UX GO 8, Devil's Advocate CAUTION 8)

## Goal

Every failed paid generation writes a terminal `FAILED` row to the `operations` table
(`error_type` + redacted `error_detail`), queryable via `gflow data list errors`, so WAF-403
cadence, cohort pins, and policy rejections become measurable instead of folkloric.

## Council-locked design decisions

- **Taxonomy = existing RFC 9457 `problem_type`.** `error_type` is the last URI segment of
  `exc.problem_type` (`waf-rejection`, `content-policy`, `auth-expired`, …). Non-`GFlowError`
  → `error_type = type(exc).__name__`, `error_detail` = SHA-256 hash (never `str(exc)`).
  No hand-rolled slug map (drift risk).
- **Record-on-catch at the orchestrator choke points, then re-raise.** No STARTED-first
  rewrite of the image hot path. FAILED write guarded by `try/except DataStoreError` →
  warn-only (existing idiom), so failure-recording can never mask the generation error.
- **Video**: STARTED row may exist (via `on_started`) — mirror `record_completed_video`:
  `get_operation_for_output_asset` → `update_operation_status(FAILED)`; else INSERT.
  Early failures (before `on_started`) INSERT a fresh FAILED row.
- **Images/batch/movie**: no pre-existing row — always INSERT with full request metadata.
- **Character saga excluded** — `services/character_create.py` deliberately keeps STARTED
  rows for resume; do NOT wire FAILED there.
- **Redaction (testable requirements):** `error_detail` passes through a new free-text scrub
  (`Bearer …`, `SAPISIDHASH …`, session-token/SAPISID cookie pairs, signed-URL query keys)
  then truncates to 500 chars; prompts on FAILED rows go through the same
  `_resolve_prompts`/`prompt_fields(mode=…)` path as success rows; the Rich table escapes
  markup; the read path only ever reads the persisted column. Fix the pre-existing
  unredacted `text[:200]` interpolation in `api/transports/_common.py::interpret_response`
  in the same PR (issue #341 upgrades its blast radius to a durable queryable row).
- **Surface = `gflow data list errors`** (consistent with `list projects/images/videos/profiles`,
  same `_PROFILE_OPT/_LIMIT_OPT/_OFFSET_OPT/_JSON_OPT/@_guard/_emit` conventions). The issue's
  `gflow data errors` example is satisfied by the `list` subgroup placement. MCP parity via
  `_MCP_EXEMPT` entry in `tests/mcp/test_cli_parity.py` (merge gate).
- **No schema migration** (columns exist), **no status index** (~400 rows), **deferred**:
  retention/export, structured external failure events, `attempt_index`/`waf_recovery_seconds`/
  `retryable` columns, `image upscale` path. Documented in PR as explicit descopes.

## Verified paid-generation funnel coverage (Devil's Advocate audit)

| Path | Site of FAILED write |
|---|---|
| `video t2v/i2v/r2v` | `cli_video.py::_generate_and_report` |
| `video chain` | `cli_video.py` chain `ChainPartialError`/failure handler (NOT `chain.py`) |
| `image t2i` / `i2i` (single) | `cli_image.py::_run_t2i` / `_run_i2i` |
| `image t2i` multi-prompt + `gflow run` | `image_batch.py` per-prompt failure path |
| `image batch <manifest>` | `image_batch.py` manifest failed-row path |
| `movie run` scene | `cli_movie.py` scene failure handler |
| MCP generate (queue) | `worker/daemon.py::process_task` except block |
| `scene create`, `character`, `upscale` | excluded (credit-free / saga / descoped) |

## Tasks

- [ ] T1 Redaction: `redact_error_detail()` in `data/redaction.py` + tests (Bearer/SAPISIDHASH/
      cookie/signed-URL scrub, 500-char truncate)
- [ ] T2 Recorder: `OperationRecorder.record_failed_operation(...)` + tests (GFlowError slug,
      non-GFlowError hash, prompt-mode honored, video UPDATE-vs-INSERT, warn-only wrapper helper)
- [ ] T3 Transport hardening: redact-before-truncate in `api/transports/_common.py::interpret_response`
      + test (Bearer token in response body never reaches exception detail)
- [ ] T4 Read path: `queries.list_errors()` + `OperationErrorRow` + tests
- [ ] T5 CLI: `gflow data list errors` + `_emit_errors_table` (markup-escaped) + tests + `_MCP_EXEMPT`
- [ ] T6 Wire call sites: video, chain, t2i, i2i, image_batch (×2), movie, worker daemon + per-family tests
- [ ] T7 Docs: DATA_LAYER.md (recording flow, querying, redaction incl. store-mode covers refused
      prompts), USAGE.md (`data list errors`), CHANGELOG `[Unreleased]`
- [ ] T8 Gates: `/gflow:check` + council branch-review + ponytail review + PR (Refs #341)
