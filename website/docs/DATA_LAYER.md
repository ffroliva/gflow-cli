# Data Layer

`gflow-cli` keeps a local SQLite catalog of every new image, batch, and video operation. This document explains what it stores, why, and how to use it.

If you only need quick lookup syntax, jump to [Querying the data layer](#querying-the-data-layer). For env vars, see [`CONFIGURATION.md`](CONFIGURATION.md#gflow_cli_db_path) and [external storage configuration](EXTERNAL_STORAGE.md). For exit codes and recovery, see [`USAGE.md`](USAGE.md#exit-codes).

---

## Goals

The data layer exists to answer three questions that the CLI alone cannot:

1. **"What generated this file?"** — given a local path or cloud URI, find the prompt, model, aspect ratio, profile, project, and Flow media ID that produced it.
2. **"Which seed image can I reuse for I2V?"** — given a profile, resolve a candidate image to the Flow `media_id` + `project_id` the video transport needs, without rewalking the Flow UI library.
3. **"What happened during that batch?"** — given a batch run, list every output with success/failure status and the operation provenance.

It is also the foundation for future history browsing, cost tracking, repair/import tooling, and (eventually) a management UI.

### Explicit non-goals (v1)

- No Postgres, cloud-hosted catalog, or `DATABASE_URL` runtime support — SQLite only.
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
| `operations` | One row per CLI command invocation — mode (`upload_image`/`t2i`/`i2i`/`t2v`/`i2v`/`r2v`), prompt, model, aspect, started/completed timestamps, error type/detail, Flow operation/batch IDs, plus [`metadata_json` provenance](#operation-metadata_json-provenance) | Each command run |
| `operation_assets` | Many-to-many between operations and assets, with role (`input`/`output`/`seed_start`/`seed_end`/`reference`) and ordered position | When the operation links its inputs/outputs |
| `local_files` | One row per downloaded asset location — local path or cloud URI, sha256/byte count when local, media type | After each download/upload completes |

Schema lives in [`src/gflow_cli/data/migrations/0001_initial.sql`](../src/gflow_cli/data/migrations/0001_initial.sql).

### Operation `metadata_json` provenance

`operations.metadata_json` answers "what composed this generation?" — the parts
that aren't columns. Keys are written only when they apply, so an operation with
neither a tool nor an entity leaves the column `NULL`:

| Key | Shape | Written when |
|---|---|---|
| `tool` | `{name, version, model, params, config_hash}` (redacted mode: `{name, version, params_hash, config_hash}`) | `--tool` rewrote the prompt |
| `entity_ids` | `["entity-abc", …]` — Flow CHARACTER entity ids, in attach order | `--reference-entity` was used |
| `entity_names` | `["Ana", …]` — display names paired with `--reference-entity-name` | `--reference-entity-name` was used |
| `entity_id` / `name` | scalar id + display name of the entity being created | `character create` only |

Entity keys cover `image t2i`, `image i2i`, and movie R2V — the surfaces that
accept `--reference-entity` — on succeeded **and** failed rows. A FAILED row
carrying `entity_ids` is how a rejected attach (the transport's wire backstop
refusing to report a text-only generation as an entity-referenced success) stays
distinguishable from a run that never requested an entity. Before v0.47.0 the
entity keys were absent on every generation operation, so character provenance
could not be reconstructed for those runs — the gap is forward-only, not
backfillable (issue [#402](https://github.com/ffroliva/gflow-cli/issues/402)).

Unlike `assets.metadata_json`, this column is **not** passed through
`redact_metadata`. Entity ids and names are Flow-side handles the user picked
rather than prompt text, so they stay readable in `redacted` prompt mode — the
same treatment `character create` already gave them. Keep that in mind before
adding a key here: anything prompt-derived must be hashed by its own builder
(as `tool` does) rather than relying on column-level redaction.

### What is NOT recorded

- Signed CDN URLs (e.g., `fifeUrl?Signature=...`) — they expire and contain bearer-style tokens.
- reCAPTCHA tokens, `Authorization` headers, cookies — stripped by [`redact_metadata`](#privacy-and-redaction).
- Thumbnails or preview blobs — only the final local path or cloud URI is recorded.
- Anything from Flow's anonymous gallery — only operations issued by gflow.

### Cloud-backed files

When `GFLOW_CLI_STORAGE_URI` is unset, `local_files.path` stores an absolute
local path and the row may include local `sha256` and byte count values.

When `GFLOW_CLI_STORAGE_URI` is set, generated asset bytes are uploaded to the
configured bucket instead of local disk. The row stores:

- `storage_provider`: `gcs` or `s3`
- `cloud_uri`: full `gs://...` or `s3://...` object URI
- `path`: the same cloud URI at the raw SQLite layer, kept only to satisfy the
  v1 schema's `NOT NULL` and `UNIQUE(asset_id, path)` constraints

Hydrated `LocalFileRecord` objects expose `path=None` for cloud-only rows and
use `cloud_uri` as the authoritative location. See
[EXTERNAL_STORAGE.md](EXTERNAL_STORAGE.md) for setup and verification.

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
   - `local_files` row per downloaded asset with either a local path or cloud URI
5. `recorder.close()` is called in a `finally` block.

For T2V the sequence is split: `record_started_video(...)` fires the moment the generation response yields a media ID (via the `on_started` callback in `FlowApiClient.generate_video`), then `record_completed_video(...)` updates status + inserts the final local path or cloud URI after long polling finishes. This way a paid video is recorded with `status="pending"` even if the long poll later fails.

For batches, each row is recorded individually in a per-row `try/except DataStoreError`, so one row's persistence failure does not stop the rest of the batch.

### Failure recording (#341, v0.39.0+)

Failed generations are first-class catalog citizens: every paid-generation
orchestrator records a terminal `status="failed"` operation row before
re-raising the error. The funnel covers `video t2v/i2v/r2v`, `video chain`
(per-link, via the `on_link_failed` hook), `image t2i/i2i`, multi-prompt
t2i / `gflow run` / `image batch` manifest rows, `movie run` scenes, and the
async worker (which mirrors its `generation_queue.error_json` failure into
`operations` — the operations table owns terminal truth).

- `error_type` is the last segment of the exception's RFC 9457 `problem_type`
  URI (`waf-rejection`, `content-policy`, `auth-expired`,
  `transport-timeout`, ...) — the taxonomy is declared per `GFlowError`
  subclass, never hand-mapped. Non-`GFlowError` exceptions store the class
  name, with `error_detail` reduced to a SHA-256 hash of the message (never
  the text — it may carry tokens).
- Video failures UPDATE every `on_started` STARTED row to `failed` (a
  `count>1` request fires the callback per output — no sibling row is
  stranded); failures before `on_started`, and all image failures, INSERT a
  fresh row with full request metadata.
- A poll that COMPLETES with a Flow-reported failure (`succeeded=false`) is
  recorded as `failed` with `error_type=generation-failed` and the
  `failure_reasons` as detail — not as a success.
- The write itself is best-effort: `record_failed_operation_safe` wraps it in
  a broad warn-only guard (`failure_record_skipped` structlog event) so a
  data-layer fault can never mask the generation error or change the exit code.
- The character-create saga deliberately keeps its STARTED rows for resume —
  it is excluded from the failure funnel by design.
- `failed` rows have **no automatic cap** by design — there is no background
  pruning. Use `gflow data errors prune --older-than <age>` for explicit
  retention and `gflow data errors export` to archive history first (see
  [`data errors`](#gflow-data-errors--bounded-retention--export-345) below,
  [#345](https://github.com/ffroliva/gflow-cli/issues/345)). Note `gflow data
  prune` is unrelated: it targets stale `local_files`, not failure history.

---

## Persistence-failure handling

The data layer follows a "fail fast before billing, warn after success" contract:

| When | Behavior | Exit code |
|---|---|---|
| DB cannot be opened (permission, path) | Raise `DataStoreError` BEFORE Flow call | 16 |
| Migration fails or detects checksum drift | Raise `DataMigrationError` BEFORE Flow call | 16 |
| DB has a NEWER schema than installed gflow-cli | Raise `DataMigrationError` BEFORE Flow call | 16 |
| Recorder write fails AFTER successful paid generation | Emit `data.persistence_failed_after_success` structlog event with `flow_media_id` + `local_path` when available; print yellow warning; **return 0** | 0 |
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

### `error_detail` redaction (#341)

Failure rows persist the exception's Problem-Details `detail` string through
[`redact_error_detail`](../src/gflow_cli/data/redaction.py): free-text
`Bearer …` / `SAPISIDHASH …` tokens and auth-cookie pairs become
`<redacted:secret>`, URLs carrying signed-query markers become
`<redacted:url>`, and the result is truncated to 500 chars post-redaction as
defense-in-depth. The experimental REST transports also redact response bodies
BEFORE truncating them into exception messages, so a clipped secret can't
reach the DB. Non-`GFlowError` details are stored only as SHA-256 hashes.
Prompts on FAILED rows honor `GFLOW_CLI_HISTORY_PROMPTS` exactly like success
rows — note that in `store` mode this includes prompts of content-policy
REJECTED generations (the same opt-in that stores successful prompts).

### What the DB still contains

Even with `redacted` mode and metadata stripping, the DB stores: profile names, Flow project/media/workflow/operation IDs, local file paths or cloud URIs, asset dimensions/seeds/timestamps, model and aspect choices, and prompt hashes. If your threat model requires hiding even profile-level activity, exclude `<GFLOW_CLI_HOME>/gflow.db` from backups or set `GFLOW_CLI_DB_PATH` to a per-task ephemeral location.

See [`SECURITY.md`](SECURITY.md) for the broader threat model.

---

## Querying the data layer

### `gflow data list {projects,images,videos,profiles,errors}` — browse the catalog (v0.9.0+)

```bash
# Newest 20 projects across all profiles
gflow data list projects

# All images for one profile, aggregated by asset by default
gflow data list images --profile ffroliva --limit 50 --offset 0

# Show every local file copy instead of aggregating
gflow data list images --all-copies

# Videos as JSONL for piping into jq
gflow data list videos --json | jq '.media_id'

# Profiles with at least one recorded generation
gflow data list profiles

# Failed generations, newest first (#341): when, which command/mode/model,
# stable error_type (waf-rejection, content-policy, ...) + redacted detail.
# The dataset for WAF-cadence and reliability analysis.
gflow data list errors --profile my-profile --json | jq '{started_at, error_type}'
```

Flags shared by all five subcommands:

| Flag | Default | Notes |
|---|---|---|
| `--limit N` | 20 | 1..1000 |
| `--offset N` | 0 | for pagination |
| `--profile NAME` | unset | filter to one profile (not available on `profiles`) |
| `--json` | off | JSONL output, one object per line |

The `images` and `videos` subcommands support an additional flag:

| Flag | Default | Notes |
|---|---|---|
| `--all-copies` | off | Show one row per local file instead of one row per asset. |

By default, `images` and `videos` aggregate rows by Flow media ID. If an asset
has multiple local copies (e.g. re-downloaded to different paths), they appear
as a single row with a `COPIES` count and the path of the latest copy.

TTY stdout → Rich table; pipe or `--json` → JSONL. Default sort: newest first. Exit code 16 on data-store errors (same `DataStoreError` family as `gflow data media`). A **missing or freshly-created** DB is NOT a data-store error — `data list` auto-creates the schema via `DataStore.open()` and returns exit 0 with an empty result (see [#88](https://github.com/ffroliva/gflow-cli/issues/88)).

> **`data list profiles` vs `gflow auth list`:** `data list profiles` shows profiles that have **recorded generations** in the catalog; `gflow auth list` shows profiles that have ever **logged in** via `gflow auth login`. A profile that logged in but never generated anything will appear in `auth list` but not in `data list profiles`.

### `gflow data prune` — clean up stale entries (v0.9.1+)

Remove `local_files` rows whose paths no longer exist on disk. Useful after
test runs that wrote to temporary directories, or after manually deleting
media files.

```bash
# Preview dead rows without deleting
gflow data prune --dry-run

# Delete stale local_files entries
gflow data prune
```

Only **local files** (where `storage_provider` is NULL) are scanned; cloud-stored
assets are ignored to prevent accidental pruning of remote objects.

### `gflow data errors` — bounded retention + export (#345)

Maintain the failed-operation history (the `data list errors` funnel). Two
subcommands, both explicit operator actions — there is **no automatic/background
pruning**.

```bash
# Archive every failure as JSONL (unbounded; newest-first) before deleting
gflow data errors export -o failures.jsonl

# Archive only the slice you are about to prune
gflow data errors export --older-than 90d -o old-failures.jsonl

# Preview what a retention prune would delete (no changes)
gflow data errors prune --older-than 90d --dry-run

# Delete failures older than 90 days
gflow data errors prune --older-than 90d
```

- `--older-than` takes an age like `90d` / `24h` / `30m` (`d` days, `h` hours,
  `m` minutes; positive integers only). It is **required** on `prune` so
  deletion is always a deliberate age choice; on `export` it is optional
  (default: all failures).
- `--profile NAME` scopes either command to one profile.
- `export` writes JSONL to `-o/--output FILE`, or to stdout when omitted —
  reusing the same serialization as `data list errors --json`.
- Deletion errors map to **exit 16** (`DataStoreError`), consistent with the
  rest of `gflow data`.

Archive first if you need the failure history for offline WAF-cadence /
reliability analysis — pruning is irreversible.

### `gflow data media <media_id>` — read a single asset

```
gflow data media MEDIA_ID [--profile NAME]
```

Without `--profile`, the lookup spans **every profile** in the catalog (matching the cross-profile default of `gflow data list`). Pass `--profile NAME` to scope to a specific profile in the rare case where the same Flow `media_id` exists under multiple profiles — `data media` refuses to guess and prints the candidate profiles annotated with their `kind` (`image` / `video`) so you can disambiguate. See [#87](https://github.com/ffroliva/gflow-cli/issues/87).

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

Cloud-backed rows use `cloud_uri_N` instead:

```
│ cloud_uri_1    │ s3://gflow/out/image.jpg        │
```

Exits with code 16 if the media ID is not in the DB or if multiple profiles claim it without `--profile` disambiguation. Exits 0 with the table if exactly one match is found.

### Direct SQL inspection

The DB is a plain SQLite file. For ad-hoc reads:

```bash
sqlite3 ~/.gflow/gflow.db
sqlite> SELECT mode, prompt, model, status, started_at
        FROM operations
        ORDER BY started_at DESC LIMIT 10;

sqlite> SELECT storage_provider, cloud_uri, path
        FROM local_files
        WHERE cloud_uri IS NOT NULL
        ORDER BY created_at DESC LIMIT 10;
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

Cloud-only assets have `LocalFileRecord.path is None` and carry their object
location in `LocalFileRecord.cloud_uri`; path-based seed-image resolution is
local-only.

The public read API is documented in [`src/gflow_cli/data/repository.py`](../src/gflow_cli/data/repository.py); all read methods return typed `SeedImage` / `AssetLookup` dataclasses, not raw SQLite rows.

---

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `GFLOW_CLI_DB_PATH` | `<GFLOW_CLI_HOME>/gflow.db` | Override the SQLite file location. Useful for tests, per-account isolation, or shared filesystems. |
| `GFLOW_CLI_HISTORY_PROMPTS` | `store` | `store` keeps prompt text, `redacted` keeps only the SHA-256 hash. |
| `GFLOW_CLI_STORAGE_URI` | unset | Optional S3/GCS output prefix; cloud-backed rows store `storage_provider` and `cloud_uri`. |

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

Distinct from the above (which are errors OF the data layer), the
`operations.error_type` column stores the taxonomy of RECORDED generation
failures — the last segment of each exception's `problem_type` URI. Common
values: `waf-rejection` (HTTP 403 / WAF), `content-policy`, `auth-expired`,
`transport-timeout`, `wire-format`, `rate-limit`, `ui-mode-unavailable`
(cohort pin), `media-attribution`. Query them with `gflow data list errors`.

---

## Extending the data layer

### Adding a new operation kind

1. Add the enum value to `OperationKind` in [`data/models.py`](../src/gflow_cli/data/models.py).
2. Add a `record_<kind>(...)` method to `OperationRecorder` following the existing patterns (upsert profile → project → asset → insert operation → link → asset location rows).
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
- **`gflow data export`** (full catalog → JSON / Parquet) — the failed-operation slice shipped as `gflow data errors export` (JSONL, #345); a full-catalog / Parquet dump for downstream analytics is still open.

These are tracked in [`PLAN.md`](../PLAN.md) under the data-layer phase backlog.

---

## See also

- [`CONFIGURATION.md`](CONFIGURATION.md) — env-var reference.
- [`EXTERNAL_STORAGE.md`](EXTERNAL_STORAGE.md) — S3, MinIO, and GCS output configuration.
- [`USAGE.md`](USAGE.md) — `gflow data media` command reference.
- [`SECURITY.md`](SECURITY.md) — threat model for stored prompts and metadata.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — how `gflow_cli.data` fits into the modular monolith.
