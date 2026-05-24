# Data Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local SQLite data layer that records profiles, Flow projects, generated or uploaded media, local files, and operation provenance for new gflow operations.

**Architecture:** Introduce `gflow_cli.data` as a small SQLite-backed repository plus an `OperationRecorder` facade. CLI/API call sites record through the facade after successful Flow operations, while DB open and migrations fail fast before paid remote calls. Video generation is normalized behind a `FlowApiClient.generate_video` method so image and video persistence share one client boundary.

**Tech Stack:** Python 3.11 stdlib `sqlite3`, `importlib.resources`, Pydantic settings, Click, structlog, pytest, ruff, pyright strict.

---

Source spec: `docs/superpowers/specs/2026-05-24-data-layer-design.md`

## File Structure

- Create `src/gflow_cli/data/__init__.py`: public data-layer exports.
- Create `src/gflow_cli/data/models.py`: enum and dataclass value objects used by repositories and recorder.
- Create `src/gflow_cli/data/store.py`: SQLite connection setup, PRAGMAs, explicit transactions, and migration runner.
- Create `src/gflow_cli/data/repository.py`: row-level upsert/query methods.
- Create `src/gflow_cli/data/redaction.py`: prompt hashing and metadata redaction.
- Create `src/gflow_cli/data/recorder.py`: high-level operation recording facade.
- Create `src/gflow_cli/data/migrations/__init__.py`: package marker for `importlib.resources`.
- Create `src/gflow_cli/data/migrations/001_initial.sql`: v1 schema.
- Create `src/gflow_cli/cli_data.py`: minimal read-only `gflow data` commands.
- Modify `src/gflow_cli/config.py`: add `db_path`, `history_prompts`, and `resolved_db_path()`.
- Modify `src/gflow_cli/errors.py`: add data-layer error classes and exit code 16.
- Modify `src/gflow_cli/paths.py`: add `database_path(home)`.
- Modify `src/gflow_cli/api/dto.py`: carry image `mediaGenerationId`.
- Modify `src/gflow_cli/api/video.py`: carry video `project_id` and `flow_operation_id`.
- Modify `src/gflow_cli/api/client.py`: add `generate_video` and data-layer-neutral result plumbing.
- Modify `src/gflow_cli/api/transports/base.py`: expose a video-capable protocol without forcing every transport to support video.
- Modify `src/gflow_cli/api/transports/_common.py`: move project-ID URL extraction here.
- Modify `src/gflow_cli/api/transports/ui_automation.py`: import the shared project-ID extractor.
- Modify `src/gflow_cli/api/transports/ui_automation_video.py`: populate video project and operation IDs.
- Modify `src/gflow_cli/cli.py`: register the new `data` command group.
- Modify `src/gflow_cli/cli_image.py`: record upload, t2i, and i2i operations.
- Modify `src/gflow_cli/cli_video.py`: route T2V through `FlowApiClient` and record video operations.
- Modify `src/gflow_cli/image_batch.py`: record image batch rows and partial-result salvage.
- Modify `pyproject.toml`: include SQL migrations in built wheels.
- Create tests under `tests/data/`.
- Modify existing CLI/API tests for new arguments and video client boundary.
- Modify docs: `docs/CONFIGURATION.md`, `docs/USAGE.md`, `docs/SECURITY.md`, `docs/ARCHITECTURE.md`, and `PLAN.md`.

## Task 1: Settings, Paths, and Error Taxonomy

**Files:**
- Modify: `src/gflow_cli/config.py`
- Modify: `src/gflow_cli/errors.py`
- Modify: `src/gflow_cli/paths.py`
- Create: `tests/data/test_settings_and_errors.py`

- [ ] **Step 1: Write failing settings and error tests**

Create `tests/data/test_settings_and_errors.py`:

```python
from pathlib import Path

import pytest

from gflow_cli.config import Settings
from gflow_cli.errors import (
    EXIT_CODE_MAP,
    DataIntegrityError,
    DataMigrationError,
    DataStoreError,
    GFlowError,
)
from gflow_cli.paths import database_path


def test_database_path_defaults_under_home(tmp_path: Path) -> None:
    assert database_path(tmp_path) == tmp_path / "gflow.db"


def test_settings_resolves_default_db_path(tmp_path: Path) -> None:
    settings = Settings(home=tmp_path)
    assert settings.resolved_db_path() == tmp_path / "gflow.db"


def test_settings_respects_db_path_override(tmp_path: Path) -> None:
    override = tmp_path / "custom.db"
    settings = Settings(home=tmp_path, db_path=override)
    assert settings.resolved_db_path() == override


def test_settings_accepts_prompt_redaction_modes(tmp_path: Path) -> None:
    assert Settings(home=tmp_path, history_prompts="full").history_prompts == "full"
    assert Settings(home=tmp_path, history_prompts="redacted").history_prompts == "redacted"


def test_data_errors_extend_gflow_error() -> None:
    assert issubclass(DataStoreError, GFlowError)
    assert issubclass(DataMigrationError, DataStoreError)
    assert issubclass(DataIntegrityError, DataStoreError)


@pytest.mark.parametrize(
    "exc_type",
    [DataStoreError, DataMigrationError, DataIntegrityError],
)
def test_data_errors_use_exit_code_16(exc_type: type[DataStoreError]) -> None:
    assert next(code for cls, code in EXIT_CODE_MAP.items() if issubclass(exc_type, cls)) == 16
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
uv run python -m pytest tests/data/test_settings_and_errors.py -q
```

Expected: failures for missing `database_path`, missing `Settings.db_path`, missing `Settings.history_prompts`, and missing data error classes.

- [ ] **Step 3: Add path and settings fields**

Modify `src/gflow_cli/paths.py`:

```python
def database_path(home: Path) -> Path:
    """SQLite DB path under GFLOW_CLI_HOME."""
    return home / "gflow.db"
```

Modify `src/gflow_cli/config.py` imports:

```python
from typing import Literal
```

Add these fields to `Settings` under the path/profile section:

```python
    db_path: Path | None = Field(
        default=None,
        description=(
            "SQLite data-layer path. Defaults to <GFLOW_CLI_HOME>/gflow.db. "
            "Override with GFLOW_CLI_DB_PATH for tests or advanced local setups."
        ),
    )
    history_prompts: Literal["full", "redacted"] = Field(
        default="full",
        description=(
            "Controls prompt persistence in the local DB. 'full' stores prompt text; "
            "'redacted' stores only prompt_hash and prompt_redacted=1."
        ),
    )
```

Add this method to `Settings`:

```python
    def resolved_db_path(self) -> Path:
        return self.db_path or paths.database_path(self.home)
```

- [ ] **Step 4: Add data-layer errors**

Modify `src/gflow_cli/errors.py`:

```python
class DataStoreError(GFlowError):
    """Raised when the local data layer cannot open, read, or write SQLite."""

    problem_type = "https://gflow-cli.dev/errors/data-store"
    title = "Data store error"
    _default_remediation = (
        "Check GFLOW_CLI_DB_PATH and filesystem permissions. "
        "If the DB was created by a newer gflow-cli, upgrade gflow-cli or "
        "point GFLOW_CLI_DB_PATH at a compatible database."
    )


class DataMigrationError(DataStoreError):
    """Raised when local SQLite schema migration cannot proceed safely."""

    problem_type = "https://gflow-cli.dev/errors/data-migration"
    title = "Data migration error"


class DataIntegrityError(DataStoreError):
    """Raised when repository writes violate expected local DB constraints."""

    problem_type = "https://gflow-cli.dev/errors/data-integrity"
    title = "Data integrity error"
```

Add the new classes to `__all__`, and add the exit-code entries before `BrowserSessionClosedError`:

```python
    DataMigrationError: 16,
    DataIntegrityError: 16,
    DataStoreError: 16,
```

- [ ] **Step 5: Run the task tests**

Run:

```powershell
uv run python -m pytest tests/data/test_settings_and_errors.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/gflow_cli/config.py src/gflow_cli/errors.py src/gflow_cli/paths.py tests/data/test_settings_and_errors.py
git commit -m "feat(data): add settings and errors"
```

## Task 2: SQLite Store and Migrations

**Files:**
- Create: `src/gflow_cli/data/__init__.py`
- Create: `src/gflow_cli/data/migrations/__init__.py`
- Create: `src/gflow_cli/data/migrations/001_initial.sql`
- Create: `src/gflow_cli/data/store.py`
- Modify: `pyproject.toml`
- Create: `tests/data/test_store_migrations.py`

- [ ] **Step 1: Write failing migration tests**

Create `tests/data/test_store_migrations.py`:

```python
import sqlite3
from pathlib import Path

import pytest

from gflow_cli.data.store import (
    DataStore,
    _checksum_sql,
    _normalize_sql_for_checksum,
)
from gflow_cli.errors import DataMigrationError


def test_normalize_sql_for_checksum_trims_line_endings() -> None:
    assert _normalize_sql_for_checksum("CREATE TABLE x(id);\r\n  \r\n") == "CREATE TABLE x(id);"


def test_checksum_sql_uses_sha256() -> None:
    checksum = _checksum_sql("CREATE TABLE x(id);\n")
    assert len(checksum) == 64
    assert checksum == _checksum_sql("CREATE TABLE x(id);\r\n")


def test_open_creates_schema_and_migration_row(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    with DataStore.open(db) as store:
        rows = store.conn.execute("SELECT version FROM schema_migrations").fetchall()
        assert [row["version"] for row in rows] == [1]
        assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert store.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_newer_schema_raises(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, filename TEXT, "
            "checksum TEXT, applied_at TEXT)"
        )
        conn.execute(
            "INSERT INTO schema_migrations(version, filename, checksum, applied_at) "
            "VALUES (999, '999.sql', 'abc', '2026-05-24T00:00:00Z')"
        )
    with pytest.raises(DataMigrationError, match="newer"):
        DataStore.open(db)


def test_checksum_drift_raises(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    with DataStore.open(db):
        pass
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE schema_migrations SET checksum='bad' WHERE version=1")
    with pytest.raises(DataMigrationError, match="checksum"):
        DataStore.open(db)


def test_foreign_keys_reject_orphan_rows(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute(
                "INSERT INTO local_files(id, asset_id, path, media_kind, created_at) "
                "VALUES ('file-1', 'missing', 'C:/missing.png', 'image', '2026-05-24T00:00:00Z')"
            )


def test_transaction_uses_begin_immediate_for_writes(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        with store.transaction(immediate=True):
            store.conn.execute(
                "INSERT INTO profiles(name, profile_dir, created_at, updated_at) "
                "VALUES ('default', 'C:/profiles/default', '2026-05-24T00:00:00Z', "
                "'2026-05-24T00:00:00Z')"
            )
        row = store.conn.execute("SELECT name FROM profiles").fetchone()
        assert row["name"] == "default"
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
uv run python -m pytest tests/data/test_store_migrations.py -q
```

Expected: import failures for `gflow_cli.data.store`.

- [ ] **Step 3: Add package markers**

Create `src/gflow_cli/data/__init__.py`:

```python
"""SQLite-backed local data layer for gflow-cli."""

from gflow_cli.data.store import DataStore

__all__ = ["DataStore"]
```

Create `src/gflow_cli/data/migrations/__init__.py`:

```python
"""Packaged SQL migrations for gflow-cli's local SQLite data layer."""
```

- [ ] **Step 4: Add the initial SQL schema**

Create `src/gflow_cli/data/migrations/001_initial.sql`:

```sql
CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  filename TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE profiles (
  name TEXT PRIMARY KEY,
  profile_dir TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  flow_project_id TEXT NOT NULL,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(profile_name) REFERENCES profiles(name),
  UNIQUE(profile_name, flow_project_id)
);

CREATE TABLE assets (
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  project_id TEXT,
  flow_media_id TEXT NOT NULL,
  flow_workflow_id TEXT,
  flow_media_generation_id TEXT,
  media_kind TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  status TEXT NOT NULL,
  prompt TEXT,
  prompt_hash TEXT,
  prompt_redacted INTEGER NOT NULL DEFAULT 0,
  model TEXT,
  aspect_ratio TEXT,
  seed INTEGER,
  width INTEGER,
  height INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(profile_name) REFERENCES profiles(name),
  FOREIGN KEY(project_id) REFERENCES projects(id),
  UNIQUE(profile_name, flow_media_id)
);

CREATE TABLE operations (
  id TEXT PRIMARY KEY,
  profile_name TEXT NOT NULL,
  project_id TEXT,
  operation_kind TEXT NOT NULL,
  status TEXT NOT NULL,
  flow_operation_id TEXT,
  flow_media_id TEXT,
  prompt TEXT,
  prompt_hash TEXT,
  prompt_redacted INTEGER NOT NULL DEFAULT 0,
  model TEXT,
  aspect_ratio TEXT,
  seed INTEGER,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(profile_name) REFERENCES profiles(name),
  FOREIGN KEY(project_id) REFERENCES projects(id),
  UNIQUE(profile_name, flow_operation_id)
);

CREATE TABLE operation_assets (
  operation_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  role TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(operation_id) REFERENCES operations(id),
  FOREIGN KEY(asset_id) REFERENCES assets(id),
  UNIQUE(operation_id, role, position),
  PRIMARY KEY(operation_id, asset_id, role, position)
);

CREATE TABLE local_files (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  path TEXT NOT NULL,
  media_kind TEXT NOT NULL,
  mime_type TEXT,
  bytes_size INTEGER,
  sha256 TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE,
  UNIQUE(asset_id, path)
);

CREATE INDEX idx_projects_profile_flow ON projects(profile_name, flow_project_id);
CREATE INDEX idx_assets_profile_media ON assets(profile_name, flow_media_id);
CREATE INDEX idx_assets_project_created ON assets(project_id, created_at);
CREATE INDEX idx_assets_kind_created ON assets(media_kind, created_at);
CREATE INDEX idx_operations_profile_created ON operations(profile_name, started_at);
CREATE INDEX idx_operation_assets_asset ON operation_assets(asset_id);
CREATE INDEX idx_local_files_asset ON local_files(asset_id);
```

- [ ] **Step 5: Implement `DataStore`**

Create `src/gflow_cli/data/store.py`:

```python
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from gflow_cli.errors import DataMigrationError, DataStoreError

MIGRATION_PACKAGE = "gflow_cli.data.migrations"
BUSY_TIMEOUT_MS = 5000


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_sql_for_checksum(sql: str) -> str:
    normalized = sql.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).rstrip()


def _checksum_sql(sql: str) -> str:
    return hashlib.sha256(_normalize_sql_for_checksum(sql).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    sql: str
    checksum: str


def _load_migrations() -> list[Migration]:
    files = []
    for item in resources.files(MIGRATION_PACKAGE).iterdir():
        if item.name.endswith(".sql"):
            files.append(item)
    migrations: list[Migration] = []
    for file_ref in sorted(files, key=lambda p: p.name):
        prefix = file_ref.name.split("_", 1)[0]
        version = int(prefix)
        sql = file_ref.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                filename=file_ref.name,
                sql=sql,
                checksum=_checksum_sql(sql),
            )
        )
    return migrations


class DataStore:
    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        self.conn = conn
        self.path = path

    @classmethod
    def open(cls, path: Path) -> DataStore:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            store = cls(conn=conn, path=path)
            store.apply_migrations()
            return store
        except DataMigrationError:
            raise
        except sqlite3.Error as exc:
            raise DataStoreError(detail=str(exc), route="data.open") from exc

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> DataStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[None]:
        try:
            self.conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def apply_migrations(self) -> None:
        migrations = _load_migrations()
        if not migrations:
            raise DataMigrationError(detail="no SQL migrations packaged", route="data.migrate")

        table_exists = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        latest_known = migrations[-1].version
        if table_exists is not None:
            row = self.conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            current = int(row[0] or 0)
            if current > latest_known:
                raise DataMigrationError(
                    detail=f"database schema {current} is newer than installed schema {latest_known}",
                    route="data.migrate",
                )

        applied = {
            int(row["version"]): str(row["checksum"])
            for row in self.conn.execute(
                "SELECT version, checksum FROM schema_migrations"
            ).fetchall()
        } if table_exists is not None else {}

        for migration in migrations:
            existing = applied.get(migration.version)
            if existing is not None:
                if existing != migration.checksum:
                    raise DataMigrationError(
                        detail=f"migration {migration.version} checksum drift",
                        route="data.migrate",
                    )
                continue
            with self.transaction():
                self.conn.executescript(migration.sql)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, filename, checksum, applied_at) "
                    "VALUES (?, ?, ?, ?)",
                    (migration.version, migration.filename, migration.checksum, _utc_now()),
                )
                self.conn.execute(f"PRAGMA user_version = {migration.version}")
```

- [ ] **Step 6: Include SQL migrations in wheels**

Modify `pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/gflow_cli/data/migrations" = "gflow_cli/data/migrations"
```

- [ ] **Step 7: Run migration tests**

Run:

```powershell
uv run python -m pytest tests/data/test_store_migrations.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```powershell
git add pyproject.toml src/gflow_cli/data tests/data/test_store_migrations.py
git commit -m "feat(data): add sqlite store and migrations"
```

## Task 3: Repository and Read Queries

**Files:**
- Create: `src/gflow_cli/data/models.py`
- Create: `src/gflow_cli/data/repository.py`
- Create: `tests/data/test_repository.py`

- [ ] **Step 1: Write failing repository tests**

Create `tests/data/test_repository.py`:

```python
from pathlib import Path

import pytest

from gflow_cli.data.models import (
    AssetKind,
    AssetRecord,
    LocalFileRecord,
    OperationAssetRole,
    OperationKind,
    OperationRecord,
    OperationStatus,
    ProjectRecord,
    SourceKind,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataIntegrityError


def test_upserts_project_asset_operation_and_file(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/profile_default"))
        project = repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="gflow-cli t2i",
                metadata_json={},
            )
        )
        asset = repo.upsert_asset(
            AssetRecord(
                id="asset-local",
                profile_name="default",
                project_id=project.id,
                flow_media_id="media-1",
                flow_workflow_id="workflow-1",
                flow_media_generation_id="generation-1",
                media_kind=AssetKind.IMAGE,
                source_kind=SourceKind.GENERATED,
                status="ready",
                prompt="a prompt",
                prompt_hash=None,
                prompt_redacted=False,
                model="NARWHAL",
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                seed=123,
                width=1024,
                height=1792,
                metadata_json={},
            )
        )
        operation = repo.insert_operation(
            OperationRecord(
                id="operation-local",
                profile_name="default",
                project_id=project.id,
                operation_kind=OperationKind.T2I,
                status=OperationStatus.SUCCEEDED,
                flow_operation_id=None,
                flow_media_id="media-1",
                prompt="a prompt",
                prompt_hash=None,
                prompt_redacted=False,
                model="NARWHAL",
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                seed=123,
                metadata_json={},
            )
        )
        repo.link_operation_asset(operation.id, asset.id, OperationAssetRole.OUTPUT, 0)
        repo.upsert_local_file(
            LocalFileRecord(
                id="file-local",
                asset_id=asset.id,
                path=tmp_path / "media-1.png",
                media_kind=AssetKind.IMAGE,
                mime_type="image/png",
                bytes_size=10,
                sha256="a" * 64,
            )
        )

        found = repo.get_asset_by_flow_media_id("default", "media-1")
        assert found is not None
        assert found.flow_project_id == "flow-project-1"
        assert found.local_files[0].path == tmp_path / "media-1.png"


def test_operation_asset_position_is_unique(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/profile_default"))
        project = repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="title",
                metadata_json={},
            )
        )
        asset_one = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-1",
                profile_name="default",
                project_id=project.id,
                flow_media_id="media-1",
            )
        )
        asset_two = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-2",
                profile_name="default",
                project_id=project.id,
                flow_media_id="media-2",
            )
        )
        operation = repo.insert_operation(
            OperationRecord.minimal(
                id="operation-1",
                profile_name="default",
                project_id=project.id,
                operation_kind=OperationKind.I2I,
            )
        )
        repo.link_operation_asset(operation.id, asset_one.id, OperationAssetRole.INPUT, 0)
        with pytest.raises(DataIntegrityError):
            repo.link_operation_asset(operation.id, asset_two.id, OperationAssetRole.INPUT, 0)
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
uv run python -m pytest tests/data/test_repository.py -q
```

Expected: import failures for `models` and `repository`.

- [ ] **Step 3: Add model dataclasses**

Create `src/gflow_cli/data/models.py` with these definitions:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]


class AssetKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class SourceKind(StrEnum):
    UPLOADED = "uploaded"
    GENERATED = "generated"


class OperationKind(StrEnum):
    UPLOAD_IMAGE = "upload_image"
    T2I = "t2i"
    I2I = "i2i"
    T2V = "t2v"
    I2V = "i2v"
    R2V = "r2v"


class OperationStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OperationAssetRole(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    SEED_START = "seed_start"
    SEED_END = "seed_end"
    REFERENCE = "reference"


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    profile_name: str
    flow_project_id: str
    title: str
    metadata_json: JsonObject


@dataclass(frozen=True)
class AssetRecord:
    id: str
    profile_name: str
    project_id: str | None
    flow_media_id: str
    flow_workflow_id: str | None
    flow_media_generation_id: str | None
    media_kind: AssetKind
    source_kind: SourceKind
    status: str
    prompt: str | None
    prompt_hash: str | None
    prompt_redacted: bool
    model: str | None
    aspect_ratio: str | None
    seed: int | None
    width: int | None
    height: int | None
    metadata_json: JsonObject

    @classmethod
    def minimal_image(
        cls,
        *,
        id: str,
        profile_name: str,
        project_id: str | None,
        flow_media_id: str,
    ) -> AssetRecord:
        return cls(
            id=id,
            profile_name=profile_name,
            project_id=project_id,
            flow_media_id=flow_media_id,
            flow_workflow_id=None,
            flow_media_generation_id=None,
            media_kind=AssetKind.IMAGE,
            source_kind=SourceKind.GENERATED,
            status="ready",
            prompt=None,
            prompt_hash=None,
            prompt_redacted=False,
            model=None,
            aspect_ratio=None,
            seed=None,
            width=None,
            height=None,
            metadata_json={},
        )


@dataclass(frozen=True)
class OperationRecord:
    id: str
    profile_name: str
    project_id: str | None
    operation_kind: OperationKind
    status: OperationStatus
    flow_operation_id: str | None
    flow_media_id: str | None
    prompt: str | None
    prompt_hash: str | None
    prompt_redacted: bool
    model: str | None
    aspect_ratio: str | None
    seed: int | None
    metadata_json: JsonObject

    @classmethod
    def minimal(
        cls,
        *,
        id: str,
        profile_name: str,
        project_id: str | None,
        operation_kind: OperationKind,
    ) -> OperationRecord:
        return cls(
            id=id,
            profile_name=profile_name,
            project_id=project_id,
            operation_kind=operation_kind,
            status=OperationStatus.SUCCEEDED,
            flow_operation_id=None,
            flow_media_id=None,
            prompt=None,
            prompt_hash=None,
            prompt_redacted=False,
            model=None,
            aspect_ratio=None,
            seed=None,
            metadata_json={},
        )


@dataclass(frozen=True)
class LocalFileRecord:
    id: str
    asset_id: str
    path: Path
    media_kind: AssetKind
    mime_type: str | None
    bytes_size: int | None
    sha256: str | None


@dataclass(frozen=True)
class AssetLookup:
    id: str
    profile_name: str
    flow_project_id: str | None
    flow_media_id: str
    media_kind: AssetKind
    local_files: list[LocalFileRecord]
```

- [ ] **Step 4: Implement repository methods**

Create `src/gflow_cli/data/repository.py`. Use `json.dumps(value, sort_keys=True)`, UUIDs supplied by callers, and wrap `sqlite3.IntegrityError` as `DataIntegrityError`.

The repository must include these methods:

- `__init__(store: DataStore) -> None`
- `upsert_profile(name: str, profile_dir: Path) -> None`
- `upsert_project(record: ProjectRecord) -> ProjectRecord`
- `upsert_asset(record: AssetRecord) -> AssetRecord`
- `insert_operation(record: OperationRecord) -> OperationRecord`
- `link_operation_asset(operation_id: str, asset_id: str, role: OperationAssetRole, position: int) -> None`
- `upsert_local_file(record: LocalFileRecord) -> LocalFileRecord`
- `get_asset_by_flow_media_id(profile_name: str, flow_media_id: str) -> AssetLookup | None`

Use `ON CONFLICT` for `profiles`, `projects`, `assets`, and `local_files`. Use plain `INSERT` for `operations` because operation rows are event records.

- [ ] **Step 5: Run repository tests**

Run:

```powershell
uv run python -m pytest tests/data/test_repository.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/gflow_cli/data/models.py src/gflow_cli/data/repository.py tests/data/test_repository.py
git commit -m "feat(data): add repositories"
```

## Task 4: Redaction and Operation Recorder

**Files:**
- Create: `src/gflow_cli/data/redaction.py`
- Create: `src/gflow_cli/data/recorder.py`
- Create: `tests/data/test_redaction.py`
- Create: `tests/data/test_recorder.py`

- [ ] **Step 1: Write failing redaction tests**

Create `tests/data/test_redaction.py`:

```python
from gflow_cli.data.redaction import prompt_fields, redact_metadata


def test_prompt_fields_full_mode_stores_text_and_hash() -> None:
    fields = prompt_fields("hello", mode="full")
    assert fields.prompt == "hello"
    assert fields.prompt_hash is not None
    assert fields.prompt_redacted is False


def test_prompt_fields_redacted_mode_stores_hash_only() -> None:
    fields = prompt_fields("hello", mode="redacted")
    assert fields.prompt is None
    assert fields.prompt_hash is not None
    assert fields.prompt_redacted is True


def test_redact_metadata_removes_signed_urls_and_tokens() -> None:
    raw = {
        "fifeUrl": "https://flow-content.google/path?Signature=abc",
        "clientContext": {"recaptchaContext": {"token": "secret"}},
        "nested": [{"authorization": "Bearer abc"}],
        "safe": "kept",
    }
    redacted = redact_metadata(raw)
    assert redacted["fifeUrl"] == "<redacted:url>"
    assert redacted["clientContext"]["recaptchaContext"]["token"] == "<redacted:token>"
    assert redacted["nested"][0]["authorization"] == "<redacted:secret>"
    assert redacted["safe"] == "kept"
```

- [ ] **Step 2: Write failing recorder tests**

Create `tests/data/test_recorder.py`:

```python
from pathlib import Path

from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore


def test_record_upload_persists_project_asset_and_file(tmp_path: Path) -> None:
    image_path = tmp_path / "seed.png"
    image_path.write_bytes(b"png-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="full")
        project = ProjectInfo(project_id="flow-project-1", title="gflow-cli upload")
        asset = AssetInfo(
            name="media-upload-1",
            project_id="flow-project-1",
            workflow_id="workflow-upload-1",
            display_name="seed.png",
            width=640,
            height=480,
        )
        recorder.record_upload_image(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project=project,
            asset=asset,
            image_path=image_path,
        )
        found = recorder.repository.get_asset_by_flow_media_id("default", "media-upload-1")
        assert found is not None
        assert found.flow_project_id == "flow-project-1"
        assert found.local_files[0].path == image_path.resolve()


def test_record_generated_images_persists_generation_metadata(tmp_path: Path) -> None:
    saved = tmp_path / "image.png"
    saved.write_bytes(b"image-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="redacted")
        project = ProjectInfo(project_id="flow-project-1", title="gflow-cli t2i")
        image = GeneratedImage(
            media_name="media-generated-1",
            workflow_id="workflow-generated-1",
            seed=123,
            prompt="prompt text",
            model_name_type="NARWHAL",
            aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
            fife_url="https://flow-content.google/path?Signature=abc",
            dimensions=(1024, 1792),
            media_generation_id="generation-1",
        )
        req = GenerateImageRequest(
            prompt="prompt text",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
        )
        recorder.record_generated_images(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project=project,
            request=req,
            images=[image],
            saved_paths=[saved],
            input_media_ids=[],
            operation_kind="t2i",
        )
        row = store.conn.execute(
            "SELECT prompt, prompt_hash, prompt_redacted, flow_media_generation_id, "
            "metadata_json FROM assets WHERE flow_media_id='media-generated-1'"
        ).fetchone()
        assert row["prompt"] is None
        assert row["prompt_hash"]
        assert row["prompt_redacted"] == 1
        assert row["flow_media_generation_id"] == "generation-1"
        assert "Signature=abc" not in row["metadata_json"]
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```powershell
uv run python -m pytest tests/data/test_redaction.py tests/data/test_recorder.py -q
```

Expected: import failures for redaction and recorder; `GeneratedImage.media_generation_id` is also missing.

- [ ] **Step 4: Add `GeneratedImage.media_generation_id`**

Modify `src/gflow_cli/api/dto.py`:

```python
    media_generation_id: str | None = None
```

In `GeneratedImage.from_response_item`, read:

```python
                media_generation_id=generated.get("mediaGenerationId"),
```

Update tests that construct `GeneratedImage` so the optional field is accepted without further changes.

- [ ] **Step 5: Implement redaction helpers**

Create `src/gflow_cli/data/redaction.py`:

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal


PromptMode = Literal["full", "redacted"]


@dataclass(frozen=True)
class PromptFields:
    prompt: str | None
    prompt_hash: str | None
    prompt_redacted: bool


def prompt_fields(prompt: str | None, *, mode: PromptMode) -> PromptFields:
    if prompt is None:
        return PromptFields(prompt=None, prompt_hash=None, prompt_redacted=False)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if mode == "redacted":
        return PromptFields(prompt=None, prompt_hash=digest, prompt_redacted=True)
    return PromptFields(prompt=prompt, prompt_hash=digest, prompt_redacted=False)


def redact_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in {"token", "recaptchatoken"}:
                out[key] = "<redacted:token>"
            elif lowered in {"authorization", "cookie", "set-cookie"}:
                out[key] = "<redacted:secret>"
            elif "url" in lowered and isinstance(item, str):
                out[key] = "<redacted:url>"
            else:
                out[key] = redact_metadata(item)
        return out
    if isinstance(value, list):
        return [redact_metadata(item) for item in value]
    return value
```

- [ ] **Step 6: Implement `OperationRecorder`**

Create `src/gflow_cli/data/recorder.py`. It must:

- Generate local IDs with `uuid.uuid4()`.
- Use `DataStore.transaction(immediate=True)` for grouped writes.
- Call `redact_metadata(value)` before storing `metadata_json`.
- Store resolved absolute paths in `local_files.path`.
- Use prompt mode from settings.

Core constructor and factory:

```python
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.video import GenerateVideoRequest, VideoResult
from gflow_cli.config import Settings
from gflow_cli.data.models import (
    AssetKind,
    AssetRecord,
    LocalFileRecord,
    OperationAssetRole,
    OperationKind,
    OperationRecord,
    OperationStatus,
    ProjectRecord,
    SourceKind,
)
from gflow_cli.data.redaction import PromptMode, prompt_fields, redact_metadata
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore


def _new_id() -> str:
    return str(uuid.uuid4())


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


class OperationRecorder:
    def __init__(self, repository: DataRepository, *, prompt_mode: PromptMode) -> None:
        self.repository = repository
        self.prompt_mode = prompt_mode

    @classmethod
    def open(cls, settings: Settings) -> OperationRecorder:
        store = DataStore.open(settings.resolved_db_path())
        return cls(DataRepository(store), prompt_mode=settings.history_prompts)

    def close(self) -> None:
        self.repository.store.close()
```

Implement these public methods:

- `record_upload_image(profile_name, profile_dir, project, asset, image_path) -> None`
- `record_generated_images(profile_name, profile_dir, project, request, images, saved_paths, input_media_ids, operation_kind) -> None`
- `record_generated_video(profile_name, profile_dir, request, result) -> None`

For REST-created image projects, build a `ProjectRecord` with a generated local ID, the current profile name, and `flow_project_id=project.project_id`. For video projects, `VideoResult.project_id` is the Flow project ID and the display title is `"gflow-cli video"`.

- [ ] **Step 7: Run recorder tests**

Run:

```powershell
uv run python -m pytest tests/api/test_image_dto.py tests/data/test_redaction.py tests/data/test_recorder.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```powershell
git add src/gflow_cli/api/dto.py src/gflow_cli/data/redaction.py src/gflow_cli/data/recorder.py tests/data/test_redaction.py tests/data/test_recorder.py tests/api/test_image_dto.py
git commit -m "feat(data): add operation recorder"
```

## Task 5: Image Command Recording

**Files:**
- Modify: `src/gflow_cli/cli_image.py`
- Modify: `tests/cli/test_cli_image.py`
- Modify: `tests/cli/test_t2i_multi_prompt.py`

- [ ] **Step 1: Write failing CLI tests for image recording**

Add tests that monkeypatch `OperationRecorder.open` with a fake recorder:

```python
class FakeRecorder:
    def __init__(self) -> None:
        self.uploads = []
        self.generated = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def record_upload_image(self, **kwargs):
        self.uploads.append(kwargs)

    def record_generated_images(self, **kwargs):
        self.generated.append(kwargs)
```

Test cases:

- `_run_upload` passes `profile_name`, `profile_dir`, `project`, `asset`, and `image_path`.
- `_run_t2i` records generated images after downloads complete.
- `_run_i2i` records uploaded local references as input assets and generated images as outputs.
- A recorder exception after download logs `data.persistence_failed_after_success` and does not raise when saved files exist.

- [ ] **Step 2: Run the image CLI tests and verify they fail**

Run:

```powershell
uv run python -m pytest tests/cli/test_cli_image.py tests/cli/test_t2i_multi_prompt.py -q
```

Expected: failures because `_run_upload`, `_run_t2i`, and `_run_i2i` do not accept `profile_name` or record data.

- [ ] **Step 3: Add recorder helpers to `cli_image.py`**

Add imports:

```python
import structlog

from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.errors import DataStoreError
```

Add logger:

```python
logger = structlog.get_logger(__name__)
```

Add helper:

```python
def _warn_persistence_failed_after_success(
    *,
    exc: Exception,
    flow_media_id: str | None,
    local_path: Path | None,
) -> None:
    logger.warning(
        "data.persistence_failed_after_success",
        error_class=type(exc).__name__,
        flow_media_id=flow_media_id,
        local_path=str(local_path) if local_path is not None else None,
    )
    console.print(
        "[yellow]Generated media was saved, but local history was not updated.[/yellow]"
    )
```

- [ ] **Step 4: Open the recorder before remote calls**

Modify `upload`, `t2i`, and `i2i` command bodies to pass `profile_name` into async runners.

In each async runner, open the recorder before `FlowApiClient`:

```python
    settings = get_settings()
    recorder = OperationRecorder.open(settings)
    try:
        async with FlowApiClient(
            profile_dir=profile_dir,
            headless=settings.headless,
            out_dir=out_dir,
        ) as client:
            project = await client.create_project(title=title)
    finally:
        recorder.close()
```

This ordering makes `DataMigrationError` and `DataStoreError` fail before the paid Flow operation begins.

- [ ] **Step 5: Record upload and generated image outputs**

For `_run_upload`, after `client.upload_image` succeeds:

```python
        recorder.record_upload_image(
            profile_name=profile_name,
            profile_dir=profile_dir,
            project=project,
            asset=asset,
            image_path=image_path,
        )
```

For `_run_t2i`, after all downloads:

```python
        try:
            recorder.record_generated_images(
                profile_name=profile_name,
                profile_dir=profile_dir,
                project=project,
                request=req,
                images=images,
                saved_paths=saved_paths,
                input_media_ids=[],
                operation_kind="t2i",
            )
        except DataStoreError as exc:
            first_image = images[0] if images else None
            first_path = saved_paths[0] if saved_paths else None
            _warn_persistence_failed_after_success(
                exc=exc,
                flow_media_id=first_image.media_name if first_image else None,
                local_path=first_path,
            )
```

For `_run_i2i`, pass `input_media_ids=[ref.name for ref in resolved_refs]` and `operation_kind="i2i"`.

- [ ] **Step 6: Run image CLI tests**

Run:

```powershell
uv run python -m pytest tests/cli/test_cli_image.py tests/cli/test_t2i_multi_prompt.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add src/gflow_cli/cli_image.py tests/cli/test_cli_image.py tests/cli/test_t2i_multi_prompt.py
git commit -m "feat(data): record image commands"
```

## Task 6: Image Batch Recording

**Files:**
- Modify: `src/gflow_cli/image_batch.py`
- Modify: `src/gflow_cli/cli_image.py`
- Modify: `tests/image_batch/test_image_manifest.py`
- Modify: `tests/image_batch/test_observability_events.py`

- [ ] **Step 1: Write failing batch recording tests**

Add tests that run `run_image_batch` and `run_manifest_image_batch` with fake clients and fake recorders. Assert:

- `profile_name` is accepted and passed to the recorder.
- The shared Flow `project_id` from `BatchSubmissionResult.project_id` is recorded.
- Each successful row records output assets and local files.
- Partial-result salvage records already-downloaded assets before re-raising `BatchPartialError`.

- [ ] **Step 2: Run batch tests and verify they fail**

Run:

```powershell
uv run python -m pytest tests/image_batch/test_image_manifest.py tests/image_batch/test_observability_events.py -q
```

Expected: failures because image batch has no recorder dependency.

- [ ] **Step 3: Add recorder dependency to batch runners**

Modify `run_image_batch` and `run_manifest_image_batch` signatures:

```python
    profile_name: str,
    recorder: OperationRecorder | None = None,
```

Pass `profile_name` from `cli_image.t2i` multi-prompt mode and `cli_image.batch`.

- [ ] **Step 4: Record successful batch outcomes**

In `_download_results`, add optional `recorder`, `profile_name`, and `profile_dir`. After each row downloads, call:

```python
recorder.record_generated_images(
    profile_name=profile_name,
    profile_dir=profile_dir,
    project=ProjectInfo(project_id=result.project_id, title="gflow-cli image batch"),
    request=_to_request(item),
    images=list(result.images),
    saved_paths=saved,
    input_media_ids=[],
    operation_kind="t2i",
)
```

Wrap only this post-download recorder call in `except DataStoreError` and emit `data.persistence_failed_after_success` with the first output media ID and first saved path.

- [ ] **Step 5: Preserve fail-fast salvage semantics**

In the `BatchPartialError` branch, pass the recorder into `_download_results` before creating the replacement `BatchPartialError`. The recorder must not prevent already-paid images from being downloaded or returned in `partial_results`.

- [ ] **Step 6: Run batch tests**

Run:

```powershell
uv run python -m pytest tests/image_batch/test_image_manifest.py tests/image_batch/test_observability_events.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add src/gflow_cli/image_batch.py src/gflow_cli/cli_image.py tests/image_batch/test_image_manifest.py tests/image_batch/test_observability_events.py
git commit -m "feat(data): record image batches"
```

## Task 7: Normalize Video Generation Through FlowApiClient

**Files:**
- Modify: `src/gflow_cli/api/transports/_common.py`
- Modify: `src/gflow_cli/api/transports/base.py`
- Modify: `src/gflow_cli/api/transports/ui_automation.py`
- Modify: `src/gflow_cli/api/transports/ui_automation_video.py`
- Modify: `src/gflow_cli/api/video.py`
- Modify: `src/gflow_cli/api/client.py`
- Modify: `src/gflow_cli/cli_video.py`
- Modify: `tests/api/test_video.py`
- Modify: `tests/api/test_client.py`
- Modify: `tests/api/transports/test_common.py`
- Modify: `tests/api/transports/test_ui_automation_video.py`
- Modify: `tests/cli/test_cli_video.py`

- [ ] **Step 1: Write failing project-ID extraction tests**

In `tests/api/transports/test_common.py`, add:

```python
from gflow_cli.api.transports._common import extract_project_id


def test_extract_project_id_from_flow_url() -> None:
    assert (
        extract_project_id("https://labs.google/fx/tools/flow/project/abc-123?x=1")
        == "abc-123"
    )


def test_extract_project_id_returns_none_for_gallery_url() -> None:
    assert extract_project_id("https://labs.google/fx/tools/flow") is None
```

- [ ] **Step 2: Write failing video DTO parser tests**

In `tests/api/test_video.py`, add fixture-driven tests:

```python
from gflow_cli.api.video import operation_name_from_generate_response


def test_operation_name_from_generate_response_reads_operation_name() -> None:
    body = {
        "media": [{"name": "media-1"}],
        "operations": [{"operation": {"name": "media-1"}}],
    }
    assert operation_name_from_generate_response(body) == "media-1"


def test_operation_name_matches_current_media_id_in_fixture() -> None:
    body = {
        "media": [{"name": "media-1"}],
        "operations": [{"operation": {"name": "media-1"}}],
    }
    assert operation_name_from_generate_response(body) == body["media"][0]["name"]
```

- [ ] **Step 3: Write failing client-boundary tests**

In `tests/cli/test_cli_video.py`, assert `_run_t2v` constructs `FlowApiClient` and does not instantiate `UiAutomationTransport` directly.

In `tests/api/test_client.py`, add a fake transport with `generate_video` and assert:

```python
result = await client.generate_video(req=request, out_dir=tmp_path, download=True)
assert result.status.media_id == "media-1"
```

- [ ] **Step 4: Run video tests and verify they fail**

Run:

```powershell
uv run python -m pytest tests/api/test_video.py tests/api/test_client.py tests/api/transports/test_common.py tests/api/transports/test_ui_automation_video.py tests/cli/test_cli_video.py -q
```

Expected: failures for missing common extractor, missing operation parser, missing `FlowApiClient.generate_video`, and direct transport use in `cli_video.py`.

- [ ] **Step 5: Move project-ID extraction to `_common.py`**

Add to `src/gflow_cli/api/transports/_common.py`:

```python
PROJECT_URL_FRAGMENT = "/project/"


def extract_project_id(url: str) -> str | None:
    if PROJECT_URL_FRAGMENT not in url:
        return None
    try:
        return url.split(PROJECT_URL_FRAGMENT)[1].split("?")[0]
    except (IndexError, ValueError):
        return None
```

Modify `ui_automation.py` to import and use `extract_project_id`. Leave a thin private alias only if existing tests import `_extract_project_id`:

```python
def _extract_project_id(url: str) -> str | None:
    return extract_project_id(url)
```

- [ ] **Step 6: Add video result metadata**

Modify `src/gflow_cli/api/video.py`:

```python
@dataclass(frozen=True)
class VideoResult:
    status: VideoStatus
    local_path: Path | None
    project_id: str | None = None
    flow_operation_id: str | None = None
```

Add:

```python
def operation_name_from_generate_response(response_json: dict[str, Any]) -> str | None:
    operations = response_json.get("operations")
    if not isinstance(operations, list) or not operations:
        return None
    first = operations[0]
    if not isinstance(first, dict):
        return None
    operation = first.get("operation")
    if not isinstance(operation, dict):
        return None
    name = operation.get("name")
    return str(name) if name is not None else None
```

- [ ] **Step 7: Populate project and operation IDs in UI video transport**

Modify `src/gflow_cli/api/transports/ui_automation_video.py`:

- Import `extract_project_id`.
- After `_enter_editor`, set `project_id = extract_project_id(page.url)`.
- Parse `flow_operation_id = operation_name_from_generate_response(generate_resp.get("body") or {})`.
- Return a `VideoResult` with the existing status and local path plus `project_id=project_id` and `flow_operation_id=flow_operation_id`.

The generated media ID remains `status.media_id`. The operation ID is stored separately even though current captures show the same value.

- [ ] **Step 8: Add video-capable client method**

Modify `src/gflow_cli/api/client.py`:

```python
    async def generate_video(
        self,
        *,
        req: GenerateVideoRequest,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
        download: bool = True,
    ) -> VideoResult:
        if self.transport is None:
            raise RuntimeError(
                "FlowApiClient.transport is None - call generate_video inside 'async with client'"
            )
        generate_video = getattr(self.transport, "generate_video", None)
        if generate_video is None:
            raise ConfigurationError(
                f"transport {type(self.transport).__name__} does not support video generation"
            )
        try:
            return await generate_video(
                request=req,
                out_dir=out_dir,
                poll_timeout_s=poll_timeout_s,
                download=download,
            )
        except Exception as e:
            if _is_target_closed(e):
                raise BrowserSessionClosedError() from e
            raise
```

Add imports for `GenerateVideoRequest`, `VideoResult`, and `ConfigurationError`.

- [ ] **Step 9: Route `gflow video t2v` through FlowApiClient**

Modify `src/gflow_cli/cli_video.py`:

- Remove direct `UiAutomationTransport` construction.
- Resolve `settings = get_settings()` in `t2v`.
- Pass `headless=settings.headless`, `profile_name`, and `transport=None` into `_run_t2v`.
- In `_run_t2v`, use `async with FlowApiClient(profile_dir=profile_dir, headless=headless, out_dir=out_dir) as client:`.
- Call `result = await client.generate_video(req=request, out_dir=out_dir, download=True)`.

- [ ] **Step 10: Run video tests**

Run:

```powershell
uv run python -m pytest tests/api/test_video.py tests/api/test_client.py tests/api/transports/test_common.py tests/api/transports/test_ui_automation_video.py tests/cli/test_cli_video.py -q
```

Expected: all tests pass.

- [ ] **Step 11: Commit**

```powershell
git add src/gflow_cli/api src/gflow_cli/cli_video.py tests/api tests/cli/test_cli_video.py
git commit -m "refactor(video): route t2v through client"
```

## Task 8: Video Recording

**Files:**
- Modify: `src/gflow_cli/cli_video.py`
- Modify: `src/gflow_cli/data/recorder.py`
- Modify: `tests/cli/test_cli_video.py`
- Modify: `tests/data/test_recorder.py`

- [ ] **Step 1: Write failing video recorder tests**

In `tests/data/test_recorder.py`, add:

```python
from gflow_cli.api.video import Aspect as VideoAspect
from gflow_cli.api.video import GenerateVideoRequest, Mode, VideoResult, VideoStatus


def test_record_generated_video_persists_media_operation_and_file(tmp_path: Path) -> None:
    saved = tmp_path / "video.mp4"
    saved.write_bytes(b"video-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="full")
        request = GenerateVideoRequest(
            prompt="video prompt",
            mode=Mode.T2V,
            aspect=VideoAspect.PORTRAIT,
        )
        result = VideoResult(
            status=VideoStatus(
                media_id="media-video-1",
                status="MEDIA_GENERATION_STATUS_SUCCESSFUL",
            ),
            local_path=saved,
            project_id="flow-project-video-1",
            flow_operation_id="media-video-1",
        )
        recorder.record_generated_video(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            request=request,
            result=result,
        )
        asset = recorder.repository.get_asset_by_flow_media_id("default", "media-video-1")
        assert asset is not None
        assert asset.flow_project_id == "flow-project-video-1"
        assert asset.local_files[0].path == saved.resolve()
```

In `tests/cli/test_cli_video.py`, add a fake recorder and assert `_run_t2v` records the successful `VideoResult`.

- [ ] **Step 2: Run video recording tests and verify they fail**

Run:

```powershell
uv run python -m pytest tests/data/test_recorder.py tests/cli/test_cli_video.py -q
```

Expected: failures until `record_generated_video` and CLI wiring are complete.

- [ ] **Step 3: Complete `record_generated_video`**

In `src/gflow_cli/data/recorder.py`, implement `record_generated_video`:

- Upsert the profile.
- Upsert a project with `flow_project_id=result.project_id` when present.
- Upsert a video asset with `flow_media_id=result.status.media_id`, `media_kind=AssetKind.VIDEO`, `source_kind=SourceKind.GENERATED`, `status=result.status.status`, `prompt`, `model=request.tier.value`, and `aspect_ratio=request.aspect.value`.
- Insert an operation with `operation_kind=OperationKind.T2V`, `flow_operation_id=result.flow_operation_id`, and `flow_media_id=result.status.media_id`.
- Link the output asset at position 0.
- Insert the local file when `result.local_path` is not `None`.

- [ ] **Step 4: Wire recorder into `cli_video.py`**

Open `OperationRecorder` before `FlowApiClient`. After successful video generation, call:

```python
        try:
            recorder.record_generated_video(
                profile_name=profile_name,
                profile_dir=profile_dir,
                request=request,
                result=result,
            )
        except DataStoreError as exc:
            logger.warning(
                "data.persistence_failed_after_success",
                error_class=type(exc).__name__,
                flow_media_id=result.status.media_id,
                local_path=str(result.local_path) if result.local_path else None,
            )
            console.print(
                "[yellow]Generated media was saved, but local history was not updated.[/yellow]"
            )
```

Keep the existing generation-failure exit behavior unchanged.

- [ ] **Step 5: Run video recording tests**

Run:

```powershell
uv run python -m pytest tests/data/test_recorder.py tests/cli/test_cli_video.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add src/gflow_cli/cli_video.py src/gflow_cli/data/recorder.py tests/cli/test_cli_video.py tests/data/test_recorder.py
git commit -m "feat(data): record video commands"
```

## Task 9: Read-Only Data CLI and Seed Resolver

**Files:**
- Create: `src/gflow_cli/cli_data.py`
- Modify: `src/gflow_cli/cli.py`
- Modify: `src/gflow_cli/data/repository.py`
- Create: `tests/cli/test_cli_data.py`
- Modify: `tests/data/test_repository.py`

- [ ] **Step 1: Write failing seed resolver tests**

Add to `tests/data/test_repository.py`:

```python
def test_resolve_seed_image_returns_project_media_and_path(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", tmp_path / "profile_default")
        project = repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="title",
                metadata_json={},
            )
        )
        asset = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-local",
                profile_name="default",
                project_id=project.id,
                flow_media_id="media-image-1",
            )
        )
        image_path = tmp_path / "image.png"
        image_path.write_bytes(b"image-bytes")
        repo.upsert_local_file(
            LocalFileRecord(
                id="file-local",
                asset_id=asset.id,
                path=image_path,
                media_kind=AssetKind.IMAGE,
                mime_type="image/png",
                bytes_size=11,
                sha256="b" * 64,
            )
        )
        seed = repo.resolve_seed_image("default", "media-image-1")
        assert seed is not None
        assert seed.flow_project_id == "flow-project-1"
        assert seed.flow_media_id == "media-image-1"
        assert seed.local_path == image_path.resolve()
```

- [ ] **Step 2: Write failing CLI data tests**

Create `tests/cli/test_cli_data.py` with Click runner tests for:

- `gflow data media media-image-1 --profile default` prints media ID, project ID, kind, and local path.
- Missing media exits non-zero with a user-facing not-found message.

- [ ] **Step 3: Run tests and verify they fail**

Run:

```powershell
uv run python -m pytest tests/data/test_repository.py tests/cli/test_cli_data.py -q
```

Expected: missing `resolve_seed_image` and missing `cli_data`.

- [ ] **Step 4: Add seed resolver model and repository method**

Add to `models.py`:

```python
@dataclass(frozen=True)
class SeedImage:
    flow_media_id: str
    flow_project_id: str
    local_path: Path | None
```

Add to `DataRepository`:

```python
    def resolve_seed_image(self, profile_name: str, flow_media_id: str) -> SeedImage | None:
        row = self.store.conn.execute(
            "SELECT a.flow_media_id, p.flow_project_id "
            "FROM assets a LEFT JOIN projects p ON p.id = a.project_id "
            "WHERE a.profile_name=? AND a.flow_media_id=? AND a.media_kind='image'",
            (profile_name, flow_media_id),
        ).fetchone()
        if row is None or row["flow_project_id"] is None:
            return None
        file_row = self.store.conn.execute(
            "SELECT path FROM local_files lf JOIN assets a ON a.id = lf.asset_id "
            "WHERE a.profile_name=? AND a.flow_media_id=? ORDER BY lf.created_at DESC LIMIT 1",
            (profile_name, flow_media_id),
        ).fetchone()
        local_path = Path(file_row["path"]) if file_row is not None else None
        return SeedImage(
            flow_media_id=row["flow_media_id"],
            flow_project_id=row["flow_project_id"],
            local_path=local_path,
        )
```

- [ ] **Step 5: Add `gflow data` command group**

Create `src/gflow_cli/cli_data.py`:

```python
from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table

from gflow_cli._cli_helpers import _resolve_profile, run_with_handlers, safe_path_text
from gflow_cli.config import get_settings
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

console = Console()


@click.group()
def data() -> None:
    """Read local gflow media history."""


@data.command("media")
@click.argument("media_id")
@click.option("--profile", default=None, help="Profile name (overrides default).")
def media(media_id: str, profile: str | None) -> None:
    profile_name = _resolve_profile(profile)
    run_with_handlers(
        lambda: _run_media(profile_name=profile_name, media_id=media_id),
        cli_command="data media",
    )


async def _run_media(*, profile_name: str, media_id: str) -> None:
    settings = get_settings()
    with DataStore.open(settings.resolved_db_path()) as store:
        repo = DataRepository(store)
        asset = repo.get_asset_by_flow_media_id(profile_name, media_id)
        if asset is None:
            console.print(f"[red]No local media record found:[/red] {media_id}")
            sys.exit(1)
        table = Table(title="gflow data media")
        table.add_column("field")
        table.add_column("value", overflow="fold")
        table.add_row("profile", profile_name)
        table.add_row("media_id", asset.flow_media_id)
        table.add_row("project_id", asset.flow_project_id or "")
        table.add_row("kind", asset.media_kind.value)
        for idx, local_file in enumerate(asset.local_files, start=1):
            table.add_row(f"local_path_{idx}", safe_path_text(local_file.path))
        console.print(table)
```

Register it in `src/gflow_cli/cli.py`:

```python
from gflow_cli.cli_data import data as _data_group

main.add_command(_data_group)
```

- [ ] **Step 6: Run data CLI tests**

Run:

```powershell
uv run python -m pytest tests/data/test_repository.py tests/cli/test_cli_data.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add src/gflow_cli/cli.py src/gflow_cli/cli_data.py src/gflow_cli/data/repository.py src/gflow_cli/data/models.py tests/cli/test_cli_data.py tests/data/test_repository.py
git commit -m "feat(data): add read-only media lookup"
```

## Task 10: Packaging, Docs, and Verification

**Files:**
- Modify: `docs/CONFIGURATION.md`
- Modify: `docs/USAGE.md`
- Modify: `docs/SECURITY.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `PLAN.md`
- Create: `tests/data/test_packaging.py`

- [ ] **Step 1: Write packaging test**

Create `tests/data/test_packaging.py`:

```python
import subprocess
import zipfile
from pathlib import Path


def test_wheel_contains_sql_migrations(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        check=True,
    )
    wheel = next(dist_dir.glob("gflow_cli-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "gflow_cli/data/migrations/001_initial.sql" in archive.namelist()
```

- [ ] **Step 2: Run packaging test and verify it passes**

Run:

```powershell
uv run python -m pytest tests/data/test_packaging.py -q
```

Expected: wheel inspection confirms `001_initial.sql` is packaged.

- [ ] **Step 3: Update configuration docs**

In `docs/CONFIGURATION.md`, document:

- `GFLOW_CLI_DB_PATH`: optional SQLite DB path override.
- Default DB location: `<GFLOW_CLI_HOME>/gflow.db`.
- `GFLOW_CLI_HISTORY_PROMPTS=full|redacted`.

- [ ] **Step 4: Update usage docs**

In `docs/USAGE.md`, document:

- `gflow data media <media_id>`.
- Exit code `16` for data store and migration failures.
- Newer-schema recovery: upgrade `gflow-cli` or set `GFLOW_CLI_DB_PATH` to a compatible database.

- [ ] **Step 5: Update security docs**

In `docs/SECURITY.md`, document:

- The DB stores profile names, Flow project/media IDs, local file paths, prompts by default, and prompt hashes.
- `GFLOW_CLI_HISTORY_PROMPTS=redacted` stores prompt hashes without prompt text.
- Signed CDN URLs, reCAPTCHA tokens, authorization headers, and cookies must not be stored in `metadata_json`.

- [ ] **Step 6: Update architecture and roadmap docs**

In `docs/ARCHITECTURE.md`, add `gflow_cli.data` to the modular monolith description.

In `PLAN.md`, replace the broad Phase 6 SQLite operations-history item with the concrete data-layer phase. Keep `gflow data import` and richer management UI as separate backlog entries.

- [ ] **Step 7: Run focused test suite**

Run:

```powershell
uv run python -m pytest tests/data tests/cli/test_cli_data.py tests/cli/test_cli_image.py tests/cli/test_cli_video.py tests/api/test_client.py tests/api/test_video.py tests/api/transports/test_common.py tests/api/transports/test_ui_automation_video.py -q
```

Expected: all focused tests pass.

- [ ] **Step 8: Run lint and type gates**

Run:

```powershell
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
```

Expected: all gates pass.

- [ ] **Step 9: Run repository hygiene**

Run:

```powershell
uv run python scripts/ci/check_repo_hygiene.py
```

Expected: hygiene check passes.

- [ ] **Step 10: Commit**

```powershell
git add docs/CONFIGURATION.md docs/USAGE.md docs/SECURITY.md docs/ARCHITECTURE.md PLAN.md tests/data/test_packaging.py
git commit -m "docs(data): document local provenance layer"
```

## Self-Review Checklist

- [ ] Migration runner applies SQL through `importlib.resources` and checks SHA-256 drift.
- [ ] Every SQLite connection enables `foreign_keys`, `WAL`, and `busy_timeout`.
- [ ] Recorder grouped writes use `BEGIN IMMEDIATE`.
- [ ] `OperationRecorder` owns metadata redaction.
- [ ] Prompt storage honors `GFLOW_CLI_HISTORY_PROMPTS`.
- [ ] Post-success persistence failure logs `data.persistence_failed_after_success` with `flow_media_id` and local path when known.
- [ ] `gflow video t2v` no longer instantiates `UiAutomationTransport` directly.
- [ ] Video result stores `flow_operation_id` separately from `flow_media_id`.
- [ ] Seed-image read path returns Flow project ID, Flow media ID, and local path.
- [ ] SQL migrations are present in built wheels.
- [ ] Docs include newer-schema recovery and prompt privacy behavior.

## Final Verification

Run the full local routine before merging:

```powershell
$env:PYTHONUTF8=1
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q --cov=gflow_cli
```

Expected: all commands pass. If local memory is constrained, run `uv run python -m pytest -m "not live and not e2e" -q` and let CI run the full suite.
