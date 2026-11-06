"""Detached, best-effort sender for completed weekly usage totals."""

from __future__ import annotations

import json
import secrets
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from contextlib import closing
from datetime import UTC, datetime, timedelta
from urllib.request import HTTPRedirectHandler

from gflow_cli import telemetry
from gflow_cli.config import _resolve_home  # pyright: ignore[reportPrivateUsage]

_ENDPOINT = "https://gflow-telemetry.ffroliva.workers.dev/v1/report"
_WEEK_SECONDS = 7 * 24 * 60 * 60
_DAY = timedelta(days=1)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_: object, **__: object) -> None:
        return None


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
    weeks = [row[0] for row in con.execute("SELECT DISTINCT week FROM counters ORDER BY week")]
    for week in weeks:
        start = _week_start(week)
        end = start + _DAY * 7
        if start <= notice_at or end > now:
            continue
        row = con.execute("SELECT due_at, sent_at FROM reports WHERE week = ?", (week,)).fetchone()
        due_at = datetime.fromisoformat(row[0]) if row and row[0] else None
        if row is None or row[1] is not None or due_at is None or now < due_at:
            continue
        retry = meta.get(f"retry_after:{week}")
        if retry and now < datetime.fromisoformat(retry):
            continue
        return str(week)
    return None


def _initialize_due_dates(now: datetime) -> None:
    home = _resolve_home()
    if not (home / "telemetry.db").exists():
        return
    with closing(telemetry._connect(home)) as con:  # pyright: ignore[reportPrivateUsage]
        meta = dict(con.execute("SELECT key, value FROM meta"))
        notice = meta.get("notice_seen_at")
        if not notice:
            return
        notice_at = datetime.fromisoformat(notice)
        if notice_at.tzinfo is None:
            notice_at = notice_at.replace(tzinfo=UTC)
        weeks = [row[0] for row in con.execute("SELECT DISTINCT week FROM counters ORDER BY week")]
    for week in weeks:
        start = _week_start(week)
        end = start + _DAY * 7
        if start <= notice_at or end > now:
            continue
        if telemetry.stored_report_id(week) is None:
            telemetry.report_id_for(week)
        with closing(telemetry._connect(home)) as con, con:  # pyright: ignore[reportPrivateUsage]
            row = con.execute("SELECT due_at FROM reports WHERE week = ?", (week,)).fetchone()
            if row is not None and row[0] is None:
                due_at = end + timedelta(seconds=secrets.randbelow(_WEEK_SECONDS + 1))
                con.execute(
                    "UPDATE reports SET due_at = ? WHERE week = ? AND due_at IS NULL",
                    (due_at.isoformat(), week),
                )


def next_due_week(now: datetime) -> str | None:
    """Return the oldest completed, noticed and due week with counters."""
    try:
        home = _resolve_home()
        if not (home / "telemetry.db").exists():
            return None
        _initialize_due_dates(now)
        with closing(telemetry._connect(home)) as con, con:  # pyright: ignore[reportPrivateUsage]
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
        if next_due_week(now) is None:
            return
        with closing(telemetry._connect(home)) as con, con:  # pyright: ignore[reportPrivateUsage]
            con.execute("BEGIN IMMEDIATE")
            meta = dict(con.execute("SELECT key, value FROM meta"))
            last = meta.get("last_attempt")
            if last:
                last_at = datetime.fromisoformat(last)
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
        if sys.platform == "win32":
            subprocess.Popen(  # noqa: S603 - fixed module command with the current interpreter
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                cwd=str(home),
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    | subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                ),
            )
        else:
            subprocess.Popen(  # noqa: S603 - fixed module command with the current interpreter
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                cwd=str(home),
                start_new_session=True,
            )
    except Exception:
        return


def send(week: str, *, endpoint: str = _ENDPOINT) -> None:
    """POST exactly the allow-listed report and apply the endpoint's reply."""
    try:
        body = telemetry.build_report(week)
        # The URL is fixed in production; endpoint injection is only for loopback tests.
        request = urllib.request.Request(  # noqa: S310
            endpoint,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
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
