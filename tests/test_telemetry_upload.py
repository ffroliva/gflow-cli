"""Weekly uploader behavior, using loopback HTTP only."""

from __future__ import annotations

import inspect
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from gflow_cli import telemetry, update_check


def test_sender_has_no_caller_controlled_endpoint() -> None:
    from gflow_cli import telemetry_upload

    assert tuple(inspect.signature(telemetry_upload.send).parameters) == ("week",)


@pytest.fixture(autouse=True)
def _home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
    monkeypatch.delenv("GFLOW_CLI_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("TZ", "America/New_York")
    monkeypatch.setattr(update_check, "_in_ci", lambda: False)
    monkeypatch.setattr(telemetry, "consent_region", lambda: False)
    telemetry.killed.cache_clear()
    return home


def _week_start(weeks_ago: int = 2) -> datetime:
    now = datetime.now(UTC)
    monday = datetime.fromisocalendar(now.isocalendar().year, now.isocalendar().week, 1)
    return monday.replace(tzinfo=UTC) - timedelta(weeks=weeks_ago)


def _week_id(start: datetime) -> str:
    iso = start.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _seed_week(
    home: Path,
    week: str,
    *,
    start: datetime | None = None,
    due_at: datetime | None = None,
    sent_at: datetime | None = None,
    notice_at: datetime | None = None,
) -> None:
    home.mkdir(parents=True, exist_ok=True)
    week_start = start or datetime.strptime(f"{week}-1", "%G-W%V-%u").replace(tzinfo=UTC)
    with closing(telemetry._connect(home)) as con, con:  # pyright: ignore[reportPrivateUsage]
        con.execute("INSERT INTO counters VALUES (?, 'cli', 'command', 'video t2v', 2)", (week,))
        con.executemany(
            "INSERT OR IGNORE INTO meta VALUES (?, ?)",
            [
                ("notice_seen_version", str(telemetry.NOTICE_VERSION)),
                (
                    "notice_seen_at",
                    (notice_at or week_start - timedelta(microseconds=1)).isoformat(),
                ),
            ],
        )
        if due_at is not None or sent_at is not None:
            con.execute(
                "INSERT OR REPLACE INTO reports (week, report_id, due_at, sent_at)"
                " VALUES (?, 'abcdefghijklmnop', ?, ?)",
                (
                    week,
                    due_at.isoformat() if due_at else None,
                    sent_at.isoformat() if sent_at else None,
                ),
            )


@contextmanager
def _server(
    status: int = 204, retry_after: str | None = None
) -> Iterator[tuple[str, list[bytes], list[str]]]:
    bodies: list[bytes] = []
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            paths.append(self.path)
            bodies.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            self.send_response(status)
            if retry_after is not None:
                self.send_header("Retry-After", retry_after)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/report", bodies, paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _allow_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli import telemetry_upload

    monkeypatch.setattr(telemetry, "uploads_enabled", lambda: True)
    monkeypatch.setattr(telemetry, "_NOTICE_SHOWN_THIS_RUN", False)
    monkeypatch.setattr(telemetry_upload.subprocess, "Popen", Mock())


def _meta(home: Path) -> dict[str, str]:
    with closing(sqlite3.connect(home / "telemetry.db")) as con:
        return dict(con.execute("SELECT key, value FROM meta"))


def _report(home: Path, week: str) -> tuple[str, str | None, str | None] | None:
    with closing(sqlite3.connect(home / "telemetry.db")) as con:
        row = con.execute(
            "SELECT report_id, due_at, sent_at FROM reports WHERE week = ?", (week,)
        ).fetchone()
    return row


def test_not_enabled_spawns_nothing(monkeypatch: pytest.MonkeyPatch, _home: Path) -> None:
    from gflow_cli import telemetry_upload

    week = _week_id(_week_start())
    _seed_week(_home, week)
    monkeypatch.setattr(telemetry, "uploads_enabled", lambda: False)
    popen = Mock()
    monkeypatch.setattr(telemetry_upload.subprocess, "Popen", popen)
    telemetry_upload.maybe_upload()
    popen.assert_not_called()


def test_weeks_before_the_notice_are_never_sent(_home: Path) -> None:
    from gflow_cli import telemetry_upload

    start = _week_start()
    week = _week_id(start)
    _seed_week(_home, week, notice_at=start + timedelta(days=1))
    assert telemetry_upload.next_due_week(datetime.now(UTC)) is None
    assert _report(_home, week) is None


def test_week_is_not_due_before_its_random_delay(
    monkeypatch: pytest.MonkeyPatch, _home: Path
) -> None:
    from gflow_cli import telemetry_upload

    start = _week_start()
    week = _week_id(start)
    _seed_week(_home, week)
    monkeypatch.setattr(telemetry_upload.secrets, "randbelow", lambda n: 86_400)
    now = start + timedelta(days=7, hours=12)
    assert telemetry_upload.next_due_week(now) is None
    assert _report(_home, week)[1] == (start + timedelta(days=8)).isoformat()


def test_delay_is_redrawn_each_week(monkeypatch: pytest.MonkeyPatch, _home: Path) -> None:
    from gflow_cli import telemetry_upload

    starts = [_week_start(3), _week_start(2)]
    weeks = [_week_id(start) for start in starts]
    for start, week in zip(starts, weeks, strict=True):
        _seed_week(_home, week, start=start, notice_at=starts[0] - timedelta(microseconds=1))
    values = iter((3600, 7200))
    monkeypatch.setattr(telemetry_upload.secrets, "randbelow", lambda n: next(values))
    telemetry_upload.next_due_week(starts[0] + timedelta(days=8))
    assert _report(_home, weeks[0])[1] == (starts[0] + timedelta(days=7, hours=1)).isoformat()
    with closing(sqlite3.connect(_home / "telemetry.db")) as con, con:
        con.execute(
            "UPDATE reports SET sent_at = ? WHERE week = ?",
            (datetime.now(UTC).isoformat(), weeks[0]),
        )
    telemetry_upload.next_due_week(starts[1] + timedelta(days=8))
    assert _report(_home, weeks[1])[1] == (starts[1] + timedelta(days=7, hours=2)).isoformat()


def test_at_most_one_attempt_a_day(monkeypatch: pytest.MonkeyPatch, _home: Path) -> None:
    from gflow_cli import telemetry_upload

    _allow_upload(monkeypatch)
    _seed_week(_home, _week_id(_week_start()))
    telemetry_upload.maybe_upload()
    telemetry_upload.maybe_upload()
    assert telemetry_upload.subprocess.Popen.call_count == 1


def test_one_report_per_attempt_oldest_first(_home: Path) -> None:
    from gflow_cli import telemetry_upload

    starts = [_week_start(3), _week_start(2)]
    weeks = [_week_id(start) for start in starts]
    for start, week in zip(starts, weeks, strict=True):
        _seed_week(
            _home,
            week,
            start=start,
            due_at=start + timedelta(days=7),
            notice_at=starts[0] - timedelta(microseconds=1),
        )
    now = datetime.now(UTC)
    assert telemetry_upload.next_due_week(now) == weeks[0]
    with _server() as (endpoint, _, _):
        telemetry_upload.send(weeks[0], endpoint=endpoint)
    assert telemetry_upload.next_due_week(now) == weeks[1]


def test_204_marks_sent_and_keeps_rows(_home: Path) -> None:
    from gflow_cli import telemetry_upload

    week = _week_id(_week_start())
    _seed_week(_home, week, due_at=datetime.now(UTC) - timedelta(days=1))
    with _server() as (endpoint, _, _):
        telemetry_upload.send(week, endpoint=endpoint)
    assert _report(_home, week)[2] is not None
    assert any(row.week == week for row in telemetry.read_counters())


def test_request_names_gflow_as_its_user_agent(_home: Path) -> None:
    # Cloudflare's edge answers 403 (error 1010) to the default "Python-urllib/3.x" agent, so a
    # report sent without our own agent never reaches the Worker (found by the live e2e).
    from gflow_cli import __version__, telemetry_upload

    agents: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            agents.append(self.headers.get("User-Agent"))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    week = _week_id(_week_start())
    _seed_week(_home, week, due_at=datetime.now(UTC) - timedelta(days=1))
    try:
        telemetry_upload.send(week, endpoint=f"http://127.0.0.1:{server.server_port}/report")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert agents == [f"gflow-cli/{__version__}"]


@pytest.mark.parametrize("status", [400, 413])
def test_rejected_report_is_dropped_not_retried(status: int, _home: Path) -> None:
    from gflow_cli import telemetry_upload

    week = _week_id(_week_start())
    _seed_week(_home, week, due_at=datetime.now(UTC) - timedelta(days=1))
    with _server(status) as (endpoint, _, _):
        telemetry_upload.send(week, endpoint=endpoint)
    assert _report(_home, week)[2] is not None


def test_429_waits_for_retry_after_plus_a_random_extra(
    monkeypatch: pytest.MonkeyPatch, _home: Path
) -> None:
    from gflow_cli import telemetry_upload

    week = _week_id(_week_start())
    _seed_week(_home, week, due_at=datetime.now(UTC) - timedelta(days=1))
    monkeypatch.setattr(telemetry_upload.secrets, "randbelow", lambda n: 1800)
    before = datetime.now(UTC)
    with _server(429, "3600") as (endpoint, _, _):
        telemetry_upload.send(week, endpoint=endpoint)
    retry = datetime.fromisoformat(_meta(_home)[f"retry_after:{week}"])
    assert timedelta(seconds=5400) <= retry - before <= timedelta(seconds=5401)
    assert _report(_home, week)[2] is None


@pytest.mark.parametrize("failure", [500, "timeout", "dns"])
def test_transient_failure_keeps_the_week(failure: int | str, _home: Path) -> None:
    from gflow_cli import telemetry_upload

    week = _week_id(_week_start())
    _seed_week(_home, week, due_at=datetime.now(UTC) - timedelta(days=1))
    if failure == 500:
        with _server(500) as (endpoint, _, _):
            telemetry_upload.send(week, endpoint=endpoint)
    else:
        error: BaseException = (
            TimeoutError("timeout") if failure == "timeout" else socket.gaierror("dns")
        )

        class BrokenOpener:
            def open(self, *_: object, **__: object) -> Any:
                raise urllib.error.URLError(error)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(urllib.request, "build_opener", lambda *_: BrokenOpener())
        try:
            telemetry_upload.send(week, endpoint="http://127.0.0.1:1/report")
        finally:
            monkeypatch.undo()
    assert _report(_home, week)[2] is None


def test_410_stops_uploading_forever(_home: Path) -> None:
    from gflow_cli import telemetry_upload

    week = _week_id(_week_start())
    _seed_week(_home, week, due_at=datetime.now(UTC) - timedelta(days=1))
    with _server(410) as (endpoint, _, _):
        telemetry_upload.send(week, endpoint=endpoint)
    assert _meta(_home)["upload_disabled"] == "1"
    assert not telemetry.uploads_enabled()


def test_redirect_is_not_followed(_home: Path) -> None:
    from gflow_cli import telemetry_upload

    week = _week_id(_week_start())
    _seed_week(_home, week, due_at=datetime.now(UTC) - timedelta(days=1))
    with _server(302) as (endpoint, _, paths):
        telemetry_upload.send(week, endpoint=endpoint)
    assert paths == ["/report"]
    assert _report(_home, week)[2] is None


def test_body_is_never_read(monkeypatch: pytest.MonkeyPatch, _home: Path) -> None:
    from gflow_cli import telemetry_upload

    week = _week_id(_week_start())
    _seed_week(_home, week, due_at=datetime.now(UTC) - timedelta(days=1))

    class Response:
        status = 204
        headers: dict[str, str] = {}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, *_: object) -> bytes:
            raise AssertionError("response body read")

    class Opener:
        def open(self, *_: object, **__: object) -> Response:
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: Opener())
    telemetry_upload.send(week, endpoint="http://127.0.0.1/report")
    assert _report(_home, week)[2] is not None


@pytest.mark.sharpit(allow_n_plus_one=True)  # eight daily attempts in one test, one read each
def test_two_months_offline_sends_each_week_once(_home: Path) -> None:
    from gflow_cli import telemetry_upload

    starts = [_week_start(i) for i in reversed(range(2, 10))]
    weeks = [_week_id(start) for start in starts]
    for start, week in zip(starts, weeks, strict=True):
        _seed_week(
            _home,
            week,
            start=start,
            due_at=start + timedelta(days=7),
            notice_at=starts[0] - timedelta(microseconds=1),
        )
    with _server() as (endpoint, bodies, _):
        for _ in weeks:
            week = telemetry_upload.next_due_week(datetime.now(UTC))
            assert week is not None
            telemetry_upload.send(week, endpoint=endpoint)
    assert len(bodies) == len(weeks)
    assert {json.loads(body)["week"] for body in bodies} == set(weeks)


def test_future_clock_loses_nothing(monkeypatch: pytest.MonkeyPatch, _home: Path) -> None:
    from gflow_cli import telemetry_upload

    _allow_upload(monkeypatch)
    week = _week_id(_week_start())
    _seed_week(_home, week)
    with closing(telemetry._connect(_home)) as con, con:  # pyright: ignore[reportPrivateUsage]
        con.execute(
            "INSERT OR REPLACE INTO meta VALUES ('last_attempt', ?)",
            ((datetime.now(UTC) + timedelta(days=2)).isoformat(),),
        )
    telemetry_upload.maybe_upload()
    assert telemetry_upload.subprocess.Popen.call_count == 1


def test_maybe_upload_never_raises(monkeypatch: pytest.MonkeyPatch, _home: Path) -> None:
    from gflow_cli import telemetry_upload

    monkeypatch.setattr(telemetry, "uploads_enabled", Mock(side_effect=RuntimeError("broken")))
    telemetry_upload.maybe_upload()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows creationflags")
def test_child_has_no_console_window_on_windows(
    monkeypatch: pytest.MonkeyPatch, _home: Path
) -> None:
    from gflow_cli import telemetry_upload

    monkeypatch.setattr(telemetry, "uploads_enabled", lambda: True)
    monkeypatch.setattr(telemetry, "_NOTICE_SHOWN_THIS_RUN", False)
    week = _week_id(_week_start())
    _seed_week(_home, week)
    popen = Mock()
    monkeypatch.setattr(telemetry_upload.subprocess, "Popen", popen)
    telemetry_upload.maybe_upload()
    assert popen.call_args is not None
    kwargs = popen.call_args.kwargs
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    assert kwargs["creationflags"] == flags
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX session")
def test_child_starts_a_new_session_on_posix(monkeypatch: pytest.MonkeyPatch, _home: Path) -> None:
    from gflow_cli import telemetry_upload

    monkeypatch.setattr(telemetry, "uploads_enabled", lambda: True)
    monkeypatch.setattr(telemetry, "_NOTICE_SHOWN_THIS_RUN", False)
    week = _week_id(_week_start())
    _seed_week(_home, week)
    popen = Mock()
    monkeypatch.setattr(telemetry_upload.subprocess, "Popen", popen)
    telemetry_upload.maybe_upload()
    assert popen.call_args is not None
    kwargs = popen.call_args.kwargs
    assert popen.call_args.args[0] == [sys.executable, "-P", "-m", "gflow_cli.telemetry_upload"]
    assert kwargs["cwd"] == os.getcwd()
    assert kwargs["env"]["GFLOW_CLI_HOME"] == str(_home.resolve())
    assert kwargs.get("start_new_session") is True
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_child_safe_path_does_not_import_json_from_home(
    monkeypatch: pytest.MonkeyPatch, _home: Path, tmp_path: Path
) -> None:
    from gflow_cli import telemetry_upload

    _seed_week(_home, _week_id(_week_start()), due_at=_week_start())
    (_home / "json.py").write_text("raise RuntimeError('home json imported')", encoding="utf-8")
    marker = tmp_path / "json-origin"
    code = (
        "import json, os; from pathlib import Path; "
        f"Path({str(marker)!r}).write_text(json.__file__, encoding='utf-8')"
    )
    real_popen = subprocess.Popen

    def spawn(args: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        assert args == [sys.executable, "-P", "-m", "gflow_cli.telemetry_upload"]
        assert kwargs["env"]["GFLOW_CLI_HOME"] == str(_home.resolve())
        assert kwargs["cwd"] == os.getcwd()
        command = list(args)
        command[command.index("-m")] = "-c"
        command[command.index("gflow_cli.telemetry_upload")] = code
        child = real_popen(command, **kwargs)
        child.wait(timeout=5)
        return child

    monkeypatch.setattr(telemetry_upload.subprocess, "Popen", spawn)
    monkeypatch.setattr(telemetry, "uploads_enabled", lambda: True)
    telemetry_upload.maybe_upload()
    assert marker.exists()
    assert Path(marker.read_text(encoding="utf-8")).resolve() != (_home / "json.py").resolve()


def test_report_survives_the_parent_exiting_at_once(_home: Path, tmp_path: Path) -> None:

    start = _week_start()
    week = _week_id(start)
    _seed_week(_home, week, start=start, due_at=start + timedelta(days=7))
    received: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/report"
    metadata = tmp_path / "metadata"
    dist = metadata / "gflow_cli-0.0.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: gflow-cli\nVersion: 0.0.0\n")
    source_root = str(Path(telemetry.__file__).resolve().parents[1])
    child_code = f"import gflow_cli.telemetry_upload as t; t._ENDPOINT = {endpoint!r}; t._main()"
    shim = (
        "import subprocess, sys\nfrom unittest.mock import patch\n"
        "from gflow_cli import telemetry_upload\nreal = subprocess.Popen\n"
        "def spawn(args, **kwargs):\n"
        f"    return real([sys.executable, '-P', '-c', {child_code!r}], **kwargs)\n"
        "patch('gflow_cli.telemetry_upload.subprocess.Popen', spawn).start()\n"
        "telemetry_upload.maybe_upload()"
    )
    env = dict(os.environ)
    env.update(
        GFLOW_CLI_HOME=str(_home),
        PYTHONPATH=os.pathsep.join((str(metadata), source_root)),
        CI="",
    )
    try:
        subprocess.run([sys.executable, "-c", shim], env=env, cwd=tmp_path, check=True, timeout=5)
        deadline = time.monotonic() + 10
        while not received and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(received) == 1
        assert json.loads(received[0])["week"] == week
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_send_body_equals_build_report_for_same_week(_home: Path) -> None:
    from gflow_cli import telemetry_upload

    week = _week_id(_week_start())
    _seed_week(_home, week, due_at=datetime.now(UTC) - timedelta(days=1))
    expected = telemetry.build_report(week)
    with _server() as (endpoint, bodies, _):
        telemetry_upload.send(week, endpoint=endpoint)
    assert json.loads(bodies[0]) == expected


def test_send_without_stored_report_id_keeps_week(_home: Path) -> None:
    from gflow_cli import telemetry_upload

    week = _week_id(_week_start())
    _seed_week(_home, week)
    with _server() as (endpoint, bodies, _):
        telemetry_upload.send(week, endpoint=endpoint)
    assert bodies == []
    assert _report(_home, week) is None


def test_recent_attempt_does_not_open_per_week_connections(
    monkeypatch: pytest.MonkeyPatch, _home: Path
) -> None:
    from gflow_cli import telemetry_upload, update_check

    monkeypatch.setattr(update_check, "_installed_from_index", lambda: True)
    monkeypatch.setattr(telemetry, "_NOTICE_SHOWN_THIS_RUN", False)
    popen = Mock()
    monkeypatch.setattr(telemetry_upload.subprocess, "Popen", popen)
    now = datetime.now(UTC)
    notice_at = now - timedelta(weeks=60)
    for weeks_ago in range(1, 53):
        start = _week_start(weeks_ago)
        _seed_week(
            _home,
            _week_id(start),
            start=start,
            notice_at=notice_at,
        )
    with closing(telemetry._connect(_home)) as con, con:  # pyright: ignore[reportPrivateUsage]
        con.execute(
            "INSERT OR REPLACE INTO meta VALUES ('last_attempt', ?)",
            (now.isoformat(),),
        )

    connect = sqlite3.connect
    calls = 0

    def count_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        nonlocal calls
        calls += 1
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", count_connect)
    telemetry_upload.maybe_upload()
    assert calls <= 2


def test_importing_telemetry_upload_alone_does_not_import_urllib_request() -> None:
    res = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import gflow_cli.telemetry_upload; print('urllib.request' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == "False"


def test_recent_attempt_does_not_execute_begin_immediate(
    monkeypatch: pytest.MonkeyPatch, _home: Path
) -> None:
    from gflow_cli import telemetry_upload

    _allow_upload(monkeypatch)
    week = _week_id(_week_start())
    _seed_week(_home, week)
    now = datetime.now(UTC)
    with closing(telemetry._connect(_home)) as con, con:  # pyright: ignore[reportPrivateUsage]
        con.execute(
            "INSERT OR REPLACE INTO meta VALUES ('last_attempt', ?)",
            (now.isoformat(),),
        )

    executed: list[str] = []
    real_connect = telemetry._connect

    def spy_connect(home: Path) -> sqlite3.Connection:
        con = real_connect(home)
        con.set_trace_callback(executed.append)
        return con

    monkeypatch.setattr(telemetry, "_connect", spy_connect)
    telemetry_upload.maybe_upload()
    assert not any("BEGIN IMMEDIATE" in stmt.upper() for stmt in executed)
