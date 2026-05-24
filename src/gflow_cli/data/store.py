from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from gflow_cli.errors import DataMigrationError, DataStoreError

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
            )
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


def _iter_sql_statements(sql: str) -> Iterator[str]:
    buf: list[str] = []
    in_single = False
    in_double = False
    in_line_comment = False
    i = 0
    while i < len(sql):
        char = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            buf.append(char)
            i += 1
            continue
        if not in_single and not in_double and char == "-" and nxt == "-":
            in_line_comment = True
            buf.extend([char, nxt])
            i += 2
            continue
        in_single, in_double, extra = _sql_update_quote_state(char, nxt, in_single, in_double)
        if extra:
            buf.extend([char, nxt])
            i += 2
            continue
        if char == ";" and not in_single and not in_double:
            statement = "".join(buf).strip()
            if statement:
                yield statement
            buf.clear()
        else:
            buf.append(char)
        i += 1
    statement = "".join(buf).strip()
    if statement:
        yield statement


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

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> DataStore:
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
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
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
                    "SELECT version, checksum FROM schema_migrations"
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
