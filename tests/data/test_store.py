"""Read-only store access + schema inspection (#542, Task D2).

``DataStore.open_readonly`` must never write, never migrate, and surface
every open-time failure as the typed ``DataStoreError`` so doctor can degrade
to a finding instead of crashing. ``inspect_schema`` reports drift/newer-DB
without ever calling ``apply_migrations``.
"""

from __future__ import annotations

import gc
import os
import sqlite3
import stat
import subprocess
import sys
import threading
import warnings
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from gflow_cli import winsec
from gflow_cli.data import store as store_module
from gflow_cli.data.store import DataStore, Migration, _secure_store_files, inspect_schema
from gflow_cli.errors import DataMigrationError, DataStoreError


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
    sibling = tmp_path / "folder-wal"
    sibling.touch()
    sibling.chmod(0o644)
    with pytest.raises(DataStoreError):
        DataStore.open(folder)
    assert stat.S_IMODE(folder.stat().st_mode) == 0o2775
    assert stat.S_IMODE(sibling.stat().st_mode) == 0o644


def test_open_on_a_blank_path_is_a_data_store_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
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


def test_another_thread_waits_until_the_new_file_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On POSIX, closing any descriptor of a file drops the locks of the whole process."""
    db = tmp_path / "gflow.db"
    close = os.close
    others: list[threading.Thread] = []
    waited: list[bool] = []

    def slow_close(fd: int) -> None:
        if not others:
            others.append(threading.Thread(target=_secure_store_files, args=(db,)))
            others[0].start()
            others[0].join(1)
            waited.append(others[0].is_alive())
        close(fd)

    monkeypatch.setattr(os, "close", slow_close)
    _secure_store_files(db)
    others[0].join()
    assert waited == [True]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def test_a_recreated_db_is_restricted_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = subprocess.run
    hardened: list[str] = []

    def run(args: list[str], **kwargs: Any) -> Any:
        if "/inheritance:r" in args:
            hardened.append(os.path.normcase(args[1]))
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    db = tmp_path / "gflow.db"
    for _ in range(2):
        with DataStore.open(db):
            pass
        with DataStore.open(db):
            pass
        db.unlink()
    assert hardened.count(os.path.normcase(str(db))) == 2
    assert hardened.count(os.path.normcase(f"{db}-wal")) == 4
    assert hardened.count(os.path.normcase(f"{db}-shm")) == 4


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def _win_icacls_exe() -> str:
    return os.path.join(os.environ["SystemRoot"], "System32", "icacls.exe")


def _win_acl(path: Path) -> list[str]:
    done = subprocess.run([_win_icacls_exe(), str(path)], check=True, capture_output=True)  # noqa: S603
    text = done.stdout.decode("ascii", errors="replace").replace(str(path), "")
    return [line.strip() for line in text.splitlines() if ":(" in line]


def _win_current_user() -> str:
    whoami = os.path.join(os.environ["SystemRoot"], "System32", "whoami.exe")
    me = subprocess.run([whoami], check=True, capture_output=True).stdout  # noqa: S603
    return me.decode("ascii", errors="replace").strip().lower()


def _grant_everyone_read(path: Path) -> None:
    subprocess.run(  # noqa: S603
        [_win_icacls_exe(), str(path), "/grant", "*S-1-1-0:(OI)(CI)R"],
        check=True,
        capture_output=True,
    )


def _assert_single_user_acl(path: Path, user: str) -> None:
    entries = _win_acl(path)
    assert len(entries) == 1
    assert entries[0].lower() == f"{user}:(f)"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def test_open_restricts_the_db_but_not_its_neighbours(tmp_path: Path) -> None:
    _grant_everyone_read(tmp_path)
    neighbour = tmp_path / "sub" / "report.txt"
    neighbour.parent.mkdir()
    neighbour.touch()
    before = [_win_acl(tmp_path), _win_acl(neighbour)]
    db = tmp_path / "gflow.db"
    db.touch()  # an existing database
    with DataStore.open(db):
        pass
    assert [_win_acl(tmp_path), _win_acl(neighbour)] == before
    user = _win_current_user()
    _assert_single_user_acl(db, user)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gflow.db", "sub"]
    with DataStore.open(tmp_path / "new" / "gflow.db"):
        pass
    (tmp_path / "plain").mkdir()
    assert _win_acl(tmp_path / "new") == _win_acl(tmp_path / "plain")
    with pytest.raises(DataStoreError):
        DataStore.open(tmp_path / "plain")
    assert _win_acl(tmp_path / "plain") == _win_acl(tmp_path / "new")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def test_open_restricts_windows_wal_and_shm_on_open_and_reopen(tmp_path: Path) -> None:
    """Live -wal and -shm sidecars are owner-only on fresh open and across clean reopens (#1437)."""
    _grant_everyone_read(tmp_path)
    neighbour = tmp_path / "sub" / "report.txt"
    neighbour.parent.mkdir()
    neighbour.touch()
    before = [_win_acl(tmp_path), _win_acl(neighbour)]
    user = _win_current_user()
    db = tmp_path / "gflow.db"
    wal = db.with_name("gflow.db-wal")
    shm = db.with_name("gflow.db-shm")

    with DataStore.open(db) as live:
        assert wal.exists() and shm.exists()
        _assert_single_user_acl(db, user)
        _assert_single_user_acl(wal, user)
        _assert_single_user_acl(shm, user)
        live.conn.execute(
            "INSERT INTO profiles(name, first_seen_at) VALUES ('p1', '2026-01-01T00:00:00Z')"
        )
        live.conn.commit()
        live.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        _assert_single_user_acl(wal, user)
        _assert_single_user_acl(shm, user)

    assert not wal.exists() and not shm.exists()

    with DataStore.open(db) as reopened:
        assert wal.exists() and shm.exists()
        _assert_single_user_acl(wal, user)
        _assert_single_user_acl(shm, user)
        rows = reopened.conn.execute("SELECT name FROM profiles").fetchall()
        assert [r["name"] for r in rows] == ["p1"]

    assert [_win_acl(tmp_path), _win_acl(neighbour)] == before


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def test_open_tightens_existing_loose_windows_wal_and_shm(tmp_path: Path) -> None:
    """Crash-leftover -wal/-shm files with Everyone read access are tightened (#1437)."""
    _grant_everyone_read(tmp_path)
    user = _win_current_user()
    db = tmp_path / "gflow.db"
    wal = db.with_name("gflow.db-wal")
    shm = db.with_name("gflow.db-shm")

    with DataStore.open(db):
        pass
    wal.touch()
    shm.touch()
    assert any("everyone" in entry.lower() for entry in _win_acl(wal))
    assert any("everyone" in entry.lower() for entry in _win_acl(shm))

    with DataStore.open(db) as reopened:
        _assert_single_user_acl(db, user)
        _assert_single_user_acl(wal, user)
        _assert_single_user_acl(shm, user)
        reopened.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def test_open_skips_same_identity_and_rehardens_externally_replaced_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated open hits ACL cache; an externally replaced DB gets a fresh ACL."""
    db = tmp_path / "gflow.db"
    real_run = subprocess.run
    hardened: list[str] = []

    def tracking_run(args: list[str], **kwargs: Any) -> Any:
        if "/inheritance:r" in args:
            hardened.append(args[1])
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", tracking_run)

    with DataStore.open(db):
        count_after_first = len(hardened)
        assert count_after_first == 3
        with DataStore.open(db):
            assert len(hardened) == count_after_first

    hardened.clear()
    db.unlink()
    db.write_bytes(b"")  # External process creates a new file without calling forget_file_acl.
    with DataStore.open(db):
        pass
    assert any(os.path.normcase(p) == os.path.normcase(str(db)) for p in hardened)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def test_open_never_hardens_windows_non_db_or_symlink_targets(tmp_path: Path) -> None:
    """Directory paths, sibling -wal files, and symlinked sidecars keep their original ACLs."""
    _grant_everyone_read(tmp_path)
    folder = tmp_path / "folder"
    folder.mkdir()
    sibling_wal = tmp_path / "folder-wal"
    sibling_wal.touch()
    before_folder = _win_acl(folder)
    before_sibling = _win_acl(sibling_wal)

    with pytest.raises(DataStoreError):
        DataStore.open(folder)
    assert _win_acl(folder) == before_folder
    assert _win_acl(sibling_wal) == before_sibling

    other = tmp_path / "other.txt"
    other.touch()
    before_other = _win_acl(other)
    sym_shm = tmp_path / "gflow.db-shm"
    try:
        sym_shm.symlink_to(other)
    except OSError as exc:
        pytest.skip(f"Windows symlink creation unavailable: {exc}")

    with suppress(DataStoreError), DataStore.open(tmp_path / "gflow.db"):
        pass
    assert _win_acl(other) == before_other

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    target = real_dir / "target.db"
    link = tmp_path / "linked.db"
    link.symlink_to(target)
    user = _win_current_user()
    with DataStore.open(link):
        _assert_single_user_acl(target, user)
        _assert_single_user_acl(tmp_path / "linked.db-wal", user)
        _assert_single_user_acl(tmp_path / "linked.db-shm", user)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def test_open_hardens_sidecars_even_when_db_acl_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An icacls failure on gflow.db (including cached failure) never skips -wal/-shm hardening."""
    _grant_everyone_read(tmp_path)
    user = _win_current_user()
    db = tmp_path / "gflow.db"
    wal = db.with_name("gflow.db-wal")
    shm = db.with_name("gflow.db-shm")
    real_run = subprocess.run

    def fail_db_only(args: list[str], **kwargs: Any) -> Any:
        if "/inheritance:r" in args and os.path.normcase(args[1]) == os.path.normcase(str(db)):
            raise subprocess.CalledProcessError(5, args, stderr=b"Access is denied.")
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_db_only)

    with capture_logs() as logs1, DataStore.open(db):
        assert any("everyone" in entry.lower() for entry in _win_acl(db))
        _assert_single_user_acl(wal, user)
        _assert_single_user_acl(shm, user)
    assert [entry["event"] for entry in logs1] == ["file_acl_failed"]

    # Second open: db hits the cached failure in stage 1; stage 2 still secures new sidecars.
    with capture_logs() as logs2, DataStore.open(db):
        _assert_single_user_acl(wal, user)
        _assert_single_user_acl(shm, user)
    assert logs2 == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACLs")
def test_open_survives_a_failed_acl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> str:
        raise OSError

    monkeypatch.setattr(winsec, "_current_user_sid", refuse)
    with capture_logs() as logs, DataStore.open(tmp_path / "gflow.db"):
        pass
    assert [entry["event"] for entry in logs] == ["file_acl_failed"]


def test_harden_files_once_does_not_retry_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fail() -> str:
        calls.append(1)
        raise OSError

    once = tmp_path / "once.db"
    once.touch()
    winsec.forget_file_acl(str(once))

    monkeypatch.setattr(sys, "platform", "linux")
    assert winsec.harden_files_once((str(once),)).ok is False
    assert calls == []

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(winsec, "_current_user_sid", fail)
    results = [winsec.harden_files_once((str(once),)) for _ in range(3)]
    assert [r.ok for r in results] == [False, False, False]
    assert results[0].sid_failed is True
    assert calls == [1]

    winsec.forget_file_acl(str(once))
    assert winsec.harden_files_once((str(once),)).ok is False
    assert calls == [1, 1]


def _clear_tracebacks(exc: BaseException) -> None:
    exc.__traceback__ = None
    if exc.__cause__ is not None:
        exc.__cause__.__traceback__ = None
    if exc.__context__ is not None:
        exc.__context__.__traceback__ = None


def _assert_closed_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    db: Path,
    call: Callable[[], object],
    expected: type[BaseException],
) -> BaseException:
    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def track_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", ResourceWarning)
        try:
            with monkeypatch.context() as patched:
                patched.setattr(sqlite3, "connect", track_connect)
                with pytest.raises(expected) as exc_info:
                    call()
            assert len(opened) == 1
            conn_repr = repr(opened[0])
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                opened[0].execute("SELECT 1")
            if db.is_file():
                moved = db.with_name(f"{db.name}.moved")
                db.rename(moved)
                moved.replace(db)
            caught = exc_info.value
            del exc_info
            _clear_tracebacks(caught)
            opened.clear()
            gc.collect()
            leaks = [
                str(w.message)
                for w in recorded
                if issubclass(w.category, ResourceWarning) and conn_repr in str(w.message)
            ]
            assert leaks == []
            return caught
        finally:
            opened.clear()
            recorded.clear()
            gc.collect()


def test_open_closes_connection_when_pragma_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"not a sqlite database" * 4)
    exc = _assert_closed_on_failure(monkeypatch, db, lambda: DataStore.open(db), DataStoreError)
    assert isinstance(exc, DataStoreError)
    assert exc.route == "data.open"
    assert isinstance(exc.__cause__, sqlite3.DatabaseError)
    db.unlink()
    with DataStore.open(db):
        pass


def test_open_closes_connection_when_migration_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    newer_db = tmp_path / "newer.db"
    conn = sqlite3.connect(newer_db)
    try:
        conn.execute(
            "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, filename TEXT, "
            "checksum TEXT, applied_at TEXT)"
        )
        conn.execute(
            "INSERT INTO schema_migrations(version, filename, checksum, applied_at) "
            "VALUES ('9999', '9999_future.sql', 'abc', '2026-05-24T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()
    _assert_closed_on_failure(
        monkeypatch, newer_db, lambda: DataStore.open(newer_db), DataMigrationError
    )
    newer_db.unlink()
    with DataStore.open(newer_db):
        pass

    drift_db = tmp_path / "drift.db"
    with DataStore.open(drift_db):
        pass
    drift_conn = sqlite3.connect(drift_db)
    try:
        drift_conn.execute("UPDATE schema_migrations SET checksum='bad' WHERE version='0001'")
        drift_conn.commit()
    finally:
        drift_conn.close()
    _assert_closed_on_failure(
        monkeypatch, drift_db, lambda: DataStore.open(drift_db), DataMigrationError
    )
    drift_db.unlink()
    with DataStore.open(drift_db):
        pass


def test_open_closes_connection_when_migration_sql_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "bad_migration.db"
    base = store_module._load_migrations()
    bad = Migration(
        version="9999",
        version_number=9999,
        filename="9999_bad.sql",
        sql="CREATE TABLE leak_probe(x); INSERT INTO no_such_table VALUES (1);",
        checksum="0" * 64,
    )
    monkeypatch.setattr(store_module, "_load_migrations", lambda: [*base, bad])
    exc = _assert_closed_on_failure(monkeypatch, db, lambda: DataStore.open(db), DataStoreError)
    assert isinstance(exc, DataStoreError)
    assert exc.route == "data.transaction"
    probe = sqlite3.connect(db)
    try:
        row = probe.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='leak_probe'"
        ).fetchone()
    finally:
        probe.close()
    assert row is None


def test_open_closes_connection_on_post_connect_oserror_and_base_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_oserror() -> list[Migration]:
        raise OSError("io fault")

    def raise_interrupt() -> list[Migration]:
        raise KeyboardInterrupt

    db_os = tmp_path / "oserror.db"
    with monkeypatch.context() as patched:
        patched.setattr(store_module, "_load_migrations", raise_oserror)
        exc = _assert_closed_on_failure(
            monkeypatch, db_os, lambda: DataStore.open(db_os), DataStoreError
        )
    assert isinstance(exc, DataStoreError)
    assert exc.route == "data.open"
    assert isinstance(exc.__cause__, OSError)

    db_int = tmp_path / "interrupt.db"
    with monkeypatch.context() as patched:
        patched.setattr(store_module, "_load_migrations", raise_interrupt)
        _assert_closed_on_failure(
            monkeypatch, db_int, lambda: DataStore.open(db_int), KeyboardInterrupt
        )

    ro_db = _new_db(tmp_path)

    def fail_init(self: DataStore, conn: sqlite3.Connection, path: Path) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patched:
        patched.setattr(DataStore, "__init__", fail_init)
        _assert_closed_on_failure(
            monkeypatch, ro_db, lambda: DataStore.open_readonly(ro_db), KeyboardInterrupt
        )
