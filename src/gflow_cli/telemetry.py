"""Local weekly usage counters and the kill switches that gate them (#1164).

Best-effort by contract: ``record`` never raises, prints or noticeably slows a
command. Counters hold only command/flag names, exit codes and coarse duration
buckets, in ``<home>/telemetry.db``. The home comes from ``_resolve_home()``,
never ``get_settings()``, so a broken configuration cannot silence or break this.
No connection is cached: a long-lived MCP server would otherwise pin the WAL.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Literal

import structlog
from dotenv import dotenv_values

from gflow_cli.config import _env_files, _resolve_home  # pyright: ignore[reportPrivateUsage]

log = structlog.get_logger(__name__)


def _debug(event: str, **kw: str) -> None:
    # Unconfigured structlog prints to STDOUT (PrintLogger); observability.configure_logging
    # routes to stderr. Log only once configured, so stdout (MCP stdio, --json) stays clean.
    if structlog.is_configured():
        log.debug(event, **kw)


_RETAIN_WEEKS = 53
_TRANSITION_WINDOW_S = 30 * 60


@dataclass(frozen=True)
class Counter:
    """One weekly counter row. ``kind`` is command, flag, exit, duration,
    transition, client, crash or tool.
    """

    week: str
    surface: str
    kind: str
    key: str
    n: int


def iso_week(now: datetime | None = None) -> str:
    year, week, _ = (now or datetime.now(UTC)).isocalendar()
    return f"{year}-W{week:02d}"


def _env_sources() -> list[dict[str, str | None]]:
    sources: list[dict[str, str | None]] = [dict(os.environ)]
    try:  # unreadable or non-UTF-8 .env (also inside _resolve_home): cannot switch anything
        for path in _env_files():
            sources.append(dict(dotenv_values(path)))
    except (OSError, ValueError):
        pass
    return sources


def _lookup(name: str) -> list[str]:
    """Every value of ``name`` across env and dotenv files (name matched case-insensitively)."""
    return [
        v for src in _env_sources() for k, v in src.items() if k.upper() == name and v is not None
    ]


@cache
def killed() -> str | None:
    """The kill switch in force, if any. Any source can only turn telemetry OFF."""
    if any(
        v.strip().lower() not in ("1", "true", "on", "") for v in _lookup("GFLOW_CLI_TELEMETRY")
    ):
        return "GFLOW_CLI_TELEMETRY"
    if any(v.strip().lower() not in ("", "0", "false") for v in _lookup("DO_NOT_TRACK")):
        return "DO_NOT_TRACK"
    return None


def opted_out() -> bool:
    """Not cached: a running MCP server must notice ``gflow telemetry off``.

    Fails closed: when the state cannot be read we cannot tell, so nothing is counted.
    """
    try:
        return (_resolve_home() / "telemetry.off").exists()
    except Exception:  # noqa: BLE001
        return True


def _bucket(seconds: float) -> str:
    if seconds < 10:
        return "<10s"
    if seconds < 60:
        return "<1m"
    if seconds < 300:
        return "<5m"
    return "5m+"


def _connect(home: Path) -> sqlite3.Connection:
    con = sqlite3.connect(home / "telemetry.db", timeout=0.2)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute(
            "CREATE TABLE IF NOT EXISTS counters (week TEXT, surface TEXT, kind TEXT, key TEXT,"
            " n INTEGER, PRIMARY KEY (week, surface, kind, key))"
        )
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    except BaseException:
        con.close()  # a leaked handle pins the file on Windows
        raise
    return con


def record(
    surface: Literal["cli", "mcp"],
    command: str,
    flags: Iterable[str],
    outcome: str,
    duration_s: float,
) -> None:
    """Count one command invocation. Silent on every failure (lossy under contention)."""
    try:
        if killed() or opted_out():
            return
        home = _resolve_home()
        home.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        week = iso_week(now)
        keys = [("command", command)]
        keys += [("flag", f"{command} {f}") for f in flags]
        keys += [("exit", f"{command} {outcome}"), ("duration", f"{command} {_bucket(duration_s)}")]
        with closing(_connect(home)) as con, con:
            meta = dict(con.execute("SELECT key, value FROM meta"))
            prev = meta.get("prev_command")
            if prev and now.timestamp() - float(meta.get("prev_time", 0)) <= _TRANSITION_WINDOW_S:
                keys.append(("transition", f"{prev} -> {command}"))
            con.executemany(
                "INSERT INTO counters VALUES (?, ?, ?, ?, 1)"
                " ON CONFLICT(week, surface, kind, key) DO UPDATE SET n = n + 1",
                [(week, surface, kind, key) for kind, key in keys],
            )
            con.executemany(
                "INSERT OR REPLACE INTO meta VALUES (?, ?)",
                [("prev_command", command), ("prev_time", str(time.time()))],
            )
            con.execute(
                "INSERT OR IGNORE INTO meta VALUES ('first_seen', ?)", (now.date().isoformat(),)
            )
            cutoff = iso_week(now - timedelta(weeks=_RETAIN_WEEKS))
            con.execute("DELETE FROM counters WHERE week < ?", (cutoff,))
    except Exception as exc:  # noqa: BLE001 - telemetry must never affect a command
        _debug("telemetry_record_failed", error=type(exc).__name__)


def read_counters(weeks: int | None = None) -> list[Counter]:
    """All stored counters (or those of the last ``weeks`` ISO weeks); [] on any failure."""
    try:
        home = _resolve_home()
        if not (home / "telemetry.db").exists():
            return []
        sql = "SELECT week, surface, kind, key, n FROM counters"
        args: tuple[str, ...] = ()
        if weeks is not None:
            sql += " WHERE week > ?"
            args = (iso_week(datetime.now(UTC) - timedelta(weeks=weeks)),)
        with closing(_connect(home)) as con:
            return [Counter(*row) for row in con.execute(sql + " ORDER BY week, kind, key", args)]
    except Exception as exc:  # noqa: BLE001
        _debug("telemetry_read_failed", error=type(exc).__name__)
        return []
