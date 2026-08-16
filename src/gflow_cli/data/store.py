from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING, Self

from gflow_cli.errors import DataMigrationError, DataStoreError

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from importlib.resources.abc import Traversable
    from pathlib import Path

MIGRATION_PACKAGE = "gflow_cli.data.migrations"
BUSY_TIMEOUT_MS = 5000
MIGRATION_RE = re.compile(r"^(?P<version>\d{4,})_.+\.sql$")
_ROUTE_MIGRATE = "data.migrate"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_sql_for_checksum(sql: str) -> str:
    normalized = sql.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).rstrip()


def _checksum_sql(sql: str) -> str:
    return hashlib.sha256(_normalize_sql_for_checksum(sql).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Migration:
    version: str
    version_number: int
    filename: str
    sql: str
    checksum: str


def _load_migrations() -> list[Migration]:
    files: list[Traversable] = []
    for item in resources.files(MIGRATION_PACKAGE).iterdir():
        if item.name.endswith(".sql"):
            files.append(item)
    migrations: list[Migration] = []
    for file_ref in sorted(files, key=lambda p: p.name):
        match = MIGRATION_RE.match(file_ref.name)
        if match is None:
            raise DataMigrationError(
                detail=f"invalid migration filename {file_ref.name!r}",
                route=_ROUTE_MIGRATE,
            )
        version = match.group("version")
        sql = file_ref.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                version_number=int(version),
                filename=file_ref.name,
                sql=sql,
                checksum=_checksum_sql(sql),
            ),
        )
    return migrations


def _sql_update_quote_state(
    char: str,
    nxt: str,
    in_single: bool,
    in_double: bool,
) -> tuple[bool, bool, int]:
    """Update single/double-quote state for one character.

    Returns (in_single, in_double, extra_advance) where extra_advance is 1
    when an escaped single-quote (``''``) was consumed so the caller skips
    the second ``'``.
    """
    if in_single and char == "'" and nxt == "'":
        # Escaped single-quote inside a string literal — consume both chars.
        return in_single, in_double, 1
    if char == "'" and not in_double:
        return not in_single, in_double, 0
    if char == '"' and not in_single:
        return in_single, not in_double, 0
    return in_single, in_double, 0


@dataclass
class _SqlTokenizerState:
    in_single: bool = False
    in_double: bool = False
    in_line_comment: bool = False


def _sql_advance_one_char(
    state: _SqlTokenizerState,
    buf: list[str],
    char: str,
    nxt: str,
) -> tuple[int, str | None]:
    """Process one char of SQL; return ``(chars_consumed, yielded_statement)``.

    ``yielded_statement`` is non-None only when the char terminated a statement
    (a `;` outside any string/comment). Pure function over the mutable ``state``
    + ``buf``; the caller owns the index advancement.
    """
    if state.in_line_comment:
        if char == "\n":
            state.in_line_comment = False
        buf.append(char)
        return 1, None
    if not state.in_single and not state.in_double and char == "-" and nxt == "-":
        state.in_line_comment = True
        buf.extend([char, nxt])
        return 2, None
    state.in_single, state.in_double, extra = _sql_update_quote_state(
        char,
        nxt,
        state.in_single,
        state.in_double,
    )
    if extra:
        buf.extend([char, nxt])
        return 2, None
    if char == ";" and not state.in_single and not state.in_double:
        statement = "".join(buf).strip()
        buf.clear()
        return 1, statement or None
    buf.append(char)
    return 1, None


def _iter_sql_statements(sql: str) -> Iterator[str]:
    buf: list[str] = []
    state = _SqlTokenizerState()
    i = 0
    while i < len(sql):
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        consumed, statement = _sql_advance_one_char(state, buf, sql[i], nxt)
        if statement is not None:
            yield statement
        i += consumed
    tail = "".join(buf).strip()
    if tail:
        yield tail


@dataclass(frozen=True)
class SchemaInspection:
    """Read-only schema report — what doctor's db.migration_drift consumes."""

    user_version: int
    applied_migrations: tuple[str, ...]
    expected_migrations: tuple[str, ...]
    drift: bool
    newer_than_binary: bool


def inspect_schema(path: Path) -> SchemaInspection:
    """Compare the DB's migration state against the packaged migrations.

    Never migrates — reads ``schema_migrations`` + ``PRAGMA user_version``
    over :meth:`DataStore.open_readonly` and diffs against the same packaged
    SQL files ``apply_migrations`` uses. ``drift`` is True whenever applied
    versions/checksums differ from the packaged set or ``user_version``
    disagrees with the packaged latest; ``newer_than_binary`` flags a DB
    written by a newer release. Raises :class:`DataStoreError` when the DB
    cannot be opened read-only.
    """
    migrations = _load_migrations()
    expected = {m.version: m.checksum for m in migrations}
    with DataStore.open_readonly(path) as store:
        user_version = int(store.conn.execute("PRAGMA user_version").fetchone()[0])
        table_exists = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'",
        ).fetchone()
        applied = (
            {
                str(row["version"]): str(row["checksum"])
                for row in store.conn.execute(
                    "SELECT version, checksum FROM schema_migrations",
                ).fetchall()
            }
            if table_exists is not None
            else {}
        )
    latest_known = migrations[-1].version_number
    max_applied = max((int(version) for version in applied), default=0)
    return SchemaInspection(
        user_version=user_version,
        applied_migrations=tuple(sorted(applied, key=int)),
        expected_migrations=tuple(m.version for m in migrations),
        drift=applied != expected or user_version != latest_known,
        newer_than_binary=max_applied > latest_known,
    )


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
        except (sqlite3.Error, OSError) as exc:
            raise DataStoreError(detail=str(exc), route="data.open") from exc

    @classmethod
    def open_readonly(cls, path: Path) -> DataStore:
        """Open an existing database strictly read-only — for diagnostics.

        Mirrors :meth:`open` conventions (classmethod returning a ``DataStore``
        with ``sqlite3.Row`` rows, usable as a context manager) so callers can
        share query helpers, but it can NEVER write: ``mode=ro`` at the VFS
        layer plus ``PRAGMA query_only = ON`` on the connection. It never
        applies migrations and never creates the file or parent directories.

        sqlite validates the header/WAL lazily, so a probe read runs here:
        any failure (missing file, not-a-database, unreadable WAL state)
        raises the typed :class:`DataStoreError` at open time, which callers
        (doctor) can degrade to a finding instead of crashing.
        """
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = ON")
                conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
                conn.execute("PRAGMA user_version").fetchone()  # probe
            except sqlite3.Error:
                conn.close()
                raise
            return cls(conn=conn, path=path)
        except (sqlite3.Error, OSError) as exc:
            raise DataStoreError(detail=str(exc), route="data.open_readonly") from exc

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Generator[None, None, None]:
        try:
            self.conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield
            self.conn.execute("COMMIT")
        except Exception:
            try:
                self.conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

    def apply_migrations(self) -> None:
        migrations = _load_migrations()
        if not migrations:
            raise DataMigrationError(detail="no SQL migrations packaged", route=_ROUTE_MIGRATE)

        table_exists = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'",
        ).fetchone()
        latest_known = migrations[-1].version_number
        if table_exists is not None:
            rows = self.conn.execute("SELECT version FROM schema_migrations").fetchall()
            current = max((int(str(row["version"])) for row in rows), default=0)
            if current > latest_known:
                raise DataMigrationError(
                    detail=(
                        f"database schema {current} is newer than installed schema {latest_known}"
                    ),
                    route=_ROUTE_MIGRATE,
                )

        applied = (
            {
                str(row["version"]): str(row["checksum"])
                for row in self.conn.execute(
                    "SELECT version, checksum FROM schema_migrations",
                ).fetchall()
            }
            if table_exists is not None
            else {}
        )

        for migration in migrations:
            existing = applied.get(migration.version)
            if existing is not None:
                if existing != migration.checksum:
                    raise DataMigrationError(
                        detail=f"migration {migration.version} checksum drift",
                        route=_ROUTE_MIGRATE,
                    )
                continue
            with self.transaction(immediate=True):
                for statement in _iter_sql_statements(migration.sql):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migrations(version, filename, checksum, applied_at) "
                    "VALUES (?, ?, ?, ?)",
                    (migration.version, migration.filename, migration.checksum, _utc_now()),
                )
                self.conn.execute(f"PRAGMA user_version = {migration.version_number}")
