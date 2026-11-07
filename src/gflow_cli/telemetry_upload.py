"""Detached, best-effort sender for completed weekly usage totals."""

from __future__ import annotations

import os
import secrets
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gflow_cli import __version__, telemetry
from gflow_cli.config import _resolve_home  # pyright: ignore[reportPrivateUsage]

_ENDPOINT = "https://gflow-telemetry.ffroliva.workers.dev/v1/report"
_WEEK_SECONDS = 7 * 24 * 60 * 60
_DAY = timedelta(days=1)


def _week_start(week: str) -> datetime:
    year, number = week.split("-W", 1)
    return datetime.fromisocalendar(int(year), int(number), 1).replace(tzinfo=UTC)


def _notice_cutoff(meta: dict[str, str]) -> datetime | None:
    notice = meta.get("notice_seen_at")
    if not notice:
        return None
    notice_at = datetime.fromisoformat(notice)
    consent_value = _resolve_home() / telemetry.CONSENT_FILE
    if telemetry.consent_region():
        try:
            consent_at = datetime.fromisoformat(consent_value.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        if consent_at.tzinfo is None:
            consent_at = consent_at.replace(tzinfo=UTC)
        notice_at = max(notice_at, consent_at)
    if notice_at.tzinfo is None:
        notice_at = notice_at.replace(tzinfo=UTC)
    return notice_at


def _eligible_week(
    row: tuple[object, ...],
    notice_at: datetime,
    now: datetime,
    inserts: list[tuple[str, str, str]],
    updates: list[tuple[str, str]],
) -> tuple[str, str | None, str] | None:
    raw_week, report_id, raw_due_at, sent_at = row
    week = str(raw_week)
    start = _week_start(week)
    end = start + _DAY * 7
    if start <= notice_at or end > now:
        return None
    if raw_due_at is None:
        due_at = (end + timedelta(seconds=secrets.randbelow(_WEEK_SECONDS + 1))).isoformat()
        if report_id is None:
            inserts.append((week, secrets.token_urlsafe(16), due_at))
        else:
            updates.append((due_at, week))
    else:
        due_at = str(raw_due_at)
    return week, str(sent_at) if sent_at is not None else None, due_at


def _prepare_due_weeks(now: datetime, con: sqlite3.Connection) -> list[tuple[str, str | None, str]]:
    notice_at = _notice_cutoff(dict(con.execute("SELECT key, value FROM meta")))
    if notice_at is None:
        return []
    rows = con.execute(
        "SELECT DISTINCT counters.week, reports.report_id, reports.due_at, reports.sent_at "
        "FROM counters LEFT JOIN reports ON reports.week = counters.week "
        "ORDER BY counters.week"
    ).fetchall()
    eligible: list[tuple[str, str | None, str]] = []
    inserts: list[tuple[str, str, str]] = []
    updates: list[tuple[str, str]] = []
    for row in rows:
        if week_row := _eligible_week(row, notice_at, now, inserts, updates):
            eligible.append(week_row)
    con.executemany(
        "INSERT OR IGNORE INTO reports (week, report_id, due_at) VALUES (?, ?, ?)", inserts
    )
    con.executemany("UPDATE reports SET due_at = ? WHERE week = ? AND due_at IS NULL", updates)
    return eligible


def _next_due_week(now: datetime, con: sqlite3.Connection) -> str | None:
    meta = dict(con.execute("SELECT key, value FROM meta"))
    for week, sent_at, due_at_value in _prepare_due_weeks(now, con):
        due_at = datetime.fromisoformat(due_at_value)
        retry = meta.get(f"retry_after:{week}")
        if (
            sent_at is None
            and now >= due_at
            and (retry is None or now >= datetime.fromisoformat(retry))
        ):
            return week
    return None


def next_due_week(now: datetime) -> str | None:
    """Return the oldest completed, noticed and due week with counters."""
    try:
        home = _resolve_home()
        if not (home / telemetry.DB_FILE).exists():
            return None
        with closing(telemetry._connect(home)) as con, con:  # pyright: ignore[reportPrivateUsage]
            con.execute("BEGIN IMMEDIATE")
            return _next_due_week(now, con)
    except Exception:
        return None


def _save_meta(key: str, value: str) -> None:
    home = _resolve_home()
    with closing(telemetry._connect(home)) as con, con:  # pyright: ignore[reportPrivateUsage]
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))


def _claim_due_week(home: Path, now: datetime) -> str | None:
    with closing(telemetry._connect(home)) as con:  # pyright: ignore[reportPrivateUsage]
        row = con.execute("SELECT value FROM meta WHERE key = 'last_attempt'").fetchone()
        if row and _attempt_is_recent(row[0], now):
            return None
        with con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT value FROM meta WHERE key = 'last_attempt'").fetchone()
            if row and _attempt_is_recent(row[0], now):
                return None
            week = _next_due_week(now, con)
            if week is None:
                return None
            con.execute(
                "INSERT OR REPLACE INTO meta VALUES ('last_attempt', ?)", (now.isoformat(),)
            )
            return week


def _attempt_is_recent(value: object, now: datetime) -> bool:
    last_at = datetime.fromisoformat(str(value))
    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=UTC)
    return last_at <= now and now - last_at < _DAY


def _spawn_sender(home: Path) -> None:
    command = [sys.executable, "-P", "-m", "gflow_cli.telemetry_upload"]
    env = os.environ.copy()
    env["GFLOW_CLI_HOME"] = str(home.resolve())
    kwargs: dict[str, Any] = (
        {"creationflags": (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    subprocess.Popen(  # noqa: S603 - fixed module command with the current interpreter
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        cwd=os.getcwd(),
        env=env,
        **kwargs,
    )


def maybe_upload() -> None:
    """Cheap, silent gate that starts one detached sender when a week is due."""
    try:
        if not telemetry.uploads_enabled():
            return
        home = _resolve_home()
        if not (home / telemetry.DB_FILE).exists():
            return
        if _claim_due_week(home, datetime.now(UTC)):
            _spawn_sender(home)
    except Exception:
        return


def send(week: str) -> None:
    """POST exactly the allow-listed report and apply the endpoint's reply."""
    try:
        import json
        import urllib.error
        import urllib.request
        from urllib.request import HTTPRedirectHandler

        class _NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *_: object, **__: object) -> None:
                return None

        report_id = telemetry.stored_report_id(week)
        if report_id is None:
            return
        body = telemetry.build_report(week)
        body["report_id"] = report_id
        request = urllib.request.Request(  # noqa: S310
            _ENDPOINT,
            data=json.dumps(body).encode(),
            # Cloudflare's edge rejects the default "Python-urllib" agent with 403 (error 1010).
            # The version is already in the report, so naming it here adds nothing new.
            headers={"Content-Type": "application/json", "User-Agent": f"gflow-cli/{__version__}"},
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=5) as response:
                status = response.status
                retry_after = response.headers.get("Retry-After")
        except urllib.error.HTTPError as response:
            status = response.code
            retry_after = response.headers.get("Retry-After")
            response.close()

        now = datetime.now(UTC)
        if status in (204, 400, 413):
            home = _resolve_home()
            with closing(telemetry._connect(home)) as con, con:  # pyright: ignore[reportPrivateUsage]
                con.execute(
                    "UPDATE reports SET sent_at = ? WHERE week = ?", (now.isoformat(), week)
                )
                con.execute("DELETE FROM meta WHERE key = ?", (f"retry_after:{week}",))
        elif status == 410:
            _save_meta("upload_disabled", "1")
        elif status == 429:
            try:
                delay = max(0, int(retry_after or "3600"))
            except ValueError:
                delay = 3600
            retry = now + timedelta(seconds=delay + secrets.randbelow(6 * 60 * 60 + 1))
            _save_meta(f"retry_after:{week}", retry.isoformat())
        else:
            _failed_attempt(week, now)
    except Exception:
        try:
            _failed_attempt(week, datetime.now(UTC))
        except Exception:
            pass
        return


def _failed_attempt(week: str, now: datetime) -> None:
    home = _resolve_home()
    with closing(telemetry._connect(home)) as con, con:  # pyright: ignore[reportPrivateUsage]
        key = f"failed_attempts:{week}"
        row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        attempts = int(row[0]) + 1 if row else 1
        if attempts >= 7:
            con.execute(
                "UPDATE reports SET sent_at = ? WHERE week = ?",
                (f"dropped:{now.isoformat()}", week),
            )
        else:
            con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(attempts)))
            retry = now + timedelta(days=1, seconds=secrets.randbelow(6 * 60 * 60 + 1))
            con.execute(
                "INSERT OR REPLACE INTO meta VALUES (?, ?)",
                (f"retry_after:{week}", retry.isoformat()),
            )


def _main() -> None:
    try:
        if not telemetry.uploads_enabled():
            return
        week = next_due_week(datetime.now(UTC))
        if week is not None:
            send(week)
    except Exception:
        return


if __name__ == "__main__":
    _main()
