# Live verification — v0.40.0 (2026-07-19)

Release scope: **prompt `@`-mention resolution for asset tagging** ([#344]) —
`@Name` in a t2i/i2i/video prompt resolves to a staged, taggable character
entity or media asset reference via `services/mentions.py`'s
`resolve_and_apply`, shared by the `image`/`video` CLI paths, the async
worker, and MCP tools. De-tagged prompts are persisted to the catalog. See
[`docs/REFERENCE_STRATEGIES.md`](REFERENCE_STRATEGIES.md) for `@`-mention vs
`--reference-entity` vs `--ref`.

## Run — full clean pass on the real feature (ffroliva, ~2 Imagen credits)

`GFLOW_CLI_E2E_PROFILE=ffroliva uv run pytest -m e2e tests/e2e/test_asset_tagging_e2e.py`
against `develop@25ed124`, **unmodified**:

1. `gflow character create --name Zoro --face-prompt …` creates a real,
   taggable character (bound reference images — `IndexEntry.has_reference_images`
   requires this; a bare image-less entity is rejected early with a clear
   error rather than failing deep in the UI attach).
2. The test's own wait-loop polls `list_characters()` until the new entity is
   visible with its reference images attached — Flow's `projectInitialData`
   read lags the entity-creation write by a few seconds, a documented,
   test-only propagation wait (see the comment at
   `tests/e2e/test_asset_tagging_e2e.py:96-102`).
3. `gflow image t2i "a photo of @Zoro walking" --project <id>` — **PASSED**,
   returncode 0, in 74s.

Result: **1 passed**. No code changes were needed — `resolve_and_apply` and
its `has_reference_images` guard, as already merged in [#344], work
end-to-end against live Flow exactly as designed.

## 5-layer evidence ledger

| Layer | Evidence |
|---|---|
| Row count | New `operations` row (`command='image t2i'`) in the ffroliva catalog after the run |
| Field value | `list_images()` shows a recorded prompt of `"a photo of Zoro walking"` — the `@Zoro` mention was resolved and stripped, not passed through raw |
| Structlog invariants | `mention_resolved` (not `mention_unresolved`) logged for the Zoro entity; no `WireFormatError` |
| User-confirmable artifact | A real generated image (`out/*.png`, `size > 1024` bytes) produced by the CLI subprocess |
| Test result | `1 passed in 74.12s`, zero retries, zero flakes on this run |

## An investigation dead end, recorded for the record

Before this clean run, a separate local checkout (`gflow-cli-pr-344`, branch
`pr-344`) was used to probe a suspected "Unknown mention" bug. That checkout
turned out to be a **stale WIP snapshot that predates** the `has_reference_images`
guard, the `resolve_and_apply` consolidation, and the e2e test's wait-loop —
none of which represent `develop`'s actual state. Testing against it
reproduced a real read-after-write lag in Flow's `projectInitialData`
endpoint (~4s, measured via a throwaway spike script) and a bug in that
stale test's own DB-assertion code, but **neither applies to the code that
is actually merged**. A product-side retry was built and live-verified
working against that stale branch, then discarded once this run confirmed
`develop`'s existing test-only wait-loop design already handles the real
lag correctly. No code changes shipped from that detour — this doc
supersedes it.

## Not verified live this cycle

- Video-path (`i2v`/`r2v`) and worker-daemon mention resolution share the
  same `resolve_and_apply` call path (verified by code inspection and the
  offline BDD suite) but were not separately live-exercised this cycle —
  only the image t2i path was.
- Media-mention (non-character) resolution and the `--tag` flag remain
  Phase 3, out of scope per the original design spec.
- Pre-existing, unrelated issue [#174] (Flow's intermittent media-library A/B
  UI variant) can still intermittently block generation regardless of this
  feature — not observed on this run, tracked separately, status HOLD.

## Pre-tag gates

- Offline: repo hygiene, doc-links, `ruff check`, `ruff format --check`,
  `pyright src` (0 errors) all clean on `develop@25ed124`.
- `/gflow:doc-review`: mechanical sections 1–7 pass. Council verdict:
  **YELLOW** across 3 auditors (Completeness YELLOW, Cross-reference YELLOW,
  Drift GREEN). 6 findings; 4 Tier 1/2 fixed in the release-prep commit
  (`docs/CHARACTER.md`'s 5 stale "not yet implemented" references corrected
  to reflect shipped `@Name` mention resolution; `CHANGELOG.md` and
  `docs/PROJECT_STATUS.md` scope wording tightened — character mentions
  ship on both image/video paths, media-asset mentions are image-only;
  the design spec's stale `DRAFT` status marker updated to `SHIPPED`). 2
  Tier 3 deferred to backlog (`docs/USAGE.md` and `docs/USER_GUIDE.md` lack
  `@Name` syntax coverage — tracked as a fast-follow, not blocking since the
  feature is fully documented in `CHARACTER.md`/`REFERENCE_STRATEGIES.md`;
  `PLAN.md` task checkboxes left unchecked despite shipping — internal
  bookkeeping only). Council reports at `tmp/council/0{1,2,3}-*.md`
  (local-only, not committed).

## Post-tag evidence

- Tag: TBD (filled after `git push origin v0.40.0`)
- Release workflow: TBD (filled after CI confirms)
- PyPI: TBD (filled after publish confirms)

[#344]: https://github.com/ffroliva/gflow-cli/issues/344
[#174]: https://github.com/ffroliva/gflow-cli/issues/174
