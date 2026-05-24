# Data Layer

`gflow-cli` keeps a local SQLite catalog of every new image, batch, and video operation. This document explains what it stores, why, and how to use it.

If you only need quick lookup syntax, jump to [Querying the data layer](#querying-the-data-layer). For env vars, see [`CONFIGURATION.md`](CONFIGURATION.md#gflow_cli_db_path). For exit codes and recovery, see [`USAGE.md`](USAGE.md#exit-codes).

---

## Goals

The data layer exists to answer three questions that the CLI alone cannot:

1. **"What generated this file?"** — given a local `.png` / `.mp4`, find the prompt, model, aspect ratio, profile, project, and Flow media ID that produced it.
2. **"Which seed image can I reuse for I2V?"** — given a profile, resolve a candidate image to the Flow `media_id` + `project_id` the video transport needs, without rewalking the Flow UI library.
3. **"What happened during that batch?"** — given a batch run, list every output with success/failure status and the operation provenance.

It is also the foundation for future history browsing, cost tracking, repair/import tooling, and (eventually) a management UI.

### Explicit non-goals (v1)

- No Postgres / cloud / `DATABASE_URL` runtime support — SQLite only.
- No event sourcing, ORM, or remote multi-user semantics.
- No backfill of existing output folders — only NEW operations are recorded.
- No user-facing history browser — only the minimal `gflow data media <id>` lookup.

The schema is adapter-shaped so a future Postgres backend can slot in without changing call sites, but that work is backlog.

---

## What is recorded

| Table | Holds | When written |
|---|---|---|
| `profiles` | Logical profile name (e.g. `default`), profile dir, first/last-seen timestamps | First operation per profile |
| `projects` | Flow project ID + display title per profile | First operation that touches the project |
| `assets` | One row per Flow media ID (image OR video) — model, aspect, dimensions, seed, generation ID, status | Upload response, image generation response, video start, video completion |
| `operations` | One row per CLI command invocation — mode (`upload_image`/`t2i`/`i2i`/`t2v`/`i2v`/`r2v`), prompt, model, aspect, started/completed timestamps, error type/detail, Flow operation/batch IDs | Each command run |
| `operation_assets` | Many-to-many between operations and assets, with role (`input`/`output`/`seed_start`/`seed_end`/`reference`) and ordered position | When the operation links its inputs/outputs |
| `local_files` | One row per downloaded file — path, sha256, byte count, media type | After each download completes |

Schema lives in [`src/gflow_cli/data/migrations/0001_initial.sql`](../src/gflow_cli/data/migrations/0001_initial.sql).

### What is NOT recorded

- Signed CDN URLs (e.g., `fifeUrl?Signature=...`) — they expire and contain bearer-style tokens.
- reCAPTCHA tokens, `Authorization` headers, cookies — stripped by [`redact_metadata`](#privacy-and-redaction).
- Thumbnails or preview blobs — only the local file path is recorded.
- Anything from Flow's anonymous gallery — only operations issued by gflow.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ CLI commands (cli_image.py, cli_video.py, image_batch)  │
│         opens OperationRecorder.open(settings)          │
│         passes recorder to runner, closes in finally    │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ OperationRecorder (gflow_cli/data/recorder.py)          │
│ Public methods:                                         │
│   record_upload_image(...)                              │
│   record_generated_images(...)                          │
│   record_started_video(...)                             │
│   record_completed_video(...)                           │
│ Owns redact_metadata + prompt_fields (prompt privacy)   │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ DataRepository (gflow_cli/data/repository.py)           │
│ Row-level upserts + read queries                        │
│ Wraps sqlite3.IntegrityError → DataIntegrityError       │
│ All writes use ON CONFLICT(...) upserts                 │
│ Seed resolvers: by_media_id / by_path / latest / list   │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│ DataStore (gflow_cli/data/store.py)                     │
│ Opens connection with foreign_keys=ON, WAL, busy=5000   │
│ Applies SHA-256-checksummed migrations via              │
│   importlib.resources (works in wheels + sdists)        │
│ transaction(immediate=True) context manager             │
└─────────────────────────────────────────────────────────┘
```

**Boundary rules:**

- The `gflow_cli.data` package never imports `playwright`, `click`, or `rich`.
- Raw `sqlite3.Error` never crosses the package boundary — all failures are wrapped in `DataStoreError` / `DataMigrationError` / `DataIntegrityError`.
- The recorder is composed at the CLI/client boundary, not inside transports.

---

## Recording flow

A typical `gflow image t2i` invocation records this sequence:

1. `OperationRecorder.open(settings)` opens the DB and applies pending migrations. **This happens BEFORE any Flow call** — a DB failure fails fast with exit code 16, so we never bill the user for a generation we can't track.
2. `FlowApiClient` creates the project and generates the image.
3. Outputs are downloaded.
4. After all downloads complete, `recorder.record_generated_images(...)` writes:
   - `profiles` upsert
   - `projects` upsert (`source="generated"`)
   - One `operations` row (mode=`t2i`, prompt + hash + redacted flag)
   - One `assets` row per generated image (kind=`image`, status=`ready`, dimensions, seed, model, aspect, `flow_media_generation_id`)
   - `operation_assets` link as `output` per position
   - `local_files` row per downloaded file with sha256 + bytes + media type
5. `recorder.close()` is called in a `finally` block.

For T2V the sequence is split: `record_started_video(...)` fires the moment the generation response yields a media ID (via the `on_started` callback in `FlowApiClient.generate_video`), then `record_completed_video(...)` updates status + inserts the local file after long polling finishes. This way a paid video is recorded with `status="pending"` even if the long poll later fails.

For batches, each row is recorded individually in a per-row `try/except DataStoreError`, so one row's persistence failure does not stop the rest of the batch.

---

## Persistence-failure handling

The data layer follows a "fail fast before billing, warn after success" contract:

| When | Behavior | Exit code |
|---|---|---|
| DB cannot be opened (permission, path) | Raise `DataStoreError` BEFORE Flow call | 16 |
| Migration fails or detects checksum drift | Raise `DataMigrationError` BEFORE Flow call | 16 |
| DB has a NEWER schema than installed gflow-cli | Raise `DataMigrationError` BEFORE Flow call | 16 |
| Recorder write fails AFTER successful paid generation | Emit `data.persistence_failed_after_success` structlog event with `flow_media_id` + `local_path`; print yellow warning; **return 0** | 0 |
| Batch row N fails to persist | Same warning; later rows still recorded | 0 (or 1 if other failure modes apply) |

**Why "warn and continue" after success?** If your `gflow video t2v` succeeds and the file lands on disk, exiting non-zero solely because the local catalog could not be updated would teach scripts to retry the paid generation. The catalog can be reconciled later by a future `gflow data repair`; the file cannot be un-billed.

Recovery for "newer schema" failures: either upgrade `gflow-cli` to a version that knows the schema, or set `GFLOW_CLI_DB_PATH` to a compatible database.

---

## Privacy and redaction

### Prompt storage

Controlled by `GFLOW_CLI_HISTORY_PROMPTS`:

| Value | Stored | Why |
|---|---|---|
| `store` (default) | Full prompt text + SHA-256 hash + `prompt_redacted=0` | Provenance — "what prompt produced this image" is the most asked question |
| `redacted` | NULL prompt text + SHA-256 hash + `prompt_redacted=1` | Local privacy — share the DB without leaking prompts |

The hash is always stored so you can correlate without the plaintext.

### Metadata redaction

[`redact_metadata`](../src/gflow_cli/data/redaction.py) is applied to every dict that lands in `assets.metadata_json`. It strips:

| Key (case-insensitive) | Replacement |
|---|---|
| `fifeUrl`, `signedUrl`, `downloadUrl`, `mediaUrl` | `<redacted:url>` |
| `token`, `recaptchaToken` | `<redacted:token>` |
| `authorization`, `cookie`, `set-cookie` | `<redacted:secret>` |
| Any string value containing `signature=`, `x-goog-signature=`, `x-goog-credential=`, or `expires=` | `<redacted:url>` |

This means the recorder OWNS redaction — call sites cannot leak signed URLs into the DB even by accident, because `OperationRecorder` always pipes metadata through `redact_metadata` before writing.

### What the DB still contains

Even with `redacted` mode and metadata stripping, the DB stores: profile names, Flow project/media/workflow/operation IDs, local file paths, asset dimensions/seeds/timestamps, model and aspect choices, and prompt hashes. If your threat model requires hiding even profile-level activity, exclude `<GFLOW_CLI_HOME>/gflow.db` from backups or set `GFLOW_CLI_DB_PATH` to a per-task ephemeral location.

See [`SECURITY.md`](SECURITY.md) for the broader threat model.

---

## Querying the data layer

### `gflow data list {projects,images,videos,profiles}` — browse the catalog (v0.9.0+)

```bash
# Newest 20 projects across all profiles
gflow data list projects

# All images for one profile, paginated
gflow data list images --profile ffroliva --limit 50 --offset 0

# Videos as JSONL for piping into jq
gflow data list videos --json | jq '.media_id'

# Profiles with at least one recorded generation
gflow data list profiles
```

Flags shared by all four subcommands:

| Flag | Default | Notes |
|---|---|---|
| `--limit N` | 20 | 1..1000 |
| `--offset N` | 0 | for pagination |
| `--profile NAME` | unset | filter to one profile (not available on `profiles`) |
| `--json` | off | JSONL output, one object per line |

TTY stdout → Rich table; pipe or `--json` → JSONL. Default sort: newest first. Exit code 16 on data-store errors (same `DataStoreError` family as `gflow data media`).

> **`data list profiles` vs `gflow auth list`:** `data list profiles` shows profiles that have **recorded generations** in the catalog; `gflow auth list` shows profiles that have ever **logged in** via `gflow auth login`. A profile that logged in but never generated anything will appear in `auth list` but not in `data list profiles`.

### `gflow data media <media_id>` — read a single asset

```
gflow data media MEDIA_ID [--profile NAME]
```

Returns a Rich-formatted table:

```
gflow data media
┌────────────────┬─────────────────────────────────┐
│ field          │ value                           │
├────────────────┼─────────────────────────────────┤
│ profile        │ default                         │
│ media_id       │ project.../media-abc-123        │
│ project_id     │ project-xyz-456                 │
│ kind           │ image                           │
│ local_path_1   │ C:\out\image.png                │
└────────────────┴─────────────────────────────────┘
```

Exits with code 16 if the media ID is not in the DB. Exits 0 with the table if found.

### Direct SQL inspection

The DB is a plain SQLite file. For ad-hoc reads:

```bash
sqlite3 ~/.gflow/gflow.db
sqlite> SELECT mode, prompt, model, status, started_at
        FROM operations
        ORDER BY started_at DESC LIMIT 10;
```

The schema is fixed at v1; future migrations will be additive (new tables, new nullable columns).

### Programmatic access from your own scripts

```python
from gflow_cli.config import get_settings
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

with DataStore.open(get_settings().resolved_db_path()) as store:
    repo = DataRepository(store)
    # Resolve a seed image candidate by Flow media ID:
    seed = repo.resolve_seed_image("default", "project.../media-abc-123")
    if seed:
        print(seed.flow_project_id, seed.local_path)

    # Latest image for a profile (optionally scoped):
    latest = repo.resolve_latest_image("default", flow_project_id=None,
                                       model=None, aspect_ratio=None)

    # Resolve by local path:
    by_path = repo.resolve_seed_image_by_path("default", Path("C:/out/image.png"))
```

The public read API is documented in [`src/gflow_cli/data/repository.py`](../src/gflow_cli/data/repository.py); all read methods return typed `SeedImage` / `AssetLookup` dataclasses, not raw SQLite rows.

---

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `GFLOW_CLI_DB_PATH` | `<GFLOW_CLI_HOME>/gflow.db` | Override the SQLite file location. Useful for tests, per-account isolation, or shared filesystems. |
| `GFLOW_CLI_HISTORY_PROMPTS` | `store` | `store` keeps prompt text, `redacted` keeps only the SHA-256 hash. |

The DB file is created on first run. Parent directory is auto-created.

See [`CONFIGURATION.md`](CONFIGURATION.md) for the full env-var precedence chain.

---

## Migrations

All schema changes are SQL files under [`src/gflow_cli/data/migrations/`](../src/gflow_cli/data/migrations/), named `NNNN_description.sql` with a 4+ digit zero-padded prefix.

**How they're applied:**

1. On `DataStore.open()`, the runner scans the migrations package via `importlib.resources` (so it works in installed wheels, not just editable installs).
2. Each file is SHA-256-checksummed (with normalized line endings — CRLF → LF, trailing whitespace trimmed) so contributor newline differences don't trigger drift.
3. The `schema_migrations` table records `version`, `filename`, `checksum`, `applied_at`.
4. Pending migrations are applied in a single `BEGIN IMMEDIATE` transaction each.
5. The highest applied version is mirrored into `PRAGMA user_version` as a convenience — `schema_migrations` remains the source of truth.

**Safety guarantees:**

- **Checksum drift** — if an applied migration's file has changed on disk, `DataMigrationError("checksum drift")` is raised. Migrations are immutable once shipped.
- **Newer schema** — if the DB contains a migration version higher than any installed in the package, `DataMigrationError("newer")` is raised. Upgrade gflow-cli or point at a compatible DB.
- **Transactional** — each migration's statements run inside a single transaction. A failure rolls back the entire migration.
- **Statement splitter** — the runner uses a small SQL tokenizer that respects `'`-quoted string literals and `--` line comments, so semicolons inside `'a;b'` don't split prematurely.

**Adding a migration:** drop a new `NNNN_description.sql` file into `src/gflow_cli/data/migrations/`. The packaging test (`tests/data/test_packaging.py`) automatically verifies it ships in the wheel.

---

## Connection settings

Every SQLite connection opened by `DataStore` enables:

| PRAGMA | Value | Why |
|---|---|---|
| `foreign_keys` | `ON` | The schema declares FK constraints; SQLite ignores them by default |
| `journal_mode` | `WAL` | Allows parallel CLI processes to read while one writes |
| `busy_timeout` | `5000` (ms) | Two CLI processes upserting concurrently retry briefly instead of failing immediately with `database is locked` |

Writes are explicitly grouped with `BEGIN IMMEDIATE` via `DataStore.transaction(immediate=True)` to avoid deferred-writer deadlocks under WAL. Connections use `isolation_level=None` so the transaction context manager controls boundaries explicitly.

---

## Error taxonomy

All three errors map to **exit code 16**:

| Class | When raised |
|---|---|
| `DataStoreError` | Generic DB open / read / write failure |
| `DataMigrationError` | Migration apply, checksum drift, or newer-schema |
| `DataIntegrityError` | Constraint violation surfaced from `sqlite3.IntegrityError` (e.g., natural-key conflict on `(profile_name, flow_media_id)`) |

All three are subclasses of `GFlowError` and re-exported through `gflow_cli.exceptions`. They follow the project's RFC 9457 Problem Details pattern.

See [`errors.py::EXIT_CODE_MAP`](../src/gflow_cli/errors.py) for the complete exit-code mapping.

---

## Extending the data layer

### Adding a new operation kind

1. Add the enum value to `OperationKind` in [`data/models.py`](../src/gflow_cli/data/models.py).
2. Add a `record_<kind>(...)` method to `OperationRecorder` following the existing patterns (upsert profile → project → asset → insert operation → link → local files).
3. Wire it into the CLI call site, mirroring the `_run_t2i` / `record_completed_video` patterns: open recorder BEFORE Flow, close in `finally`, wrap the `record_*` call in `try/except DataStoreError` calling `_warn_persistence_failed_after_success(...)`.

### Adding a new column

1. Write a new migration file `NNNN_description.sql` with `ALTER TABLE ... ADD COLUMN`. Keep the column nullable so old rows still validate.
2. Add the field to the relevant `*Record` dataclass with a `None` default at the end.
3. Update `DataRepository` upsert/read methods to handle the new field.
4. Add a test in `tests/data/test_repository.py`.

### Why no ORM

We deliberately use stdlib `sqlite3` with hand-written SQL. Reasons:

- The schema is small (7 tables) and stable.
- Migrations are simpler to reason about as raw SQL.
- No dependency on SQLAlchemy / Alembic adds zero binary weight.
- `importlib.resources` packaging of SQL files is straightforward.

If we ever need Postgres, the migration story changes — but the call sites won't, because they go through `DataRepository`, not raw SQL.

---

## Testing

- **Unit tests** at `tests/data/` use temporary SQLite files and exercise migrations, repository, recorder, redaction.
- **CLI tests** at `tests/cli/test_cli_data.py`, `test_cli_image.py`, `test_cli_video.py` use a `FakeRecorder` to assert the right kwargs reach the recorder without touching SQLite.
- **Batch tests** at `tests/image_batch/test_image_manifest.py` cover the per-row recorder + salvage paths.
- **Packaging test** at `tests/data/test_packaging.py::test_migration_resource_is_discoverable` verifies `0001_initial.sql` is reachable via `importlib.resources`. The `@pytest.mark.integration` companion builds a wheel + sdist and inspects them.

Run the focused data-layer suite:

```bash
PYTHONUTF8=1 uv run python -m pytest tests/data \
    tests/cli/test_cli_data.py tests/cli/test_cli_image.py tests/cli/test_cli_video.py \
    tests/api/test_client.py tests/api/test_video.py \
    tests/api/transports/test_common.py tests/api/transports/test_ui_automation_video.py -q
```

Live tests (`@pytest.mark.live`) and E2E tests (`@pytest.mark.e2e`) are opt-in and do exercise the real data layer end-to-end.

---

## Backlog (future enhancements)

- **`gflow data import` / `gflow data repair`** — backfill rows from existing output folders.
- **`gflow data ls` / `gflow data show <operation_id>`** — richer browsing without leaving the terminal.
- **Cost tracking** — once enough operation metadata is reliable, surface "credits spent this month" per profile / model / aspect.
- **Postgres / Supabase adapter** — for users running gflow on shared workers or wanting cross-machine sync. The repository surface is already designed for this swap.
- **`gflow data export`** — dump the catalog as JSON / Parquet for downstream analytics.

These are tracked in [`PLAN.md`](../PLAN.md) under the data-layer phase backlog.

---

## See also

- [Spec](superpowers/specs/2026-05-24-data-layer-design.md) — original design document with goals, non-goals, and architectural rationale.
- [Plan](superpowers/plans/2026-05-24-data-layer.md) — task-by-task implementation plan.
- [`CONFIGURATION.md`](CONFIGURATION.md) — env-var reference.
- [`USAGE.md`](USAGE.md) — `gflow data media` command reference.
- [`SECURITY.md`](SECURITY.md) — threat model for stored prompts and metadata.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — how `gflow_cli.data` fits into the modular monolith.
