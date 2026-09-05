---
name: exit-code-16-data-store
description: Exit code 16 = data-layer failure (DataStoreError / DataMigrationError / DataIntegrityError). Pre-Flow only — post-success failures warn + return 0
---

Exit code 16 covers three error classes in `src/gflow_cli/errors.py`:

- `DataStoreError` — generic DB open / read / write failure.
- `DataMigrationError` — migration apply failed, SHA-256 checksum drift, or DB schema newer than installed gflow-cli.
- `DataIntegrityError` — `sqlite3.IntegrityError` wrapped (e.g., natural-key conflict on `(profile_name, flow_media_id)`).

All three are re-exported via `gflow_cli.exceptions`. Comment in `errors.py:423` documents the EXIT_CODE_MAP ordering invariant — see [[exit-code-map-ordering-invariant-test-pitfall]].

**Contract:** exit code 16 fires ONLY for pre-Flow failures (DB open, migration check before any paid Flow call). Post-success persistence failures (generation completed, file downloaded, but `INSERT` failed) emit `data.persistence_failed_after_success` structlog event with `flow_media_id` + `local_path`, print a yellow console warning, and STILL return 0. This prevents scripts from retrying paid generations just because the local catalog couldn't be updated.

**How to apply:**
- User reports exit 16: check `GFLOW_CLI_DB_PATH` filesystem permissions, then check for "newer schema" — upgrade gflow-cli OR repoint `GFLOW_CLI_DB_PATH` to a compatible DB.
- Documented recovery in `docs/USAGE.md` exit-code table and `docs/DATA_LAYER.md` § "Persistence-failure handling".
- Writing new recorder integrations: open the recorder BEFORE the Flow call (fail-fast on 16), close in `finally`, wrap post-success `record_*` calls in `try/except DataStoreError → _warn_persistence_failed_after_success`. See `cli_image.py::_run_t2i` for the canonical shape.

Related: [[data-layer-overview]], [[on-started-callback-recorder-safety]].
