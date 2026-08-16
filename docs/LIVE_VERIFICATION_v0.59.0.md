# Live verification — v0.59.0

> Hand-run against real Flow on Windows 11, profile `denon82` (chrome strategy,
> pt-BR), 2026-08-16, during Phase D/S/R development on
> `feature/542-546-doctor-sync-refresh`. Doctor is offline (unit fixtures are
> its evidence); sync and refresh-on-miss were live-verified end to end.
> Credits spent: ~4 Imagen (two seed/heal pairs across refresh-on-miss e2e
> runs); sync verification was **zero-credit** (listing endpoint only).

## Environment

| | |
|---|---|
| Branch | `feature/542-546-doctor-sync-refresh` (off `develop` @ v0.58.0) |
| Profile | `denon82` (pt-BR) |
| Date | 2026-08-16 |
| OS | Windows 11 |
| Credits spent | ~4 Imagen + 0 Veo |

## Pre-tag gates

- Full four-dir suite on the finished tree: **2,088+ passed** (tests/cli,
  tests/api, tests/services, tests/data, tests/mcp), `pyright src` 0 errors,
  ruff clean, all doc gates green (mirror `--check`, links, PII, hygiene).
- Every task shipped through the subagent implement → spec-review →
  quality-review loop (14 plan tasks + S6b hardening + 3 review-fix commits);
  two live-caught bugs fixed with red-first regression tests (sqlite
  cross-thread store open; MagicMock project-id leak past an `is None` guard).

## Matrix

| # | Feature | Verification | Result |
|---|---|---|---|
| 1 | `gflow doctor` (#542) | Offline by design — 33 unit tests over migration-built fixture DBs incl. the byte-identical never-writes proof; real-output smoke run during review (grouped report, caveat, exit 33) | ✅ |
| 2 | `gflow data sync --names` (#543) e2e | `tests/e2e/test_data_sync_names_e2e.py` (zero credits): seeded name-stripped rows + fabricated ghost against a real project → names restored with `sync.named_at`/`sync.source="sync"`, ghost tombstoned `missing_remote`, idempotent re-run byte-identical | ✅ 1 passed, 9.7 s |
| 3 | Sync at real scale | **Real catalog backfill**: 117 projects swept in ~4 min, zero failures — 398 nameless rows → **338 named, 57 tombstoned, 3 left** (async captions; retry harmlessly). Dry-run first (50 projects, counts matched) | ✅ |
| 4 | Refresh-on-miss (#546) e2e | `tests/e2e/test_refresh_on_miss_e2e.py` (~2 credits/run): seed t2i → catalog name deliberately corrupted (simulated rename) → same-project `i2i --ref` → picker miss → resolver fetched the true caption mid-generation → `image_ref_selected_existing`, no upload fallback, no `name_resolver_failed`, catalog healed with `sync.source="refresh"` | ✅ 1 passed, 100 s (first attempt failed on transient live-Flow timing; clean pass on re-run) |

## 5-layer ledger (refresh-on-miss, the deepest chain)

1. **File count** — one generated image from the heal run in the isolated out dir.
2. **Magic bytes / artifact** — generation completed through the normal
   download path (exit 0, `--json` payload with `ref_count == 1`).
3. **Shape** — `image_ref_selected_existing` with `resolved_by=display_name`
   after a first-search miss on the corrupted name.
4. **Structlog invariants** — no `image_ref_upload_fallback`, no
   `name_resolver_failed`; healed row carries `display_name == real Flow
   caption` + `sync.source == "refresh"`.
5. **User-confirmable artifact** — the referenced asset and the new generation
   are visible in the `denon82` Flow project.

## Known live behaviors recorded

- Flow captions remain async: 3 real catalog rows stayed nameless after the
  full backfill — expected; they re-enter the next sweep.
- One transient heal-run failure on live Flow (timing); deterministic on
  re-run. The e2e's seed retry (2 attempts) covers the caption race; the heal
  step has no retry by design — a rerun is the operator remedy.

## Post-tag evidence

_To be filled after the v0.59.0 tag publishes: release workflow run link, PyPI
listing._
