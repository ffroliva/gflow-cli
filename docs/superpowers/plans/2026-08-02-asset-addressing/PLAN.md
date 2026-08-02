# Asset Addressing & Storage Portability — Implementation Plan

**Goal:** every byte gflow-cli writes is addressable through the catalog and verifiable by hash,
regardless of backend; a user can resolve any generation to its artifact with one scriptable
command, move that artifact between local and cloud storage without losing provenance, and every
error on this surface names the exact command that fixes it.

**Architecture:** keep `gflow_cli.storage`'s UPath-based backend dispatch unchanged; add a frozen
`Location` value object as the write path's return type; make the storage key canonical (built
once, never recovered by subtraction); give every byte-producing surface a catalog row by admitting
a **derived artifact** class (`assets.flow_media_id` nullable). **No `AssetStore` Protocol, no
strategy classes** — see spec § 10.1. Full contract:
[specs/2026-08-02-asset-addressing-design.md](../../specs/2026-08-02-asset-addressing-design.md).

**Origin:** [#411](https://github.com/ffroliva/gflow-cli/issues/411). Note that #411's literal ask
(`-o/--output`) is **deferred** — it is closed by Task 2.1's read API, not by a flag (spec § 10.4).

**Council verdict:** internal 6-dimension council, 2026-08-02 — **RED** on the originally proposed
shape (D1 correctness/mode-coverage RED, D14 over-engineering RED, D3/D4/D7/D8 YELLOW). This plan
implements the superseding design. **`/gflow:predict` has not been run against this revised spec —
run it before starting Phase 2.**

**⚠️ External-review gap:** the council ran `small`-tier; `codex` and `agy` were unavailable, so
both RED verdicts come from same-model-family reviewers. Re-run `/gflow:llm-council` with the
external layer before Phase 2.

**⛔ Environment constraint:** Phase 0 tasks 0.1, 0.3, 0.4, 0.5 and all of Phase 1 touch generation
recording paths. Per AGENTS.md, offline green is **LIKELY-working, never CONFIRMED** — these need a
live run on an operator machine with an authenticated Chrome-strategy profile and real credits
before the PR is called done. Phase 2 (port) does **not** contact Flow and is exempt. Any PR
produced from a cloud/CI session must leave the live-verification boxes unchecked and say so.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| High | Local video layout change (flat → `videos/<date>/`) breaks user scripts globbing `--out-dir/*.mp4` | Explicit CHANGELOG entry + open question 2 answered before Task 1.3; consider a one-minor-version compat flag |
| High | `assets.flow_media_id` nullable weakens `UNIQUE(profile_name, flow_media_id)` dedupe | Partial unique index excluding NULLs; synthetic `gflow:derived:<sha>` handle carries the identity |
| Medium | Chain + cloud is already broken (PyAV cannot decode `gs://`) — fixing it may expand scope | Task 0.5 ships the honest error first; download-to-temp is a separate decision (open question 1) |
| Medium | `containers` suite is not CI-gated, so cloud adapter changes have no safety net | Task 0.10 forces the choice: wire the job or document opt-in explicitly |
| Medium | `memory://` is documented but unproven — port tests could be built on a fiction | Task 0.11 proves it or removes the claim, before Phase 2 depends on it |
| Low | `ConfigurationError` split touches 114 raise sites | Split changes class defaults only; no raise site needs editing |

---

## File structure

### New files
```
src/gflow_cli/locations.py
  Location frozen dataclass; resolve_location(); key builders (image/video/character/scene/frame)
src/gflow_cli/data/migrations/0010_asset_addressing.sql
  assets.flow_media_id nullable (table rebuild, 0009 pattern); local_files.provenance
tests/test_locations.py
  Key scheme, sanitization at construction, local/cloud resolution, extension correction
tests/data/test_migration_0010.py
  Migration applies, is checksum-stable, preserves existing rows (test_store_migrations.py pattern)
tests/cli/test_data_media_json.py
  `gflow data media --json` contract, incl. cloud rows and multi-location assets
tests/cli/test_data_port.py
  Port ordering, idempotency, partial failure, --prune-source, --dry-run (fake store)
tests/test_error_remediation.py
  All-subclass remediation coverage + on-topic assertions (currently-failing regression)
tests/conftest_fake_store.py
  Dict-backed fake storage fixture — the primary offline harness for port
```

### Modified files
```
src/gflow_cli/storage.py            Location construction + centralized key sanitization; typed ImportError
src/gflow_cli/paths.py              Delete dead video_output_path; key builders move to locations.py
src/gflow_cli/api/client.py         Collapse 3 duplicated relative_to blocks; honor out_dir; return Location
src/gflow_cli/api/transports/ui_automation_video.py   Use the shared key builder; kill layout divergence
src/gflow_cli/data/recorder.py      sha256/bytes for cloud; cloud_storage_info on character path; provenance
src/gflow_cli/data/repository.py    provenance column; derived-artifact upsert
src/gflow_cli/cli_image.py          Upscale records to catalog
src/gflow_cli/services/character_create.py   Stop str()-ing a UPath and re-parsing as Path
src/gflow_cli/chain.py              Seed-frame artifact recording; cloud guard
src/gflow_cli/cli_data.py           `data media --json`; new `data port`; remediation hints
src/gflow_cli/errors.py             ManifestValidationError / PromptValidationError split
src/gflow_cli/config.py             Reject userinfo in GFLOW_CLI_STORAGE_URI
src/gflow_cli/json_output.py        locations[] alongside local_path
docs/CONFIGURATION.md, docs/EXTERNAL_STORAGE.md, docs/DATA_LAYER.md, docs/SECURITY.md, CHANGELOG.md
PLAN.md                             Close Phase 8 as superseded; retire the line-468 backlog item
```

---

## Phase 0 — Repair (no new concepts)

Every task here is a live defect the council found. Each is independently shippable, offline-testable,
and carries no design risk. **Phase 0 can merge before Phases 1–2 are designed further.**

### Task 0.1 — Compute `sha256` and `bytes` for cloud writes
- [ ] Failing test: record a cloud-backed image/video, assert `local_files.sha256` is non-NULL
- [ ] Hash from the in-memory buffer at all three sites — `recorder.py:456-457`, `:518-519`, `:845-846`
      currently do `sha256=... if cloud_storage_info is None else None`; the bytes are already
      buffered (`client.py:1557` and the concat/upscale equivalents), so no re-read or network call
- [ ] Assert no extra read: the hash is computed from the same `bytes` object that is written
- **Why first:** Phase 2's port cannot verify integrity without it (spec § 2.4).

### Task 0.2 — `upsample_image` honors its own `out_dir`
- [ ] Failing test: `image upscale --out-dir <tmp>` with `GFLOW_CLI_STORAGE_URI` set produces a
      date-partitioned key, not a flat basename
- [ ] Fix: `client.py:281-289` stores the constructor's `out_dir=` as `self._out_dir` but the key
      derivation at `:1934-1936` reads `self.settings.output_dir` — so **every** `upscale --out-dir`
      with cloud storage hits the lossy fallback unconditionally

### Task 0.3 — `image upscale` records to the catalog
- [ ] Failing test: after `image upscale`, `gflow data list images` contains the upscaled asset
- [ ] Fix: `_run_upscale` (`cli_image.py:585-612`) never instantiates `OperationRecorder` — add
      recording with the same `record_failed_operation_safe` guard used elsewhere
- [ ] Confirm the recorder failure path cannot lose a paid generation

### Task 0.4 — `character create` stops corrupting the catalog under cloud storage
- [ ] Failing test: `character create` with `GFLOW_CLI_STORAGE_URI` set records
      `storage_provider="gcs"` and an intact `gs://…` URI
- [ ] Fix A: `services/character_create.py:176,224` does `str(p0)`/`str(p1)` on what may be a cloud
      `UPath`; `recorder.py:1204` re-parses it with plain `pathlib.Path` and calls `.resolve()`,
      producing a nonsensical local path
- [ ] Fix B: `record_character_completed` (`recorder.py:1132-1188`) has no `cloud_storage_info`
      parameter, so `:1263-1264` hardcodes `storage_provider=None, cloud_uri=None` regardless of
      backend — add the parameter and thread it through

### Task 0.5 — chain + cloud storage fails honestly
- [ ] Failing test: `video chain` with `GFLOW_CLI_STORAGE_URI` set raises `ConfigurationError`
      (exit 11) with the spec § 4.6 remediation, instead of failing obscurely at link 2
- [ ] Root cause: seed frames are written as bare local `Path`s (`chain.py:257,379-385`), and
      `extract_last_frame` decodes with PyAV, which cannot open a `gs://`/`s3://` source — so every
      link after the first is already broken today
- [ ] **Open question 1 gates the permanent fix** (fail fast vs download-to-temp); this task ships
      the honest error either way

### Task 0.6 — the cloud-extra install hint reaches the user
- [ ] Failing test: with `GFLOW_CLI_STORAGE_URI=gs://…` and `universal_pathlib` absent, the CLI
      exits 11 and prints `pip install 'gflow-cli[gcs]'`
- [ ] Fix: `storage.py:68-81` authors the correct message then raises a raw `ImportError`, which is
      not a `GFlowError`, so the catch-all discards it and prints "Unexpected error." Re-raise as
      `ConfigurationError` — pattern already correct at `api/_engine.py:184-187`

### Task 0.7 — error taxonomy split + enforcement
- [ ] Failing test A: walk `GFlowError.__subclasses__()` and assert every class resolves to a
      non-empty remediation — generalizes `tests/test_self_documenting_errors.py:41-57`, which
      hardcodes 8 of 36 classes
- [ ] Failing test B (on-topic): a manifest-validation error's remediation must not contain
      "transport" / "make_transport" — **this fails today**
- [ ] Fix: add `ManifestValidationError` and `PromptValidationError` subclasses with correct
      defaults. `ConfigurationError` is raised 119 times with only 5 passing a hint; the inherited
      default (`errors.py:339-342`) is transport-specific and wrong for 53 sites in
      `movie_manifest.py` and 32 in `image_batch.py`
- [ ] Re-point those raise sites at the new classes (mechanical; no message rewriting)
- [ ] Add `remediation_hint=` to the two `gflow data media` raise sites (`cli_data.py:302-306`,
      `:314-322`) — the ambiguous-profile case has good text in `detail` but not in
      `remediation_hint`, so `--json` consumers get the wrong guidance

### Task 0.8 — delete dead code, collapse duplication
- [ ] Delete `video_output_path` (`paths.py:114-122`) — zero production callers; remove its test
- [ ] Collapse the three duplicated `relative_to` blocks (`client.py:1583-1585`, `:1796-1798`,
      `:1934-1936`) into one private helper — this also fixes a live divergence where two sites
      `mkdir` in the `else` branch and one does not (`:1588` vs `:1801`/`:1940`)
- [ ] Expected: net ≈ −30 lines

### Task 0.9 — reject credentials in storage URIs
- [ ] Failing test: `GFLOW_CLI_STORAGE_URI=s3://KEY:SECRET@bucket/` is rejected at settings load
- [ ] Fix `config.py:357-369` (currently a prefix check only) to reject embedded userinfo
- [ ] Add `local_files.path` / `cloud_uri` to the redaction test matrix; update
      `docs/SECURITY.md:120-122`, which does not currently list them

### Task 0.10 — decide the `containers` CI story
- [ ] Either wire a `containers` job into `.github/workflows/ci.yml`, **or** document in
      `docs/EXTERNAL_STORAGE.md` that S3/GCS adapter tests are developer opt-in and not a merge gate
- [ ] Today the default addopts exclude the marker and CI has no Docker reference, so
      `tests/integration/test_storage_{s3,gcs}.py` never runs anywhere automatically

### Task 0.11 — prove or remove `memory://`
- [ ] Add a `memory://` round-trip test through `storage_path` + `write_asset_async`, **or** remove
      the claim from `config.py:365` and `storage.py:15,34,110`
- [ ] It is currently allowed and documented as the test-only backend with **zero** references in
      `tests/` — Phase 2 must not build on it unproven

### Task 0.12 — Phase 0 gates
- [ ] `/gflow:check` green
- [ ] CHANGELOG entry under `[Unreleased]`
- [ ] Live-verify one `image upscale`, one `character create`, and one `video chain` on an operator
      machine (tasks 0.2–0.5 touch generation paths)

---

## Phase 1 — Canonical keys and the read API

**Do not start before Phase 0 merges** — Phase 1 builds on corrected write paths.

### Task 1.1 — `Location` value object
- [ ] Test: construction sanitizes `key` via `_sanitize_key`; traversal (`../`) raises
- [ ] Test: `uri` derives correctly for local, `gs://`, `s3://`
- [ ] Implement `src/gflow_cli/locations.py` per spec § 4.1 — frozen dataclass, no Protocol

### Task 1.2 — canonical key builders
- [ ] Table test covering all five key shapes (spec § 4.3)
- [ ] Test: extension correction updates `Location.key` before the catalog row is written, so key
      and reality never diverge
- [ ] Move builders out of `paths.py`; replace the inline duplicate at
      `ui_automation_video.py:1146-1157`

### Task 1.3 — unify local and cloud video layout ⚠️ breaking
- [ ] Test: local video lands at `videos/<YYYY-MM-DD>/<media_id>.mp4`, matching cloud
- [ ] **Requires open question 2 answered first** (accept the break vs compat flag)
- [ ] CHANGELOG entry flagged BREAKING

### Task 1.4 — migration 0010
- [ ] Test: applies cleanly, checksum recorded, existing rows preserved
      (`tests/data/test_store_migrations.py` pattern)
- [ ] `assets.flow_media_id` nullable via table rebuild (`0009_queue_claims.sql` pattern — SQLite
      cannot alter constraints in place); partial unique index excluding NULLs
- [ ] `local_files.provenance` (`generated` | `derived` | `referenced`)
- [ ] **No Python, no `Settings`, no filesystem access in the SQL** — the runner executes verbatim
      inside one transaction on every `DataStore.open()` (spec § 6.3)

### Task 1.5 — derived artifacts get catalog rows
- [ ] Test: `scene create --output` produces an addressable asset; `scenes.output_path` still reads
      correctly as a derived view
- [ ] Test: chain seed frames produce addressable artifacts; `chain_links.seed_frame_path` unchanged
- [ ] Synthetic handle `gflow:derived:<sha256[:16]>`

### Task 1.6 — `image upload` referenced-file class
- [ ] Test: an uploaded source file records `provenance="referenced"` and is not treated as
      gflow-written bytes

### Task 1.7 — `gflow data media --json`
- [ ] Test: JSON contract per spec § 4.4, including multi-location and cloud-only assets
- [ ] Test: not-found and ambiguous cases carry the Task 0.7 remediation in `--json`
- [ ] Add `locations[]` to generation-command `--json`, retaining `local_path`

### Task 1.8 — Phase 1 gates
- [ ] `/gflow:check` green · docs updated (`CONFIGURATION.md:58-59,400`, `DATA_LAYER.md`,
      `EXTERNAL_STORAGE.md`) · `check_doc_links.py` green
- [ ] Live-verify one t2i, one t2v, one scene create, one chain run
- [ ] **Close #411** referencing Task 1.7

---

## Phase 2 — Port

**Prerequisite: Task 0.1 must be merged** — without cloud hashes, port cannot verify integrity, and
a "verified" port would be unverified by construction.

### Task 2.0 — run `/gflow:predict` and the external council layer
- [ ] `/gflow:predict` on the revised spec (never run against this shape)
- [ ] `/gflow:llm-council` at `medium` or `high` tier with `codex` installed

### Task 2.1 — fake dict-backed store harness
- [ ] `tests/conftest_fake_store.py` — the primary offline harness; keep imports light so the
      default suite's collection weight does not grow (AGENTS.md: full-suite OOM)

### Task 2.2 — `gflow data port`
- [ ] Test: additive by default — port produces a **second** `Location`, source untouched
- [ ] Test: ordering — bytes → hash → compare → commit catalog → (optional) delete source
- [ ] Test: hash mismatch aborts with both copies intact
- [ ] Test: crash after bytes-write, before catalog commit → resume is a no-op, no orphan rows
- [ ] Test: re-running a completed port is idempotent
- [ ] Test: `--prune-source` deletes the old location only after the new one verifies
- [ ] Test: `--dry-run` performs no writes
- [ ] `transfer_state` (`pending` → `verified` → `committed`) drives idempotency
- [ ] All spec § 4.6 port errors, with remediation

### Task 2.3 — adapter verification
- [ ] MinIO / fake-gcs round-trip under the `containers` marker
- [ ] Report honestly per Task 0.10's outcome — if not CI-gated, say "developer-verified", never
      "CI-verified"

### Task 2.4 — Phase 2 gates
- [ ] `/gflow:check` green · `/gflow:sonar` green
- [ ] Port needs **no** live-verify — it never contacts Flow's API (spec § 4.5)

---

## Phase 3 — Deferred

Not in this plan; listed so they are not silently lost.

- `storage_key` column and `gflow data reconcile` — revisit when drift is **measured** (spec § 10.3)
- `local_files.path` polymorphism / table rebuild (spec § 6.2)
- `-o/--output` on generation commands — as sugar, `type=str` never `click.Path` (spec § 10.4)
- Orphan adoption — carries a security cost (spec § 10.3)

---

## Housekeeping (this PR)

- [ ] Close `PLAN.md` Phase 8 (lines 674-685) as superseded by `gflow_cli.storage`, recording the
      CI-coverage caveat
- [ ] Retire `PLAN.md:468` ("Unify Output Resolution") — resolved by spec § 4.3

---

## Open questions — must be answered before the tasks they gate

1. **Chain + cloud** (gates Task 0.5's permanent fix): fail fast, or download-to-temp before PyAV?
2. **Local video layout** (gates Task 1.3): accept the break, or compat flag for one minor version?
3. **`containers` in CI** (gates Task 0.10): wire the job, or document opt-in?
