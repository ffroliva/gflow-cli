# gflow-cli Data Layer Design

**Date:** 2026-05-24
**Status:** Draft for user review
**Scope:** Local SQLite persistence for new gflow operations, asset provenance, and I2V seed-image reuse.

## Context

`gflow-cli` can generate and download images and T2V videos, but it does not keep a durable catalog of what happened. The CLI prints IDs and writes files, then loses important provenance:

- which profile generated or uploaded an asset;
- which Flow project owns the asset;
- which Flow media ID and workflow ID should be reused later;
- which prompt, model, aspect ratio, and source images produced an output;
- whether an operation completed, failed, or only partially downloaded.

This is now blocking pragmatic I2V work. To generate a video from a seed image, the system needs a reliable way to resolve a seed image to the Flow media ID, project ID, and profile that own it. Rewalking Flow's UI library every time is slower, brittle, and hard to verify. A local data layer also becomes the foundation for future history, repair/import tooling, cost tracking, and a management UI.

The current code has a related boundary problem: image operations mostly flow through `FlowApiClient`, while `gflow video t2v` directly instantiates `UiAutomationTransport`. The data layer should improve that architecture rather than persist from scattered CLI and Playwright paths.

## Goals

1. Persist new upload, image, batch-image, and T2V operations automatically.
2. Record enough provenance to answer "what generated this file/media ID?" and "which seed image can I reuse for I2V?".
3. Provide an internal read API that can resolve seed images by Flow media ID, local path, or "latest matching image" for the active profile.
4. Normalize T2V behind `FlowApiClient` so image and video orchestration follow the same pattern.
5. Use local SQLite with conservative automatic migrations.
6. Keep the design adapter-shaped so a future Postgres/Supabase backend is possible without changing call sites.

## Non-Goals

- No Postgres or `DATABASE_URL` runtime support in v1.
- No backfill of existing output folders in v1.
- No management UI in v1.
- No full user-facing history browser required in v1, though the schema should support one later.
- No event sourcing, ORM, cloud sync, or remote multi-user database semantics.

Backlog items:

- `gflow data import` or `gflow data repair` to backfill partial records from existing output files.
- Postgres/Supabase adapter and migration dialect after the SQLite schema has settled.
- `gflow history` / `gflow assets` browsing commands.
- Cost tracking once enough operation metadata is reliable.

## Storage Boundary

Add a top-level `gflow_cli.data` module. It owns persistence only. It must not call Flow, Playwright, Click, or Rich.

Public surface:

- `DataStore`: opens the configured SQLite DB, applies migrations, and exposes repositories.
- `OperationRecorder`: higher-level write helper used by `FlowApiClient` and batch/workflow orchestration.
- Read/query helpers for internal flows, especially I2V seed-image resolution.

Rules:

- SQLite is the only v1 runtime backend.
- The default DB path is `$GFLOW_CLI_HOME/gflow.db`.
- Add `GFLOW_CLI_DB_PATH` for tests and advanced local override.
- No direct `sqlite3` usage outside `gflow_cli.data`.
- No global mutable singleton. CLI/client composition passes a store/recorder explicitly or constructs it at the client boundary.
- Store all timestamps as UTC ISO-8601 text.
- Store JSON metadata as text with application-side serialization.
- Use app-generated local IDs for operation rows; use Flow IDs only for Flow resources.

## Migration Strategy

Use checked-in SQL migrations:

```text
src/gflow_cli/data/
├── __init__.py
├── store.py
├── migrations.py
├── recorder.py
├── repositories.py
└── migrations/
    └── 0001_initial.sql
```

The SQLite DB tracks applied migrations in:

```sql
schema_migrations(
  version TEXT PRIMARY KEY,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
)
```

Migration behavior:

- Run pending migrations automatically when `DataStore.open()` is called.
- Apply each migration in a transaction.
- Calculate a checksum for each SQL file.
- If a migration already applied with a different checksum, raise `DataMigrationError`.
- If the DB contains a migration version newer than the installed package, raise `DataMigrationError`.
- Mirror the highest numeric migration into `PRAGMA user_version` as a convenience only; `schema_migrations` remains the source of truth.

This mirrors the useful part of Supabase-style migrations: migration files live in source control, and the database records what has run. We avoid Alembic for v1 because the project does not need SQLAlchemy or multi-dialect migration machinery yet.

Packaging requirements:

- Read migration SQL via `importlib.resources.files(...)`, not filesystem paths relative to the current working directory.
- Update the Hatchling build configuration in `pyproject.toml` so `gflow_cli/data/migrations/*.sql` is included in wheels and sdists.
- Add a packaging test or build check that verifies the installed or built package can discover `0001_initial.sql`.

SQLite connection requirements:

- Enable foreign key enforcement on every connection: `PRAGMA foreign_keys = ON`.
- Enable safer concurrent CLI usage: `PRAGMA journal_mode = WAL`.
- Set a busy timeout, for example `PRAGMA busy_timeout = 5000`, so parallel profile/process runs do not fail immediately with `database is locked`.
- Use Python `sqlite3` with `isolation_level=None` and explicit `BEGIN` / `COMMIT` blocks so connection-level pragmas and transaction boundaries behave predictably.
- Open write transactions explicitly around grouped recorder operations.

## Error Taxonomy Impact

The persistence layer needs typed project errors; raw `sqlite3.Error` must not cross the `gflow_cli.data` boundary.

Add:

```python
class DataStoreError(GFlowError): ...
class DataMigrationError(DataStoreError): ...
class DataIntegrityError(DataStoreError): ...
```

Usage:

- `DataStoreError`: parent for persistence open/read/write failures.
- `DataMigrationError`: migration apply, checksum drift, or newer-schema failures.
- `DataIntegrityError`: impossible local-record states, duplicate conflicting Flow IDs, malformed persisted JSON.
- `ConfigurationError`: still used for invalid user configuration before opening SQLite, such as an unusable `GFLOW_CLI_DB_PATH`.

Impact:

- Add the classes to `errors.py::__all__`.
- Re-export them through `gflow_cli.exceptions`.
- Add a new stable exit code, `16`, for `DataStoreError`.
- Update `EXIT_CODE_MAP`, tests, and the docs exit-code table.
- Keep data errors compatible with RFC 9457 Problem Details logging.
- Exit code `16` applies to pre-Flow persistence setup failures and explicit future `gflow data ...` command failures. A tracking failure after a paid generation already succeeded must not turn the command into a retry-shaped failure.

## Flow ID Semantics

The schema must distinguish local IDs from Flow IDs.

Known Flow IDs from captured responses:

- Flow media ID: `media[].name`. This is the operational asset handle. It is used for image refs and `media.getMediaUrlRedirect?name=...`.
- Flow workflow ID: `workflowId`. This identifies the Flow workflow/library item and is used for archive/cleanup.
- Flow project ID: `projectId`. This scopes media in Flow.
- Flow media generation ID: `media[].image.generatedImage.mediaGenerationId`. Observed for generated images as a separate opaque generation identifier.
- Flow operation ID: video `operations[].operation.name` and `video.operation.name`. In current captures this appears to match the generated media ID, but it should be stored separately when observed.
- Flow batch ID: `mediaGenerationContext.batchId` and workflow metadata `batchId`; useful for grouping submissions.

Naming rule:

- `id`: local database primary key.
- `flow_media_id`: Google Flow `media[].name`.
- `flow_workflow_id`: Google Flow `workflowId`.
- `flow_project_id`: Google Flow `projectId`.
- `flow_media_generation_id`: nullable opaque Flow generation ID.
- `flow_operation_id`: nullable Flow operation ID.
- `flow_batch_id`: nullable Flow batch ID.

I2V must resolve and use `flow_media_id`; `flow_media_generation_id` is provenance unless a future capture proves Flow requires it for a specific route.

## Schema

Initial tables:

```sql
profiles(
  name TEXT PRIMARY KEY,
  profile_dir TEXT,
  first_seen_at TEXT NOT NULL,
  last_used_at TEXT
)

projects(
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  flow_project_id TEXT NOT NULL,
  title TEXT,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(profile_name) REFERENCES profiles(name),
  UNIQUE(profile_name, flow_project_id)
)

operations(
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  flow_project_id TEXT,
  command TEXT,
  mode TEXT NOT NULL,
  prompt TEXT,
  prompt_hash TEXT,
  prompt_redacted INTEGER NOT NULL DEFAULT 0,
  model TEXT,
  aspect_ratio TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  error_type TEXT,
  error_detail TEXT,
  flow_operation_id TEXT,
  flow_batch_id TEXT,
  FOREIGN KEY(profile_name) REFERENCES profiles(name)
)

assets(
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  flow_project_id TEXT,
  flow_media_id TEXT NOT NULL,
  flow_workflow_id TEXT,
  flow_media_generation_id TEXT,
  kind TEXT NOT NULL,
  model TEXT,
  aspect_ratio TEXT,
  width INTEGER,
  height INTEGER,
  duration_seconds REAL,
  seed INTEGER,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  metadata_json TEXT,
  FOREIGN KEY(profile_name) REFERENCES profiles(name),
  UNIQUE(profile_name, flow_media_id)
)

operation_assets(
  operation_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  role TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(operation_id) REFERENCES operations(id),
  FOREIGN KEY(asset_id) REFERENCES assets(id),
  PRIMARY KEY(operation_id, asset_id, role, position)
)

local_files(
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT,
  bytes INTEGER,
  media_type TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(asset_id) REFERENCES assets(id),
  UNIQUE(asset_id, path)
)
```

Important constraints:

- Flow IDs are unique only within a profile for v1: use `(profile_name, flow_media_id)` and `(profile_name, flow_project_id)`.
- `profile_name` is duplicated on key tables to keep common queries simple and future cross-profile scheduling efficient.
- `metadata_json` can hold route-specific fields without requiring a migration for every observed Flow nuance.
- `metadata_json` must not store signed CDN URLs such as `fifeUrl`; those expire and contain bearer-style query tokens.
- Prompt redaction stores `prompt = NULL`, `prompt_redacted = 1`, and `prompt_hash` for correlation.
- `local_files.path` stores the resolved absolute local path in v1. Moving output directories or sharing the DB across Windows/WSL path schemes is out of scope until `gflow data repair` can relink files.

## Prompt Privacy

Store full prompts locally by default because provenance is weak without them.

Add `GFLOW_CLI_HISTORY_PROMPTS`:

- `store` default: persist full prompt text.
- `redacted`: persist `prompt = NULL`, `prompt_redacted = 1`, and a stable prompt hash.

Prompts remain local. No cloud sync or telemetry upload is introduced by this feature.

## Recording Flow

Record facts when they become reliable:

1. At CLI/client boundary, resolve `profile_name` and upsert `profiles`.
2. On `create_project()` response or observed UI editor URL, upsert `projects`.
3. On upload response, upsert an `uploaded_image` asset with `flow_media_id`, `flow_workflow_id`, dimensions, project, and profile.
4. On image generation response, create/update an operation, upsert generated image assets, link input refs and outputs via `operation_assets`, and record model/aspect/seed/prompt.
5. On image download, insert/update `local_files` with path, byte count, and SHA-256 if cheap to compute.
6. On video generation response, record the pending `flow_media_id` immediately.
7. On video status response, update the video asset and operation to success or failure.
8. On video download, insert/update `local_files`.
9. On batch image operations, create one parent operation with multiple output assets rather than treating each output as a separate CLI command.

Persistence failure handling:

- Before any remote Flow action, DB open/migration failures fail fast with exit code `16`.
- After a successful paid remote generation, persistence failures must not discard generated media or skip downloads that can still complete.
- If media was generated and downloaded but the final persistence write fails, keep the local file, emit a visible warning plus a structured `data.persistence_failed_after_success` event, and preserve the command's generation result exit semantics. A successful paid generation with a saved file should not exit non-zero solely because local tracking failed.
- No data-layer failure should be silently swallowed. If a write is intentionally deferred or retried, emit a structured event with the operation ID and failure class.

## T2V Client-Boundary Normalization

The data layer includes a required refactor: T2V must move behind `FlowApiClient`.

Current state:

- Image upload/generation uses `FlowApiClient`.
- `gflow video t2v` directly creates `UiAutomationTransport`.

Target state:

- Add `FlowApiClient.generate_video(...)` as the public orchestration boundary.
- Keep `UiAutomationTransport.generate_video(...)` as the transport implementation detail.
- Update `gflow video t2v` to use `FlowApiClient`, matching image commands.
- Compose the `OperationRecorder` at the client/workflow layer, not inside Click callbacks or low-level Playwright selector methods.

Benefits:

- One boundary for profile, output directory, transport setup, and data recording.
- Fewer special cases before persistence spreads through the codebase.
- A cleaner foundation for `video i2v`, which needs data reads before transport work and data writes after generation.

## Internal Read API For I2V

Add query methods for the first I2V consumers:

- resolve image by `(profile_name, flow_media_id)`;
- resolve image by local file path;
- resolve latest generated/uploaded image for a profile, optionally filtered by project/model/aspect;
- list images in a Flow project for a profile;
- verify that a candidate image exists in the active profile and has a `flow_project_id`.

Return typed dataclasses, not raw SQLite rows. A seed candidate should include:

- `profile_name`;
- `flow_project_id`;
- `flow_media_id`;
- `flow_workflow_id`;
- `kind`;
- dimensions if known;
- local path if downloaded;
- prompt/model/aspect if known.

The first `video i2v` implementation can then accept a local path or Flow media ID, resolve it through the data layer, and pass the operational `flow_media_id` to the transport/API path.

## Backfill And Import

v1 records new operations only.

Backfilling old output files is deferred because historical files usually lack profile, project, prompt, model, aspect, workflow ID, and operation status. A future `gflow data import` or `gflow data repair` command may create partial records marked with an `imported_partial` source/status.

## Testing Strategy

Required tests:

- Migration runner applies `0001_initial.sql` to an empty DB.
- Migration runner is idempotent.
- Checksum drift raises `DataMigrationError`.
- Newer DB schema raises `DataMigrationError`.
- Built wheels/sdists contain SQL migration files and the runner reads them through `importlib.resources`.
- New SQLite connections enable `foreign_keys`, `WAL`, and `busy_timeout`.
- Foreign key enforcement rejects orphaned `operation_assets` and `local_files` rows.
- SQLite exceptions are mapped to `DataStoreError` subclasses.
- Repository upserts are idempotent.
- Prompt redaction stores hash and no prompt text.
- Recording an upload persists profile, project, asset, and operation linkage.
- Recording generated images persists Flow media IDs, workflow IDs, model/aspect/seed, and local files.
- Recording T2V through `FlowApiClient` persists the pending media ID, terminal status, and local file path.
- I2V seed queries resolve by media ID and local path.
- `gflow video t2v` no longer instantiates `UiAutomationTransport` directly.

Use unit tests with temporary SQLite files for the data layer. Integration tests should mock Flow responses and assert data records without live Playwright. Live tests remain opt-in.

## Documentation Impact

Update:

- `docs/CONFIGURATION.md`: `GFLOW_CLI_DB_PATH`, `GFLOW_CLI_HISTORY_PROMPTS`, DB location.
- `docs/USAGE.md`: exit code `16`, local history/provenance note, and any new minimal `gflow data` command if implemented.
- `docs/SECURITY.md`: prompts are stored locally by default; redaction option; DB may contain profile names and asset IDs.
- `docs/ARCHITECTURE.md`: add `gflow_cli.data` to modular monolith modules and note SQLite local persistence.
- `PLAN.md`: replace the broad Phase 6 entry with this concrete data-layer phase and keep import/history UI as backlog.

## Acceptance Criteria

- New operations are recorded in `$GFLOW_CLI_HOME/gflow.db` without user action.
- Migrations run automatically and safely.
- Pre-Flow data-layer failures raise typed `GFlowError` subclasses and map to exit code `16`.
- Post-success tracking failures warn and log without causing paid generations to be blindly retried by generic non-zero-exit automation.
- T2V is invoked via `FlowApiClient`, not direct transport construction in the CLI.
- Image/video asset rows distinguish local IDs from Flow media IDs, workflow IDs, media generation IDs, operation IDs, and batch IDs.
- I2V code can query the data layer for a seed image candidate without opening Flow's library UI.
- Existing generation and download behavior remains compatible when the DB is empty.
- No existing output folders are backfilled by default.
