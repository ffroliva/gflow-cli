"""Read-only store access + schema inspection (#542, Task D2).

``DataStore.open_readonly`` must never write, never migrate, and surface
every open-time failure as the typed ``DataStoreError`` so doctor can degrade
to a finding instead of crashing. ``inspect_schema`` reports drift/newer-DB
without ever calling ``apply_migrations``.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from gflow_cli import winsec
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_open_creates_db_and_dir_owner_only(tmp_path: Path) -> None:
    """gflow.db and its parent directory are owner-only (issue #1437)."""
    db = tmp_path / "data" / "gflow.db"
    old_umask = os.umask(0o022)
    try:
        with DataStore.open(db):
            pass
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(db.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
@pytest.mark.parametrize("loose", [0o644, 0o640])
@pytest.mark.parametrize("dir_mode", [0o755, 0o2770, 0o1777])
def test_open_tightens_existing_files_but_not_the_directory(
    tmp_path: Path, dir_mode: int, loose: int
) -> None:
    """GFLOW_CLI_DB_PATH may name a directory gflow does not own (issue #1437)."""
    parent = tmp_path / "data"
    parent.mkdir()
    db = parent / "gflow.db"
    with DataStore.open(db) as live:
        live.conn.execute("CREATE TABLE keep (x)")
        files = [db, parent / "gflow.db-wal", parent / "gflow.db-shm"]
        for file in files:
            file.chmod(loose)
        parent.chmod(dir_mode)
        with DataStore.open(db) as reopened:
            assert [stat.S_IMODE(f.stat().st_mode) for f in files] == [0o600] * 3
            reopened.conn.execute("SELECT * FROM keep").fetchall()
    assert stat.S_IMODE(parent.stat().st_mode) == dir_mode


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_files_are_owner_only_before_sqlite_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = sqlite3.connect
    seen: list[int] = []

    def connect(path: Path, **kwargs: Any) -> sqlite3.Connection:
        seen.append(stat.S_IMODE(path.stat().st_mode))
        return real(path, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    db = tmp_path / "gflow.db"
    with DataStore.open(db):
        pass
    db.chmod(0o644)
    with DataStore.open(db):
        pass
    assert seen == [0o600, 0o600]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_open_survives_a_failed_chmod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    db.touch()
    db.chmod(0o644)

    def refuse(target: str, mode: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "chmod", refuse)
    with capture_logs() as logs, DataStore.open(db):
        pass
    assert [entry["event"] for entry in logs] == ["data_store_chmod_failed"]

    fresh = tmp_path / "fresh.db"
    with DataStore.open(fresh):
        assert stat.S_IMODE(fresh.stat().st_mode) == 0o600  # created so, not chmodded


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_open_secures_the_target_of_a_symlinked_db(tmp_path: Path) -> None:
    target = tmp_path / "real" / "target.db"
    target.parent.mkdir()
    link = tmp_path / "gflow.db"
    link.symlink_to(target)
    files = [target, target.with_name("target.db-wal"), target.with_name("target.db-shm")]
    old_umask = os.umask(0o022)
    try:
        with DataStore.open(link):
            assert [stat.S_IMODE(f.stat().st_mode) for f in files] == [0o600] * 3
            for file in files:
                file.chmod(0o644)
            with DataStore.open(link):
                assert [stat.S_IMODE(f.stat().st_mode) for f in files] == [0o600] * 3
    finally:
        os.umask(old_umask)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_open_never_changes_something_that_is_not_the_database(tmp_path: Path) -> None:
    other = tmp_path / "other.txt"
    other.touch()
    other.chmod(0o644)
    (tmp_path / "gflow.db-shm").symlink_to(other)
    with suppress(DataStoreError), DataStore.open(tmp_path / "gflow.db"):
        pass
    assert stat.S_IMODE(other.stat().st_mode) == 0o644

    folder = tmp_path / "folder"
    folder.mkdir()
    folder.chmod(0o2775)
    with pytest.raises(DataStoreError):
        DataStore.open(folder)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o2775


def test_open_on_a_blank_path_is_a_data_store_error() -> None:
    with pytest.raises(DataStoreError):
        DataStore.open(Path())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_a_second_open_keeps_the_first_connections_locks(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    outside = (
        "import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); "
        "c.execute('SELECT * FROM keep').fetchall(); c.close()"
    )
    with DataStore.open(db) as first:
        first.conn.execute("CREATE TABLE keep (x)")
        with DataStore.open(db):
            subprocess.run([sys.executable, "-c", outside, str(db)], check=True)  # noqa: S603
            # The outside close deletes the WAL only when it sees no lock of ours.
            assert (tmp_path / "gflow.db-wal").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def test_a_recreated_db_is_restricted_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = subprocess.run
    hardened: list[str] = []

    def run(args: list[str], **kwargs: Any) -> Any:
        if "/inheritance:r" in args:
            hardened.append(args[1])
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    db = tmp_path / "gflow.db"
    for _ in range(2):
        with DataStore.open(db):
            pass
        with DataStore.open(db):
            pass
        db.unlink()
    assert len(hardened) == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def test_open_restricts_the_db_but_not_its_neighbours(tmp_path: Path) -> None:
    icacls = os.path.join(os.environ["SystemRoot"], "System32", "icacls.exe")

    def acl(path: Path) -> list[str]:
        done = subprocess.run([icacls, str(path)], check=True, capture_output=True)  # noqa: S603
        text = done.stdout.decode("ascii", errors="replace").replace(str(path), "")
        return [line.strip() for line in text.splitlines() if ":(" in line]

    subprocess.run(  # noqa: S603
        [icacls, str(tmp_path), "/grant", "*S-1-1-0:(OI)(CI)R"], check=True, capture_output=True
    )
    neighbour = tmp_path / "sub" / "report.txt"
    neighbour.parent.mkdir()
    neighbour.touch()
    before = [acl(tmp_path), acl(neighbour)]
    db = tmp_path / "gflow.db"
    with DataStore.open(db):
        pass
    assert [acl(tmp_path), acl(neighbour)] == before
    assert len(acl(db)) == 1
    whoami = os.path.join(os.environ["SystemRoot"], "System32", "whoami.exe")
    me = subprocess.run([whoami], check=True, capture_output=True).stdout  # noqa: S603
    user = me.decode("ascii", errors="replace").strip().lower()
    assert acl(db)[0].lower() == f"{user}:(f)"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gflow.db", "sub"]


def test_harden_file_once_does_not_retry_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fail() -> str:
        calls.append(1)
        raise OSError

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(winsec, "_current_user_sid", fail)
    for _ in range(3):
        assert winsec.harden_file_once(str(tmp_path / "once.db")) is False
    assert calls == [1]
