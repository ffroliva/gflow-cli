"""End-to-end tests for usage telemetry notice, opt-out, and weekly upload (#1164).

Validates first-run notice in a real PTY, pipe suppression, opt-out mechanisms,
and weekly upload lifecycle against an installed wheel and the live Cloudflare endpoint.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_telemetry]

WRANGLER_DIR = Path(r"C:\development\github\gflow-telemetry-endpoint")
CLOUDFLARE_ACCOUNT_ID = "6e84ee0d4c899e5b5fa648302e0bfba9"

# Spec allow-listed fields + edge country + crosses + reports
ALLOWED_D1_FIELDS = {
    "schema",
    "version",
    "python",
    "os",
    "arch",
    "installer",
    "install_age",
    "storage",
    "commands",
    "flags",
    "exits",
    "durations",
    "transitions",
    "models",
    "modes",
    "aspects",
    "outcomes",
    "mcp_clients",
    "crashes",
    "tools",
    "country",
    "reports",
    "x:os_exit",
    "x:version_exit",
    "x:storage_exit",
    "x:os_install_age",
    "x:installer_update_exit",
}

_USED_WEEKS: set[str] = set()
_USED_HOMES: set[Path] = set()

CI_VARS = {
    "CI",
    "GITHUB_ACTIONS",
    "CONTINUOUS_INTEGRATION",
    "TRAVIS",
    "CIRCLECI",
    "APPVEYOR",
    "BUILDKITE",
    "GITLAB_CI",
}


def is_live_enabled() -> bool:
    """Whether live Cloudflare endpoint testing is opted in and wrangler is available."""
    if os.environ.get("GFLOW_CLI_E2E_RUN_TELEMETRY_UPLOAD") != "1":
        return False
    if not os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
        return False
    try:
        res = subprocess.run(
            ["npx", "wrangler", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            shell=sys.platform == "win32",
        )
        return res.returncode == 0
    except Exception:
        return False


requires_live = pytest.mark.skipif(
    not is_live_enabled(),
    reason=(
        "Live upload requires GFLOW_CLI_E2E_RUN_TELEMETRY_UPLOAD=1, "
        "CLOUDFLARE_ACCOUNT_ID, and functional npx wrangler"
    ),
)


@dataclass(frozen=True)
class InstalledGFlow:
    gflow: Path
    python: Path
    venv_dir: Path


@pytest.fixture(scope="module")
def installed_gflow(tmp_path_factory: pytest.TempPathFactory) -> Iterator[InstalledGFlow]:
    """Build a wheel from this worktree root, install into a fresh venv, and yield binaries."""
    worktree_root = Path(__file__).resolve().parent.parent.parent
    dist_dir = tmp_path_factory.mktemp("dist")
    venv_dir = tmp_path_factory.mktemp("venv")

    # 1. Build wheel into <tmp>/dist
    build_res = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(dist_dir)],
        cwd=str(worktree_root),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert build_res.returncode == 0, f"uv build failed:\n{build_res.stderr}"

    wheel_files = list(dist_dir.glob("gflow_cli-*.whl"))
    assert len(wheel_files) == 1, f"Expected 1 wheel in {dist_dir}, found: {wheel_files}"
    wheel_path = wheel_files[0]

    # 2. Create fresh venv with python 3.12
    venv_res = subprocess.run(
        ["uv", "venv", str(venv_dir), "--python", "3.12"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert venv_res.returncode == 0, f"uv venv failed:\n{venv_res.stderr}"

    if sys.platform == "win32":
        venv_python = venv_dir / "Scripts" / "python.exe"
        venv_gflow = venv_dir / "Scripts" / "gflow.exe"
    else:
        venv_python = venv_dir / "bin" / "python"
        venv_gflow = venv_dir / "bin" / "gflow"

    assert venv_python.exists(), f"Python binary not found at {venv_python}"

    # Install dependencies by installing the wheel
    install_res = subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), str(wheel_path)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert install_res.returncode == 0, f"uv pip install failed:\n{install_res.stderr}"

    # Reinstall with --no-index --find-links to simulate index install (no direct_url.json)
    force_res = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv_python),
            "--no-index",
            "--find-links",
            str(dist_dir),
            "--no-deps",
            "--reinstall",
            "gflow-cli",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert force_res.returncode == 0, f"uv pip reinstall failed:\n{force_res.stderr}"
    assert venv_gflow.exists(), f"gflow binary not found at {venv_gflow}"

    # On Windows, install pywinpty into the temp venv for real PTY tests
    if sys.platform == "win32":
        winpty_res = subprocess.run(
            ["uv", "pip", "install", "--python", str(venv_python), "pywinpty"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert winpty_res.returncode == 0, f"Failed to install pywinpty:\n{winpty_res.stderr}"

    # Assert package verification
    sysconfig_res = subprocess.run(
        [str(venv_python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True,
        text=True,
        check=True,
    )
    site_packages = Path(sysconfig_res.stdout.strip())
    telemetry_file = site_packages / "gflow_cli" / "telemetry.py"
    assert telemetry_file.is_file(), f"telemetry.py missing at {telemetry_file}"

    direct_urls = list(site_packages.glob("gflow_cli-*.dist-info/direct_url.json"))
    assert not direct_urls, f"direct_url.json found (expected index install): {direct_urls}"

    yield InstalledGFlow(gflow=venv_gflow, python=venv_python, venv_dir=venv_dir)


@pytest.fixture
def clean_home(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Isolated home directory and scrubbed environment."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GFLOW_CLI_") and k != "DO_NOT_TRACK" and k not in CI_VARS
    }
    env["GFLOW_CLI_HOME"] = str(home)
    env["GFLOW_CLI_UPDATE_CHECK"] = "0"
    env["PYTHONUTF8"] = "1"
    env["TZ"] = "America/New_York"
    return home, env


def run_d1_sql(sql: str) -> list[dict[str, Any]]:
    """Execute SQL query against remote D1 database via wrangler and return results."""
    env = dict(os.environ)
    env["CLOUDFLARE_ACCOUNT_ID"] = CLOUDFLARE_ACCOUNT_ID
    cmd = [
        "npx",
        "wrangler",
        "d1",
        "execute",
        "gflow-telemetry",
        "--remote",
        "--json",
        "--command",
        sql,
    ]
    res = subprocess.run(
        cmd,
        cwd=str(WRANGLER_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        shell=sys.platform == "win32",
    )
    assert res.returncode == 0, f"wrangler d1 execute failed:\n{res.stderr}\n{res.stdout}"
    data = json.loads(res.stdout)
    if isinstance(data, list) and data and "results" in data[0]:
        return data[0]["results"]
    return []


def _endpoint_test_env(home: Path, env: dict[str, str], endpoint: str) -> dict[str, str]:
    """Inject the loopback URL in the test process that starts the detached sender."""
    hook = home / "test-endpoint-hook"
    hook.mkdir(parents=True, exist_ok=True)
    child_code = f"import gflow_cli.telemetry_upload as t; t._ENDPOINT = {endpoint!r}; t._main()"
    (hook / "sitecustomize.py").write_text(
        "import subprocess, sys\n"
        "_real_popen = subprocess.Popen\n"
        "class _P(_real_popen):\n"
        "    def __init__(self, args=None, *pos, **kwargs):\n"
        "        if args is None and 'args' in kwargs:\n"
        "            args = kwargs.pop('args')\n"
        "        if isinstance(args, (list, tuple)) and '-m' in args and "
        "'gflow_cli.telemetry_upload' in args:\n"
        f"            code = {child_code!r}\n"
        "            args = [sys.executable, '-P', '-c', code]\n"
        "        super().__init__(args, *pos, **kwargs)\n"
        "subprocess.Popen = _P\n",
        encoding="utf-8",
    )
    child_env = dict(env)
    child_env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(hook), env.get("PYTHONPATH", "")) if part
    )
    return child_env


@contextmanager
def stopped_local_worker(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """Run the test Worker locally with uploads stopped, then kill its process."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log_path = tmp_path / "wrangler-local.log"
    with log_path.open("w", encoding="utf-8") as log:
        kwargs: dict[str, Any] = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if sys.platform == "win32"
            else {"start_new_session": True}
        )
        proc = subprocess.Popen(
            ["npx", "wrangler", "dev", "--port", str(port), "--var", "STOPPED:1", "--local"],
            cwd=str(WRANGLER_DIR),
            stdout=log,
            stderr=subprocess.STDOUT,
            shell=sys.platform == "win32",
            **kwargs,
        )
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                output = log_path.read_text(encoding="utf-8", errors="replace")
                if "Ready on" in output:
                    yield f"http://127.0.0.1:{port}/v1/report", log_path
                    return
                if proc.poll() is not None:
                    raise RuntimeError(f"wrangler dev exited early:\n{output}")
                time.sleep(0.2)
            raise TimeoutError(f"wrangler dev did not become ready:\n{log_path.read_text()}")
        finally:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                try:
                    import signal

                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                proc.wait(timeout=10)
            except Exception:
                pass

            deadline_port = time.monotonic() + 5.0
            port_closed = False
            while time.monotonic() < deadline_port:
                with socket.socket() as s:
                    s.settimeout(0.5)
                    if s.connect_ex(("127.0.0.1", port)) != 0:
                        port_closed = True
                        break
                time.sleep(0.2)
            assert port_closed, f"Port {port} still open after stopping wrangler dev"


def run_in_pty(
    installed_gflow: InstalledGFlow, cmd: list[str], env: dict[str, str], cwd: Path
) -> str:
    """Run command under a real pseudo-terminal and return combined output."""
    if sys.platform == "win32":
        driver = f"""
import sys
from winpty import PtyProcess

proc = PtyProcess.spawn({cmd!r}, env={env!r}, cwd={str(cwd)!r})
proc.wait()
sys.stdout.write(proc.read())
"""
        res = subprocess.run(
            [str(installed_gflow.python), "-c", driver],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert res.returncode == 0, f"PTY runner failed:\n{res.stderr}"
        return res.stdout
    else:
        import pty

        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=str(cwd),
                env=env,
                close_fds=True,
            )
            os.close(slave)
            output = b""
            while True:
                try:
                    data = os.read(master, 1024)
                    if not data:
                        break
                    output += data
                except OSError:
                    break
            proc.wait(timeout=30)
            return output.decode("utf-8", errors="replace")
        finally:
            os.close(master)


def seed_telemetry_db(
    installed_gflow: InstalledGFlow,
    home: Path,
    env: dict[str, str],
    *,
    with_notice: bool = True,
    weeks_ago: int = 40,
    probe_id: str | None = None,
) -> tuple[str, str]:
    """Seed <home>/telemetry.db via the installed interpreter."""
    if not 30 <= weeks_ago <= 50:
        raise ValueError("live upload tests may seed only weeks 30-50")
    if probe_id is None:
        probe_id = secrets.token_hex(4)
    marker = f"e2eprobe {probe_id}"

    seed_script = f"""
import os, sys, sqlite3
from datetime import datetime, UTC, timedelta
from pathlib import Path
from gflow_cli import telemetry

home = Path(r'{home}')
now = datetime.now(UTC)
target_dt = now - timedelta(days={weeks_ago} * 7)
week = telemetry.iso_week(target_dt)
iso = target_dt.isocalendar()
week_start = datetime.fromisocalendar(iso.year, iso.week, 1).replace(tzinfo=UTC)

# 1. Create schema
with telemetry._connect(home) as con:
    pass

# 2. Seed tables directly
with sqlite3.connect(home / 'telemetry.db') as con:
    if {with_notice}:
        con.execute(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)",
            ('notice_seen_version', '2')
        )
        con.execute(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)",
            ('notice_seen_at', (week_start - timedelta(microseconds=1)).isoformat())
        )
    con.execute(
        "INSERT INTO counters (week, surface, kind, key, n) VALUES (?, 'cli', 'command', ?, 1)",
        (week, '{marker}')
    )
    con.execute(
        "INSERT INTO counters (week, surface, kind, key, n) VALUES (?, 'cli', 'exit', ?, 1)",
        (week, '{marker} 0')
    )

print(week)
"""
    res = subprocess.run(
        [str(installed_gflow.python), "-c", seed_script],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    week = res.stdout.strip()
    _USED_WEEKS.add(week)
    _USED_HOMES.add(home)
    return week, marker


@contextmanager
def exact_d1_cleanup(week: str, home: Path) -> Iterator[None]:
    """Snapshot totals for week before test, and subtract exact delta after test."""
    if not is_live_enabled():
        yield
        return
    # Residual risk: a real client posting the same old week within the same seconds.
    before_rows = run_d1_sql(f"SELECT field, key, n FROM totals WHERE week = '{week}'")
    before_counts = {(r["field"], r["key"]): int(r["n"]) for r in before_rows}
    try:
        yield
    finally:
        try:
            after_rows = run_d1_sql(f"SELECT field, key, n FROM totals WHERE week = '{week}'")
            after_counts = {(r["field"], r["key"]): int(r["n"]) for r in after_rows}
            stmts: list[str] = []
            for (field, key), after_n in after_counts.items():
                before_n = before_counts.get((field, key), 0)
                delta = after_n - before_n
                if delta > 0:
                    escaped_field = field.replace("'", "''")
                    escaped_key = key.replace("'", "''")
                    stmts.append(
                        f"UPDATE totals SET n = n - {delta} "
                        f"WHERE week = '{week}' AND field = '{escaped_field}' "
                        f"AND key = '{escaped_key}'; "
                        f"DELETE FROM totals "
                        f"WHERE week = '{week}' AND field = '{escaped_field}' "
                        f"AND key = '{escaped_key}' AND n <= 0;"
                    )
            if stmts:
                run_d1_sql(" ".join(stmts))

            db_path = home / "telemetry.db"
            if db_path.exists():
                with sqlite3.connect(db_path) as con:
                    report_ids = [
                        row[0] for row in con.execute("SELECT report_id FROM reports") if row[0]
                    ]
                if report_ids:
                    seen_stmts: list[str] = []
                    for r_id in report_ids:
                        escaped_r_id = r_id.replace("'", "''")
                        seen_stmts.append(f"DELETE FROM seen WHERE report_id = '{escaped_r_id}';")
                    run_d1_sql(" ".join(seen_stmts))
        except Exception as exc:
            print(f"\n[D1_CLEANUP] Cleanup error for week {week}: {exc}", file=sys.stderr)


@pytest.fixture(scope="module", autouse=True)
def d1_cleanup() -> Iterator[None]:
    """Module-level cleanup: delete test rows from seen and record final counts."""
    yield
    if not is_live_enabled():
        return
    try:
        report_ids: set[str] = set()
        for home in _USED_HOMES:
            db_path = home / "telemetry.db"
            if db_path.exists():
                with sqlite3.connect(db_path) as con:
                    report_ids.update(
                        row[0] for row in con.execute("SELECT report_id FROM reports") if row[0]
                    )
        for report_id in report_ids:
            escaped_id = report_id.replace("'", "''")
            run_d1_sql(f"DELETE FROM seen WHERE report_id = '{escaped_id}'")

        t_cnt = run_d1_sql("SELECT count(*) AS count FROM totals")
        s_cnt = run_d1_sql("SELECT count(*) AS count FROM seen")
        print(f"\n[D1_CLEANUP] Final totals count: {t_cnt}, seen count: {s_cnt}")
    except Exception as exc:
        print(f"\n[D1_CLEANUP] Cleanup error: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_notice_prints_once_in_a_real_tty_and_nothing_is_sent(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    """Notice prints once in interactive TTY, sets notice_seen_version, and sends nothing."""
    home, env = clean_home
    week, marker = seed_telemetry_db(installed_gflow, home, env, with_notice=False)

    from gflow_cli.telemetry import NOTICE_TEXT

    first_line = NOTICE_TEXT.splitlines()[0]

    # Run 1: real PTY -> notice prints
    cmd = [str(installed_gflow.gflow), "data", "list", "projects", "--json"]
    out1 = run_in_pty(installed_gflow, cmd, env, home)
    assert first_line in out1, f"Notice first line missing from output:\n{out1}"

    # Verify local DB: notice recorded, nothing sent
    with sqlite3.connect(home / "telemetry.db") as con:
        meta = dict(con.execute("SELECT key, value FROM meta"))
        assert meta.get("notice_seen_version") == "2"
        assert "last_attempt" not in meta

        row = con.execute("SELECT sent_at FROM reports WHERE week = ?", (week,)).fetchone()
        assert row is None or row[0] is None, f"Expected sent_at NULL, got {row}"

    # If live is enabled, assert D1 has no row for this marker after 20 s
    if is_live_enabled():
        time.sleep(20)
        d1_rows = run_d1_sql(
            f"SELECT count(*) AS count FROM totals WHERE week = '{week}' AND key = 'cli|{marker}'"
        )
        assert d1_rows and d1_rows[0].get("count") == 0, f"Unexpected D1 row found: {d1_rows}"

    # Run 2: same command in PTY -> notice does NOT print again
    out2 = run_in_pty(installed_gflow, cmd, env, home)
    assert first_line not in out2, f"Notice printed second time:\n{out2}"


def test_notice_absent_when_piped(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    """In non-interactive piped run, notice is not printed and meta is unchanged."""
    home, env = clean_home
    seed_telemetry_db(installed_gflow, home, env, with_notice=False)

    from gflow_cli.telemetry import NOTICE_TEXT

    first_line = NOTICE_TEXT.splitlines()[0]

    res = subprocess.run(
        [str(installed_gflow.gflow), "data", "list", "projects", "--json"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert first_line not in res.stderr
    assert first_line not in res.stdout

    with sqlite3.connect(home / "telemetry.db") as con:
        meta = dict(con.execute("SELECT key, value FROM meta"))
        assert "notice_seen_version" not in meta


@requires_live
def test_one_real_post_lands_in_d1_from_a_command_that_exits_at_once(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    """Detached uploader delivers weekly totals to D1 from command that exits at once."""
    home, env = clean_home
    week, marker = seed_telemetry_db(installed_gflow, home, env, with_notice=True)

    # Fetch client's public IP once
    public_ip: str | None = None
    try:
        ip_fetch_code = (
            "import urllib.request as u; "
            "print(u.urlopen('https://api.ipify.org', timeout=5).read().decode().strip())"
        )
        ip_res = subprocess.run(
            [str(installed_gflow.python), "-c", ip_fetch_code],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if ip_res.returncode == 0 and ip_res.stdout.strip():
            public_ip = ip_res.stdout.strip()
    except Exception:
        pass

    with exact_d1_cleanup(week, home):
        # Run command that exits immediately; detached child does the POST
        res = subprocess.run(
            [str(installed_gflow.gflow), "data", "list", "projects", "--json"],
            cwd=str(home),
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0

        # Poll up to 60 s for local reports.sent_at to become non-NULL
        deadline = time.monotonic() + 60.0
        sent_at: str | None = None
        while time.monotonic() < deadline:
            with sqlite3.connect(home / "telemetry.db") as con:
                cur = con.execute("SELECT sent_at FROM reports WHERE week = ?", (week,))
                row = cur.fetchone()
                if row and row[0] is not None:
                    sent_at = row[0]
                    break
            time.sleep(1.0)

        assert sent_at is not None, f"Timed out waiting for local sent_at for week {week}"

        # Poll up to 60 s for D1 rows to reflect the marker and reports/all
        deadline_d1 = time.monotonic() + 60.0
        totals_rows: list[dict[str, Any]] = []
        while time.monotonic() < deadline_d1:
            totals_rows = run_d1_sql(f"SELECT field, key, n FROM totals WHERE week = '{week}'")
            has_marker = any(
                r.get("field") == "commands" and r.get("key") == f"cli|{marker}"
                for r in totals_rows
            )
            has_report = any(
                r.get("field") == "reports" and r.get("key") == "all" for r in totals_rows
            )
            if has_marker and has_report:
                break
            time.sleep(1.0)

        # Assert D1 totals
        marker_row = [
            r
            for r in totals_rows
            if r.get("field") == "commands" and r.get("key") == f"cli|{marker}"
        ]
        report_row = [
            r for r in totals_rows if r.get("field") == "reports" and r.get("key") == "all"
        ]
        assert marker_row and marker_row[0].get("n") == 1, (
            f"Missing marker command key cli|{marker}: {totals_rows}"
        )
        assert report_row and report_row[0].get("n") == 1, f"Missing reports/all: {totals_rows}"

        # Assert every field is in the allow-list
        fields = {r["field"] for r in totals_rows}
        unknown_fields = fields - ALLOWED_D1_FIELDS
        assert not unknown_fields, f"Unexpected fields in D1 totals: {unknown_fields}"

        # Assert seen holds only report_id and expires columns
        seen_info = run_d1_sql("PRAGMA table_info(seen)")
        seen_cols = {col["name"] for col in seen_info}
        assert seen_cols == {"report_id", "expires"}, f"Unexpected seen schema: {seen_cols}"

        # Assert no value anywhere in totals/seen contains public IP
        seen_rows = run_d1_sql("SELECT report_id, expires FROM seen")
        ip_regex = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
        all_values: list[str] = []
        for r in totals_rows:
            all_values.extend(str(v) for v in r.values())
        for r in seen_rows:
            all_values.extend(str(v) for v in r.values())

        if public_ip:
            for val in all_values:
                assert public_ip not in val, f"Public IP {public_ip} leaked into D1: {val}"
        else:
            for val in all_values:
                assert not ip_regex.search(val), f"IP address format detected in D1: {val}"


@requires_live
def test_eu_region_needs_consent_then_uploads(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    """EU timezone blocks the report until telemetry on persists explicit consent."""
    home, env = clean_home
    env["TZ"] = "Europe/Paris"
    week, marker = seed_telemetry_db(installed_gflow, home, env, with_notice=True, weeks_ago=42)
    command = [str(installed_gflow.gflow), "data", "list", "projects", "--json"]

    with exact_d1_cleanup(week, home):
        first = subprocess.run(command, cwd=str(home), env=env, capture_output=True, text=True)
        assert first.returncode == 0, first.stderr
        time.sleep(20)
        with sqlite3.connect(home / "telemetry.db") as con:
            row = con.execute("SELECT sent_at FROM reports WHERE week = ?", (week,)).fetchone()
            assert row is None or row[0] is None, f"Unexpected upload before consent: {row}"
        no_consent_rows = run_d1_sql(
            f"SELECT count(*) AS count FROM totals WHERE week = '{week}' AND key = 'cli|{marker}'"
        )
        assert no_consent_rows and no_consent_rows[0].get("count") == 0

        on = subprocess.run(
            [str(installed_gflow.gflow), "telemetry", "on"],
            cwd=str(home),
            env=env,
            capture_output=True,
            text=True,
        )
        assert on.returncode == 0, on.stderr
        assert (home / "telemetry.consent").exists()
        with sqlite3.connect(home / "telemetry.db") as con:
            con.execute("DELETE FROM meta WHERE key = 'last_attempt'")
            notice_at_row = con.execute(
                "SELECT value FROM meta WHERE key = 'notice_seen_at'"
            ).fetchone()
            if notice_at_row:
                (home / "telemetry.consent").write_text(notice_at_row[0], encoding="utf-8")

        second = subprocess.run(command, cwd=str(home), env=env, capture_output=True, text=True)
        assert second.returncode == 0, second.stderr
        deadline = time.monotonic() + 60.0
        sent_at: str | None = None
        while time.monotonic() < deadline:
            with sqlite3.connect(home / "telemetry.db") as con:
                row = con.execute("SELECT sent_at FROM reports WHERE week = ?", (week,)).fetchone()
                if row and row[0]:
                    sent_at = row[0]
                    break
            time.sleep(1.0)
        assert sent_at is not None, f"Timed out waiting for consented upload for week {week}"

        deadline_d1 = time.monotonic() + 60.0
        rows: list[dict[str, Any]] = []
        while time.monotonic() < deadline_d1:
            rows = run_d1_sql(f"SELECT field, key, n FROM totals WHERE week = '{week}'")
            if any(
                row.get("field") == "commands" and row.get("key") == f"cli|{marker}" for row in rows
            ):
                break
            time.sleep(1.0)
        marker_row = [
            row
            for row in rows
            if row.get("field") == "commands" and row.get("key") == f"cli|{marker}"
        ]
        assert marker_row and marker_row[0].get("n") == 1, f"Missing consented report: {rows}"


@requires_live
def test_telemetry_off_stops_the_next_upload(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    """'gflow telemetry off' prevents subsequent uploads."""
    home, env = clean_home
    week, marker = seed_telemetry_db(installed_gflow, home, env, with_notice=True, weeks_ago=44)

    with exact_d1_cleanup(week, home):
        off_res = subprocess.run(
            [str(installed_gflow.gflow), "telemetry", "off"],
            cwd=str(home),
            env=env,
            capture_output=True,
            text=True,
        )
        assert off_res.returncode == 0

        run_res = subprocess.run(
            [str(installed_gflow.gflow), "data", "list", "projects", "--json"],
            cwd=str(home),
            env=env,
            capture_output=True,
            text=True,
        )
        assert run_res.returncode == 0

        time.sleep(20)

        with sqlite3.connect(home / "telemetry.db") as con:
            meta = dict(con.execute("SELECT key, value FROM meta"))
            assert "last_attempt" not in meta
            row = con.execute("SELECT sent_at FROM reports WHERE week = ?", (week,)).fetchone()
            assert row is None or row[0] is None, f"Expected sent_at NULL, got {row}"

        d1_rows = run_d1_sql(
            f"SELECT count(*) AS count FROM totals WHERE week = '{week}' AND key = 'cli|{marker}'"
        )
        assert d1_rows and d1_rows[0].get("count") == 0, (
            f"Marker landed in D1 despite opt-out: {d1_rows}"
        )


@requires_live
def test_do_not_track_stops_the_upload(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    """DO_NOT_TRACK=1 stops the upload."""
    home, env = clean_home
    week, marker = seed_telemetry_db(installed_gflow, home, env, with_notice=True, weeks_ago=46)

    with exact_d1_cleanup(week, home):
        env["DO_NOT_TRACK"] = "1"
        run_res = subprocess.run(
            [str(installed_gflow.gflow), "data", "list", "projects", "--json"],
            cwd=str(home),
            env=env,
            capture_output=True,
            text=True,
        )
        assert run_res.returncode == 0

        time.sleep(20)

        with sqlite3.connect(home / "telemetry.db") as con:
            meta = dict(con.execute("SELECT key, value FROM meta"))
            assert "last_attempt" not in meta
            row = con.execute("SELECT sent_at FROM reports WHERE week = ?", (week,)).fetchone()
            assert row is None or row[0] is None, f"Expected sent_at NULL, got {row}"

        d1_rows = run_d1_sql(
            f"SELECT count(*) AS count FROM totals WHERE week = '{week}' AND key = 'cli|{marker}'"
        )
        assert d1_rows and d1_rows[0].get("count") == 0, (
            f"Marker landed in D1 despite DO_NOT_TRACK: {d1_rows}"
        )


def test_410_stops_the_client_for_good(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
    tmp_path: Path,
) -> None:
    """A local stopped Worker disables upload and the client sends no later week."""
    home, env = clean_home
    week1, _ = seed_telemetry_db(installed_gflow, home, env, with_notice=True, weeks_ago=48)
    with stopped_local_worker(tmp_path) as (endpoint, log_path):
        env = _endpoint_test_env(home, env, endpoint)
        res = subprocess.run(
            [str(installed_gflow.gflow), "data", "list", "projects", "--json"],
            cwd=str(home),
            env=env,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0

        deadline = time.monotonic() + 60.0
        disabled: str | None = None
        while time.monotonic() < deadline:
            with sqlite3.connect(home / "telemetry.db") as con:
                meta = dict(con.execute("SELECT key, value FROM meta"))
                if meta.get("upload_disabled") == "1":
                    disabled = "1"
                    break
            time.sleep(1.0)

        assert disabled == "1", f"Timed out waiting for upload_disabled='1' in meta: {meta}"

        with sqlite3.connect(home / "telemetry.db") as con:
            row = con.execute("SELECT sent_at FROM reports WHERE week = ?", (week1,)).fetchone()
            assert row is None or row[0] is None, f"Expected sent_at NULL on 410, got {row}"

        worker_log = log_path.read_text(encoding="utf-8", errors="replace")
        posts_after_410 = len(re.findall(r"POST\s+/v1/report", worker_log))
        assert posts_after_410 >= 1, f"Local Worker received no report POST:\n{worker_log}"

        week2, _ = seed_telemetry_db(installed_gflow, home, env, with_notice=True, weeks_ago=49)
        with sqlite3.connect(home / "telemetry.db") as con:
            con.execute("DELETE FROM meta WHERE key = 'last_attempt'")

        run_res2 = subprocess.run(
            [str(installed_gflow.gflow), "data", "list", "projects", "--json"],
            cwd=str(home),
            env=env,
            capture_output=True,
            text=True,
        )
        assert run_res2.returncode == 0
        time.sleep(3)

        with sqlite3.connect(home / "telemetry.db") as con:
            row2 = con.execute("SELECT sent_at FROM reports WHERE week = ?", (week2,)).fetchone()
            assert row2 is None or row2[0] is None, (
                f"Expected sent_at NULL for second week, got {row2}"
            )
        worker_log = log_path.read_text(encoding="utf-8", errors="replace")
        assert len(re.findall(r"POST\s+/v1/report", worker_log)) == posts_after_410

    # Status command reports server stopped uploads
    status_res = subprocess.run(
        [str(installed_gflow.gflow), "telemetry", "status", "--json"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert status_res.returncode == 0
    status_payload = json.loads(status_res.stdout)
    assert status_payload.get("upload", {}).get("enabled") is False
    assert status_payload.get("upload", {}).get("reason") == "server stopped uploads"


def test_agent_shell_tty_record() -> None:
    """Record sys.stderr.isatty() as seen by a plain subprocess from this agent shell."""
    res = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.stderr.isatty())"],
        capture_output=True,
        text=True,
        check=True,
    )
    val = res.stdout.strip()
    print(f"\n[AGENT_SHELL_ISATTY] sys.stderr.isatty() = {val}")
