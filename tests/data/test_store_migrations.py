import sqlite3
from pathlib import Path

import pytest

from gflow_cli.data.store import (
    DataStore,
    _checksum_sql,
    _iter_sql_statements,
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
        assert [row["version"] for row in rows] == [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
            "0006",
            "0007",
            "0008",
            "0009",
            "0010",
        ]
        assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert store.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_newer_schema_raises(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, filename TEXT, "
            "checksum TEXT, applied_at TEXT)"
        )
        conn.execute(
            "INSERT INTO schema_migrations(version, filename, checksum, applied_at) "
            "VALUES ('9999', '9999_future.sql', 'abc', '2026-05-24T00:00:00Z')"
        )
    with pytest.raises(DataMigrationError, match="newer"):
        DataStore.open(db)


def test_checksum_drift_raises(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    with DataStore.open(db):
        pass
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE schema_migrations SET checksum='bad' WHERE version='0001'")
    with pytest.raises(DataMigrationError, match="checksum"):
        DataStore.open(db)


def test_foreign_keys_reject_orphan_rows(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute(
                "INSERT INTO local_files(id, profile_name, asset_id, path, media_type, created_at) "
                "VALUES ('file-1', 'default', 'missing', 'C:/missing.png', "
                "'image/png', '2026-05-24T00:00:00Z')"
            )


def test_transaction_uses_begin_immediate_for_writes(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        with store.transaction(immediate=True):
            store.conn.execute(
                "INSERT INTO profiles(name, profile_dir, first_seen_at, last_used_at) "
                "VALUES ('default', 'C:/profiles/default', '2026-05-24T00:00:00Z', "
                "'2026-05-24T00:00:00Z')"
            )
        row = store.conn.execute("SELECT name FROM profiles").fetchone()
        assert row["name"] == "default"


def test_migration_statement_batch_rolls_back_on_failure(tmp_path: Path) -> None:
    from gflow_cli.errors import DataStoreError

    with DataStore.open(tmp_path / "gflow.db") as store:
        # Typed since #900; what this pins is the rollback below.
        with pytest.raises(DataStoreError):
            with store.transaction(immediate=True):
                for statement in _iter_sql_statements(
                    "CREATE TABLE rollback_probe(id INTEGER); "
                    "INSERT INTO missing_table(id) VALUES (1);"
                ):
                    store.conn.execute(statement)
        row = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='rollback_probe'"
        ).fetchone()
        assert row is None


def test_iter_sql_statements_preserves_semicolon_inside_string_literal() -> None:
    statements = list(
        _iter_sql_statements("CREATE TABLE x(v TEXT); INSERT INTO x(v) VALUES ('a;b');")
    )
    assert statements == ["CREATE TABLE x(v TEXT)", "INSERT INTO x(v) VALUES ('a;b')"]


def test_open_wraps_oserror_into_datastoreerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.errors import DataStoreError

    def boom(*args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "mkdir", boom)
    with pytest.raises(DataStoreError):
        DataStore.open(tmp_path / "gflow.db")


def test_0010_keeps_every_queue_column_and_allows_the_abort_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 0010 rebuild (#1198) copies a claimed, checkpointed row whole; 0009 copied 9 of 12."""
    from gflow_cli.data import store as store_module
    from gflow_cli.worker.queue import QueueRepository

    db = tmp_path / "gflow.db"
    every = store_module._load_migrations()
    # A narrow patch: monkeypatch.undo() would also revert the autouse env isolation.
    with monkeypatch.context() as patched:
        patched.setattr(
            store_module, "_load_migrations", lambda: [m for m in every if m.version < "0010"]
        )
        old = DataStore.open(db)
    with old:
        old.conn.execute(
            "INSERT INTO profiles (name, first_seen_at) VALUES ('default', CURRENT_TIMESTAMP)"
        )
        repo = QueueRepository(old)
        repo.enqueue_task("r", "default", "t2v", {"k": 1})
        repo.claim_task("r", "worker-1")
        repo.update_checkpoint(
            "r", claimant="worker-1", phase="remote_started", may_have_spent=True
        )
        repo.update_task_status("r", "processing", error={"title": "x"})
        before = repo.get_task("r")
        with pytest.raises(sqlite3.IntegrityError):
            old.conn.execute("UPDATE generation_queue SET status = 'abandoned'")

    with DataStore.open(db) as new:
        repo = QueueRepository(new)
        assert repo.get_task("r") == before
        repo.update_task_status("r", "abandoned")
        assert getattr(repo.get_task("r"), "status", None) == "abandoned"
