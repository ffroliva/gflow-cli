# Live verification — v0.39.0 (2026-07-19)

Release scope: **failed generations are now persisted to the local catalog**
([#341]) — every paid-generation path records a terminal `status="failed"`
operation row (stable `error_type` from the exception's RFC 9457
`problem_type`, redacted `error_detail`) before the error propagates, plus the
new `gflow data list errors` browse surface and status-aware video-poll
recording. Verified against live Flow on denon82. Credit-free (the failure
under test never reached a billable generation).

## Run — real live failure produced the first FAILED row, $0

A real `uv run gflow image t2i …` on denon82 failed at the wire: Flow's
`batchGenerateImages` returned **HTTP 400 → `WireFormatError`**. The
record-on-catch funnel (`record_failed_operation_safe`) fired before the error
propagated and wrote the **first-ever `failed` operation row** into the
production catalog:

- `error_type=wire-format` — the last URI segment of the `GFlowError.problem_type`,
  derived by the shared taxonomy helper (no hand-rolled slug map).
- `error_detail` scrubbed and length-capped per the redaction path.
- `gflow data list errors --profile denon82` renders the row newest-first
  (Rich table on a TTY; JSONL under `--json`).
- **$0** — the failure occurred before any billable generation.

## 5-layer evidence ledger

| Layer | Evidence |
|---|---|
| Row count | 1 new `status=failed` operation row in the denon82 catalog after the failing run (0 before) |
| Taxonomy value | `error_type=wire-format` — matches the last segment of the `WireFormatError` `problem_type` URI; slug uniqueness pinned by unit test |
| Redaction | `error_detail` stored scrubbed + 500-char capped (Bearer/SAPISIDHASH/auth-cookie/signed-URL patterns removed); non-`GFlowError` messages stored only as `sha256:<digest>` |
| Structlog invariants | record-on-catch fired (`record_failed_operation_safe`), warn-only double-fault guard did not trigger, error re-raised to the caller, `cli_version 0.39.0` |
| User-confirmable artifact | `gflow data list errors` lists the failed row with its `error_type`, timestamp, and redacted detail — the observability the issue asked for, confirmed end-to-end |

## Not verified live this cycle (recorded, not omitted)

The funnel was wired into **every** paid-generation path (video t2v/i2v/r2v,
`video chain` incl. download-fail abort, image t2i/i2i, multi-prompt t2i,
`gflow run`, `image batch`, `movie run`, and the async worker). Only the
**image t2i** path was exercised by a genuine live failure this cycle; the
remaining paths are covered by the dedicated funnel regression tests (chain
hook, worker, batch) added in this release, not by a forced live failure —
provoking a real terminal failure on each billable path would cost Veo credits
and cannot be triggered deterministically. The status-aware video-poll fix
(`record_completed_video` recording `succeeded=false` polls as
`generation-failed`) is unit-locked; its live trigger
(`PUBLIC_ERROR_UNSAFE_GENERATION` on a real Veo poll) was not observed this
cycle.

The next real WAF-403 block on denon82 will land as a `waf-rejection` row —
that is the dataset the issue exists to build, and the point at which ADR-13
([#315]) becomes worth revisiting.

The verification artifact (the throwaway failing run's output dir) was not
committed; only the catalog row and structlog evidence were recorded.

## Pre-tag gates

- `/gflow:check` — hygiene, doc-links, ruff, `ruff format --check`, and `pyright
  src` (0 errors) all clean; full-suite coverage satisfied by #343's green CI on
  the same develop tree (`200c5a2`).
- `/gflow:doc-review` council verdict: **GREEN across all 3 auditors**
  (completeness / cross-reference / drift). No Tier-1 findings. One Tier-2 doc
  gap fixed in the release-prep commit: `DATA_LAYER.md` now states that `failed`
  rows grow without an automatic cap and points to #345 for bounded retention.
  Council reports at `tmp/council/0{1,2,3}-*.md` (local-only).

[#341]: https://github.com/ffroliva/gflow-cli/issues/341
[#315]: https://github.com/ffroliva/gflow-cli/issues/315
