"""Detached, best-effort sender for completed weekly usage totals."""

from __future__ import annotations

import secrets
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

from gflow_cli import __version__, telemetry
from gflow_cli.config import _resolve_home  # pyright: ignore[reportPrivateUsage]

_ENDPOINT = "https://gflow-telemetry.ffroliva.workers.dev/v1/report"
_WEEK_SECONDS = 7 * 24 * 60 * 60
_DAY = timedelta(days=1)


def _week_start(week: str) -> datetime:
    year, number = week.split("-W", 1)
    return datetime.fromisocalendar(int(year), int(number), 1).replace(tzinfo=UTC)


def _next_due_week(now: datetime, con: sqlite3.Connection) -> str | None:
    meta = dict(con.execute("SELECT key, value FROM meta"))
    notice = meta.get("notice_seen_at")
    if not notice:
        return None
    notice_at = datetime.fromisoformat(notice)
    if notice_at.tzinfo is None:
        notice_at = notice_at.replace(tzinfo=UTC)
    rows = con.execute(
        "SELECT DISTINCT counters.week, reports.report_id, reports.due_at, reports.sent_at "
        "FROM counters LEFT JOIN reports ON reports.week = counters.week "
        "ORDER BY counters.week"
    ).fetchall()
    eligible: list[tuple[str, str | None, str | None]] = []
    inserts: list[tuple[str, str, str]] = []
    updates: list[tuple[str, str]] = []
    for raw_week, report_id, raw_due_at, sent_at in rows:
        week = str(raw_week)
        start = _week_start(week)
        end = start + _DAY * 7
        if start <= notice_at or end > now:
            continue
        if raw_due_at is None:
            due_at = (end + timedelta(seconds=secrets.randbelow(_WEEK_SECONDS + 1))).isoformat()
            if report_id is None:
                inserts.append((week, secrets.token_urlsafe(16), due_at))
            else:
                updates.append((due_at, week))
        else:
            due_at = str(raw_due_at)
        eligible.append((week, str(sent_at) if sent_at is not None else None, due_at))
    con.executemany(
        "INSERT OR IGNORE INTO reports (week, report_id, due_at) VALUES (?, ?, ?)", inserts
    )
    con.executemany("UPDATE reports SET due_at = ? WHERE week = ? AND due_at IS NULL", updates)
    for week, sent_at, raw_due_at in eligible:
        due_at = datetime.fromisoformat(raw_due_at) if raw_due_at else None
        if sent_at is not None or due_at is None or now < due_at:
            continue
        retry = meta.get(f"retry_after:{week}")
        if retry and now < datetime.fromisoformat(retry):
            continue
        return str(week)
    return None


def next_due_week(now: datetime) -> str | None:
    """Return the oldest completed, noticed and due week with counters."""
    try:
        home = _resolve_home()
        if not (home / "telemetry.db").exists():
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


def maybe_upload() -> None:
    """Cheap, silent gate that starts one detached sender when a week is due."""
    try:
        if not telemetry.uploads_enabled() or telemetry._NOTICE_SHOWN_THIS_RUN:  # pyright: ignore[reportPrivateUsage]
            return
        home = _resolve_home()
        if not (home / "telemetry.db").exists():
            return
        now = datetime.now(UTC)
        with closing(telemetry._connect(home)) as con:  # pyright: ignore[reportPrivateUsage]
            row = con.execute("SELECT value FROM meta WHERE key = 'last_attempt'").fetchone()
            if row:
                last_at = datetime.fromisoformat(str(row[0]))
                if last_at.tzinfo is None:
                    last_at = last_at.replace(tzinfo=UTC)
                if last_at <= now and now - last_at < _DAY:
                    return
            with con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT value FROM meta WHERE key = 'last_attempt'").fetchone()
                if row:
                    last_at = datetime.fromisoformat(str(row[0]))
                    if last_at.tzinfo is None:
                        last_at = last_at.replace(tzinfo=UTC)
                    if last_at <= now and now - last_at < _DAY:
                        return
                week = _next_due_week(now, con)
                if week is None:
                    return
                con.execute(
                    "INSERT OR REPLACE INTO meta VALUES ('last_attempt', ?)", (now.isoformat(),)
                )

        command = [sys.executable, "-m", "gflow_cli.telemetry_upload"]
        kwargs: dict[str, Any] = (
            {
                "creationflags": (
                    subprocess.CREATE_NO_WINDOW
                    | subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            }
            if sys.platform == "win32"
            else {"start_new_session": True}
        )
        subprocess.Popen(  # noqa: S603 - fixed module command with the current interpreter
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            cwd=str(home),
            **kwargs,
        )
    except Exception:
        return


def send(week: str, *, endpoint: str = _ENDPOINT) -> None:
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
        # The URL is fixed in production; endpoint injection is only for loopback tests.
        request = urllib.request.Request(  # noqa: S310
            endpoint,
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
    except Exception:
        return


def _main() -> None:
    endpoint = _ENDPOINT
    # The argv hook exists only for the loopback subprocess test; production spawn passes no args.
    if len(sys.argv) == 3 and sys.argv[1] == "--test-endpoint":
        endpoint = sys.argv[2]
    try:
        if not telemetry.uploads_enabled():
            return
        week = next_due_week(datetime.now(UTC))
        if week is not None:
            send(week, endpoint=endpoint)
    except Exception:
        return


if __name__ == "__main__":
    _main()
