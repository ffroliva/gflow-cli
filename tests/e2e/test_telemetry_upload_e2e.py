"""End-to-end tests for usage telemetry notice, opt-out, and weekly upload (#1164).

Validates first-run notice in a real PTY, pipe suppression, opt-out mechanisms,
and weekly upload lifecycle against an installed wheel and the live Cloudflare endpoint.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_telemetry]

ENDPOINT = "https://gflow-telemetry.ffroliva.workers.dev/v1/report"
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
_RUN_START_UNIX = int(time.time())

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


def empty_post(timeout_s: float = 5.0) -> int:
    """Send empty POST to endpoint to check status (400 vs 410)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        ENDPOINT,
        data=b"",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "gflow-cli-e2e/0.68.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            return int(resp.status)
    except urllib.error.HTTPError as err:
        return int(err.code)


def wait_for_empty_post_status(expected: int, timeout_s: float = 60.0) -> None:
    """Poll endpoint with empty POST until status matches expected."""
    deadline = time.monotonic() + timeout_s
    last_status: int | None = None
    while time.monotonic() < deadline:
        try:
            status = empty_post(timeout_s=5.0)
            last_status = status
            if status == expected:
                return
        except Exception:
            pass
        time.sleep(1.0)
    raise TimeoutError(
        f"Endpoint did not return {expected} within {timeout_s}s (last: {last_status})"
    )


def run_wrangler_deploy(var_stopped: str | None = None) -> None:
    """Deploy Worker optionally with --var STOPPED:<val>."""
    env = dict(os.environ)
    env["CLOUDFLARE_ACCOUNT_ID"] = CLOUDFLARE_ACCOUNT_ID
    cmd = ["npx", "wrangler", "deploy"]
    if var_stopped is not None:
        cmd.extend(["--var", f"STOPPED:{var_stopped}"])
    res = subprocess.run(
        cmd,
        cwd=str(WRANGLER_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        shell=sys.platform == "win32",
    )
    assert res.returncode == 0, f"wrangler deploy failed:\n{res.stderr}\n{res.stdout}"


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
    weeks_ago: int = 3,
    probe_id: str | None = None,
) -> tuple[str, str]:
    """Seed <home>/telemetry.db via the installed interpreter."""
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

# 1. Create schema
with telemetry._connect(home) as con:
    pass

# 2. Seed tables directly
with sqlite3.connect(home / 'telemetry.db') as con:
    if {with_notice}:
        con.execute(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)",
            ('notice_seen_version', '1')
        )
        con.execute(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)",
            ('notice_seen_at', (now - timedelta(days=70)).isoformat())
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
    return week, marker


@pytest.fixture(scope="module", autouse=True)
def d1_cleanup() -> Iterator[None]:
    """Module-level cleanup: delete test rows from totals and seen."""
    yield
    if not is_live_enabled():
        return
    try:
        if _USED_WEEKS:
            weeks_list = ", ".join(f"'{w}'" for w in sorted(_USED_WEEKS))
            run_d1_sql(f"DELETE FROM totals WHERE week IN ({weeks_list})")
        # DELETE FROM seen WHERE expires > <run start + 13 days> is acceptable
        # since no other client exists yet
        cutoff = _RUN_START_UNIX + 13 * 86400
        run_d1_sql(f"DELETE FROM seen WHERE expires > {cutoff}")

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
        assert meta.get("notice_seen_version") == "1"
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
            r.get("field") == "commands" and r.get("key") == f"cli|{marker}" for r in totals_rows
        )
        has_report = any(r.get("field") == "reports" and r.get("key") == "all" for r in totals_rows)
        if has_marker and has_report:
            break
        time.sleep(1.0)

    # Assert D1 totals
    marker_row = [
        r for r in totals_rows if r.get("field") == "commands" and r.get("key") == f"cli|{marker}"
    ]
    report_row = [r for r in totals_rows if r.get("field") == "reports" and r.get("key") == "all"]
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
def test_telemetry_off_stops_the_next_upload(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    """'gflow telemetry off' prevents subsequent uploads."""
    home, env = clean_home
    week, marker = seed_telemetry_db(installed_gflow, home, env, with_notice=True)

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
    week, marker = seed_telemetry_db(installed_gflow, home, env, with_notice=True)

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


@requires_live
def test_410_stops_the_client_for_good(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    """HTTP 410 permanently disables upload locally; subsequent weeks are never sent."""
    home, env = clean_home
    week1, marker1 = seed_telemetry_db(installed_gflow, home, env, with_notice=True, weeks_ago=3)

    try:
        # 1. Deploy STOPPED:1 and wait until empty POST answers 410
        run_wrangler_deploy(var_stopped="1")
        wait_for_empty_post_status(expected=410, timeout_s=60.0)

        # 2. Run command and poll for upload_disabled='1' (max 60 s)
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

    finally:
        # 3. Restore deploy in finally block and confirm 400
        run_wrangler_deploy()
        wait_for_empty_post_status(expected=400, timeout_s=60.0)

    # 4. Add a SECOND eligible week marker, clear last_attempt
    week2, marker2 = seed_telemetry_db(installed_gflow, home, env, with_notice=True, weeks_ago=4)
    with sqlite3.connect(home / "telemetry.db") as con:
        con.execute("DELETE FROM meta WHERE key = 'last_attempt'")

    # Run command again
    run_res2 = subprocess.run(
        [str(installed_gflow.gflow), "data", "list", "projects", "--json"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert run_res2.returncode == 0

    time.sleep(20)

    # Assert no POST
    with sqlite3.connect(home / "telemetry.db") as con:
        row2 = con.execute("SELECT sent_at FROM reports WHERE week = ?", (week2,)).fetchone()
        assert row2 is None or row2[0] is None, f"Expected sent_at NULL for second week, got {row2}"

    d1_rows2 = run_d1_sql(
        f"SELECT count(*) AS count FROM totals WHERE week = '{week2}' AND key = 'cli|{marker2}'"
    )
    assert d1_rows2 and d1_rows2[0].get("count") == 0, (
        f"Marker 2 landed in D1 despite 410: {d1_rows2}"
    )

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
