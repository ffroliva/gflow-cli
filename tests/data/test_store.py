"""Read-only store access + schema inspection (#542, Task D2).

``DataStore.open_readonly`` must never write, never migrate, and surface
every open-time failure as the typed ``DataStoreError`` so doctor can degrade
to a finding instead of crashing. ``inspect_schema`` reports drift/newer-DB
without ever calling ``apply_migrations``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from gflow_cli.data.store import DataStore, inspect_schema
from gflow_cli.errors import DataStoreError


def _new_db(tmp_path: Path) -> Path:
    """Fresh fully-migrated DB via the normal open path."""
    db = tmp_path / "store.db"
    with DataStore.open(db):
        pass
    return db


def _forget_last_migration(db: Path) -> str:
    """Make the schema stale: drop the newest row + lower user_version."""
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY CAST(version AS INTEGER) DESC LIMIT 1"
        ).fetchone()
        conn.execute("DELETE FROM schema_migrations WHERE version = ?", (row[0],))
        conn.execute(f"PRAGMA user_version = {int(row[0]) - 1}")
        conn.commit()
        return str(row[0])
    finally:
        conn.close()


def _applied_versions(db: Path) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        return {str(r[0]) for r in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DataStore.open_readonly
# ---------------------------------------------------------------------------


def test_open_readonly_missing_file_raises_datastore_error(tmp_path: Path) -> None:
    with pytest.raises(DataStoreError):
        DataStore.open_readonly(tmp_path / "absent.db")


def test_open_readonly_not_a_database_raises_datastore_error(tmp_path: Path) -> None:
    """sqlite validates the header lazily — the typed error must fire at open."""
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database ..........")
    with pytest.raises(DataStoreError):
        DataStore.open_readonly(bad)


def test_open_readonly_stale_wal_sidecars_fail_typed_not_crash(tmp_path: Path) -> None:
    """Stale WAL sidecars never leak a raw sqlite3.Error or mutate the files.

    sqlite tolerates some garbage sidecars (a zeroed WAL header reads as an
    empty WAL); when it does NOT, the failure must be the typed DataStoreError.
    Either way the main DB and the WAL sidecar stay byte-identical.
    """
    db = _new_db(tmp_path)
    wal = db.with_name(db.name + "-wal")
    wal.write_bytes(b"\x00" * 128)
    db_before, wal_before = db.read_bytes(), wal.read_bytes()
    try:
        with DataStore.open_readonly(db) as store:
            store.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
    except DataStoreError:
        pass  # typed degradation is the contract
    assert db.read_bytes() == db_before
    assert wal.read_bytes() == wal_before


def test_open_readonly_cannot_write(tmp_path: Path) -> None:
    db = _new_db(tmp_path)
    with DataStore.open_readonly(db) as store, pytest.raises(sqlite3.OperationalError):
        store.conn.execute(
            "INSERT INTO profiles(name, first_seen_at) VALUES ('x', '2026-01-01T00:00:00.000Z')"
        )


def test_open_readonly_does_not_migrate_stale_schema(tmp_path: Path) -> None:
    db = _new_db(tmp_path)
    forgotten = _forget_last_migration(db)
    with DataStore.open_readonly(db) as store:
        rows = {
            str(r["version"])
            for r in store.conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
    assert forgotten not in rows  # still stale while open ...
    assert forgotten not in _applied_versions(db)  # ... and after close


# ---------------------------------------------------------------------------
# inspect_schema
# ---------------------------------------------------------------------------


def test_inspect_schema_clean_db_reports_no_drift(tmp_path: Path) -> None:
    info = inspect_schema(_new_db(tmp_path))
    assert info.drift is False
    assert info.newer_than_binary is False
    assert info.applied_migrations == info.expected_migrations
    assert info.user_version == int(info.expected_migrations[-1])


def test_inspect_schema_stale_db_reports_drift(tmp_path: Path) -> None:
    db = _new_db(tmp_path)
    forgotten = _forget_last_migration(db)
    info = inspect_schema(db)
    assert info.drift is True
    assert info.newer_than_binary is False
    assert forgotten not in info.applied_migrations
    assert forgotten in info.expected_migrations


def test_inspect_schema_newer_db_reports_newer_than_binary(tmp_path: Path) -> None:
    db = _new_db(tmp_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO schema_migrations(version, filename, checksum, applied_at)"
            " VALUES ('9999', '9999_future.sql', 'deadbeef', '2026-01-01T00:00:00.000Z')"
        )
        conn.execute("PRAGMA user_version = 9999")
        conn.commit()
    finally:
        conn.close()
    info = inspect_schema(db)
    assert info.newer_than_binary is True
    assert info.drift is True
    assert info.user_version == 9999


def test_inspect_schema_never_migrates(tmp_path: Path) -> None:
    db = _new_db(tmp_path)
    forgotten = _forget_last_migration(db)
    inspect_schema(db)
    assert forgotten not in _applied_versions(db)


def test_inspect_schema_missing_db_raises_datastore_error(tmp_path: Path) -> None:
    with pytest.raises(DataStoreError):
        inspect_schema(tmp_path / "absent.db")
