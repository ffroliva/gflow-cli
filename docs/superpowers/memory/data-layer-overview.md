---
name: data-layer-overview
description: "gflow_cli.data module — local SQLite catalog for image/video provenance, shipped 2026-05-24 (PR"
---

A local SQLite catalog now records every new image, batch, and video operation. Lives under `src/gflow_cli/data/` — `store.py` (DataStore + migrations), `repository.py` (typed upserts + seed resolvers), `recorder.py` (OperationRecorder facade), `redaction.py` (prompt hashing + signed-URL stripping), `models.py` (dataclasses + enums), `migrations/0001_initial.sql` (v1 schema: profiles/projects/assets/operations/operation_assets/local_files).

**Why:** unblocks I2V (resolve seed image → Flow media ID + project ID without rewalking Flow's library), establishes provenance for "what generated this file", foundation for future history/cost/repair tooling. Spec: `docs/superpowers/specs/2026-05-24-data-layer-design.md`. Plan: `docs/superpowers/plans/2026-05-24-data-layer.md`. Full doc: `docs/DATA_LAYER.md`.

**How to apply:**
- Touching `gflow_cli.data`? Read `docs/DATA_LAYER.md` first — it covers schema, recording flow, redaction, migrations, extension guide.
- Adding a new operation kind: add to `OperationKind` enum, add `record_<kind>(...)` on `OperationRecorder` mirroring existing patterns, wire into CLI runner with the standard recorder lifecycle (open before Flow, close in `finally`, `try/except DataStoreError` after success).
- Adding a column: write a new `NNNN_description.sql` migration (additive, nullable), update `*Record` dataclass field with `None` default at the end, update repository methods, add a test.
- Connection pragmas (`foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`) and `BEGIN IMMEDIATE` transaction grouping are mandatory — `DataStore` enforces them.
- Recorder owns redaction — call sites must never `metadata_json = raw_response` directly; `redact_metadata` strips signed URLs / tokens / authorization headers.

Related: [[exit-code-16-data-store]], [[bdd-stubs-mirror-runtime-signatures]], [[on-started-callback-recorder-safety]], [[exit-code-map-ordering-invariant-test-pitfall]].
