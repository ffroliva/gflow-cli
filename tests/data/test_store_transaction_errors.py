"""`DataStore.transaction()` raises typed errors (#900).

Every catalog write runs inside it, and every success-recording site catches
``DataStoreError`` to warn instead of failing a paid-for generation. A raw sqlite error
missed all of them. Real file, real second connection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError


@pytest.fixture
def store(tmp_path: Path) -> Iterator[DataStore]:
    s = DataStore.open(tmp_path / "c.db")
    s.conn.execute("PRAGMA busy_timeout = 100")
    yield s
    s.close()


def test_a_write_blocked_past_busy_timeout_is_a_data_store_error(store: DataStore) -> None:
    blocker = sqlite3.connect(store.path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(DataStoreError) as info, store.transaction(immediate=True):
            store.conn.execute("DELETE FROM operations")
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert isinstance(info.value.__cause__, sqlite3.OperationalError)
    assert "locked" in str(info.value)


def test_a_nested_transaction_is_a_data_store_error(store: DataStore) -> None:
    with pytest.raises(DataStoreError), store.transaction(), store.transaction():
        pass


def test_an_integrity_error_still_reaches_the_caller_raw(store: DataStore) -> None:
    # Callers translate it into DataIntegrityError with their own route.
    with pytest.raises(sqlite3.IntegrityError), store.transaction():
        store.conn.execute("INSERT INTO operations(id) VALUES (NULL)")


def test_a_non_sqlite_error_passes_through_and_rolls_back(store: DataStore) -> None:
    store.conn.execute("CREATE TABLE t(x)")
    with pytest.raises(KeyError), store.transaction():
        store.conn.execute("INSERT INTO t VALUES (1)")
        raise KeyError("boom")
    assert store.conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
