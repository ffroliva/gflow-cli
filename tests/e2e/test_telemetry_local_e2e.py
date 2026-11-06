"""End-to-end tests for local usage telemetry against an installed wheel (#1164).

Validates that CLI commands, kill switches, crash fingerprints, MCP stdio clients,
and socket boundaries behave correctly against a real installed package in a fresh venv.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_telemetry]

ALLOWED_KEYS = {
    "schema",
    "report_id",
    "week",
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
}


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

    # 1. Build the wheel into <tmp>/dist
    build_res = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(dist_dir)],
        cwd=str(worktree_root),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert build_res.returncode == 0, f"uv build failed:\n{build_res.stderr}"

    wheel_files = list(dist_dir.glob("gflow_cli-*.whl"))
    assert len(wheel_files) == 1, f"Expected exactly 1 wheel in {dist_dir}, found: {wheel_files}"
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

    # Install dependencies by installing the wheel normally
    install_res = subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), str(wheel_path)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert install_res.returncode == 0, f"uv pip install wheel failed:\n{install_res.stderr}"

    # Force our build using --no-index --find-links
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

    # 3. Assert installed package is ours
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
    assert not direct_urls, f"direct_url.json found (expected index-style install): {direct_urls}"

    # 4. Yield installed binaries
    yield InstalledGFlow(gflow=venv_gflow, python=venv_python, venv_dir=venv_dir)


@pytest.fixture
def clean_home(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Isolated home directory and scrubbed environment."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GFLOW_CLI_") and k != "DO_NOT_TRACK"
    }
    env["GFLOW_CLI_HOME"] = str(home)
    env["GFLOW_CLI_UPDATE_CHECK"] = "0"
    env["PYTHONUTF8"] = "1"
    return home, env


def read_db_rows(home: Path) -> list[tuple[str, str, str, str, int]]:
    """Read all rows from <home>/telemetry.db counters table."""
    db_file = home / "telemetry.db"
    if not db_file.exists():
        return []
    with sqlite3.connect(db_file) as con:
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='counters'")
        if not cur.fetchone():
            return []
        return con.execute(
            "SELECT week, surface, kind, key, n FROM counters ORDER BY week, surface, kind, key"
        ).fetchall()


def test_commands_add_rows(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    home, env = clean_home

    # 1. gflow doctor (exit 0 clean, 33 findings present)
    doc_res = subprocess.run(
        [str(installed_gflow.gflow), "doctor"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert doc_res.returncode in (0, 33), f"doctor exited {doc_res.returncode}:\n{doc_res.stderr}"

    # 2. gflow data stats
    stats_res = subprocess.run(
        [str(installed_gflow.gflow), "data", "stats"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert stats_res.returncode == 0, f"data stats failed:\n{stats_res.stderr}"

    # 3. gflow telemetry show
    show_res = subprocess.run(
        [str(installed_gflow.gflow), "telemetry", "show"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert show_res.returncode == 0, f"telemetry show failed:\n{show_res.stderr}"

    # Assert counters
    rows = read_db_rows(home)
    commands = {r[3] for r in rows if r[1] == "cli" and r[2] == "command"}
    assert {"doctor", "data stats", "telemetry show"}.issubset(commands), (
        f"Missing commands in {commands}"
    )

    exit_keys = {r[3] for r in rows if r[1] == "cli" and r[2] == "exit"}
    assert any(k.startswith("doctor ") for k in exit_keys), f"doctor exit missing in {exit_keys}"
    assert any(k.startswith("data stats ") for k in exit_keys), (
        f"data stats exit missing in {exit_keys}"
    )
    assert any(k.startswith("telemetry show ") for k in exit_keys), (
        f"telemetry show exit missing in {exit_keys}"
    )

    # Assert telemetry show JSON schema
    report = json.loads(show_res.stdout)
    assert set(report.keys()) == ALLOWED_KEYS


def test_do_not_track_adds_nothing(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    home, env = clean_home

    # Run once without kill switch to ensure counters work
    subprocess.run(
        [str(installed_gflow.gflow), "telemetry", "show"],
        cwd=str(home),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    before_rows = read_db_rows(home)
    assert before_rows, "Expected counters before DO_NOT_TRACK"

    # Set DO_NOT_TRACK=1
    env["DO_NOT_TRACK"] = "1"
    subprocess.run(
        [str(installed_gflow.gflow), "doctor"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(installed_gflow.gflow), "data", "stats"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(installed_gflow.gflow), "telemetry", "show"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )

    after_rows = read_db_rows(home)
    assert after_rows == before_rows


def test_gflow_cli_telemetry_0_adds_nothing(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    home, env = clean_home

    # Run once without kill switch
    subprocess.run(
        [str(installed_gflow.gflow), "telemetry", "show"],
        cwd=str(home),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    before_rows = read_db_rows(home)
    assert before_rows, "Expected counters before GFLOW_CLI_TELEMETRY=0"

    # Set GFLOW_CLI_TELEMETRY=0
    env["GFLOW_CLI_TELEMETRY"] = "0"
    subprocess.run(
        [str(installed_gflow.gflow), "doctor"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(installed_gflow.gflow), "data", "stats"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [str(installed_gflow.gflow), "telemetry", "show"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )

    after_rows = read_db_rows(home)
    assert after_rows == before_rows


def test_forced_crash_records_fingerprint_without_message_or_path(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    home, env = clean_home

    code = (
        "import gflow_cli.cli\n"
        "import gflow_cli.cli_data\n"
        "\n"
        "def _crash(*args, **kwargs):\n"
        "    raise RuntimeError(r'secret prompt C:\\Users\\jane\\x.png')\n"
        "\n"
        "gflow_cli.cli_data.collect_usage_stats = _crash\n"
        "gflow_cli.cli.main(['data', 'stats'], standalone_mode=True)\n"
    )
    proc = subprocess.run(
        [str(installed_gflow.python), "-c", code],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, f"Expected exit 1, got {proc.returncode}:\n{proc.stderr}"

    rows = read_db_rows(home)
    crash_rows = [r for r in rows if r[2] == "crash"]
    assert crash_rows, f"Expected a crash row in counters, found none. Rows: {rows}"
    crash_key = crash_rows[0][3]
    assert crash_key.startswith("RuntimeError"), (
        f"Crash key does not start with RuntimeError: {crash_key}"
    )
    assert "cli_data" in crash_key or "stats" in crash_key

    for token in ("secret", "jane", "\\", ".png"):
        assert token not in crash_key, f"Token {token} leaked into crash key: {crash_key}"
    assert not any(c.isdigit() for c in crash_key), f"Digits found in crash key: {crash_key}"

    # Ruling: incident bundles are only written by the FlowApiClient diagnostics path,
    # which no zero-cost command reaches; the bundle fingerprint is already proven by
    # test_incident_bundle_holds_the_same_fingerprint in tests/test_telemetry_cli.py.
    # If any incident bundle exists under <home>, its manifest fingerprint equals the row's key.
    incidents_root = home / "incidents"
    manifests = list(incidents_root.glob("*/*/manifest.json"))
    for manifest_path in manifests:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest_data.get("fingerprint") == crash_key


@pytest.mark.asyncio
async def test_mcp_stdio_client_records_tool_and_client(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    home, env = clean_home
    params = StdioServerParameters(
        command=str(installed_gflow.gflow),
        args=["mcp", "run"],
        env=env,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init_res = await session.initialize()
            assert init_res.server_info is not None
            result = await session.call_tool("gflow_usage_stats", {"weeks": 4})
            assert not result.is_error
            assert result.content
            raw_text = getattr(result.content[0], "text", None)
            assert raw_text is not None, "Tool response missing text content"
            payload = json.loads(raw_text)
            assert "telemetry" in payload
            assert payload["telemetry"].get("state") == "on"
            assert "report" in payload["telemetry"]

    rows = read_db_rows(home)
    mcp_rows = [r for r in rows if r[1] == "mcp"]
    command_keys = {r[3] for r in mcp_rows if r[2] == "command"}
    client_keys = {r[3] for r in mcp_rows if r[2] == "client"}
    assert "gflow_usage_stats" in command_keys, f"gflow_usage_stats not in {command_keys}"
    assert "mcp" in client_keys, f"mcp not in {client_keys}"


@pytest.mark.asyncio
async def test_no_network_socket_is_opened(
    installed_gflow: InstalledGFlow,
    clean_home: tuple[Path, dict[str, str]],
    tmp_path: Path,
) -> None:
    """Assert no outbound network socket is opened during telemetry paths.

    The MCP stdio transport uses pipes, not sockets; asyncio on Windows may create
    a local socketpair (127.0.0.1) for its loop — allow loopback addresses only.
    """
    home, env = clean_home
    socket_log = tmp_path / "socket_attempts.log"
    site_dir = tmp_path / "site_dir"
    site_dir.mkdir(parents=True, exist_ok=True)
    sitecustomize = site_dir / "sitecustomize.py"
    sitecustomize.write_text(
        f"""\
import socket
import os

_orig_connect = socket.socket.connect
_orig_create_connection = socket.create_connection

LOG_FILE = {repr(str(socket_log))}

def _is_loopback(address):
    if isinstance(address, tuple) and len(address) >= 1:
        host = address[0]
        if host in ("127.0.0.1", "::1", "localhost"):
            return True
    return False

def _guard(target):
    if _is_loopback(target):
        return
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(str(target) + "\\n")
    raise RuntimeError(f"Network connection blocked: {{target}}")

def patched_connect(self, address):
    _guard(address)
    return _orig_connect(self, address)

def patched_create_connection(address, *args, **kwargs):
    _guard(address)
    return _orig_create_connection(address, *args, **kwargs)

socket.socket.connect = patched_connect
socket.create_connection = patched_create_connection
""",
        encoding="utf-8",
    )

    env["PYTHONPATH"] = str(site_dir)

    # 1. gflow doctor
    res_doc = subprocess.run(
        [str(installed_gflow.gflow), "doctor"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_doc.returncode in (0, 33)

    # 2. gflow data stats
    res_stats = subprocess.run(
        [str(installed_gflow.gflow), "data", "stats"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_stats.returncode == 0

    # 3. gflow telemetry show
    res_show = subprocess.run(
        [str(installed_gflow.gflow), "telemetry", "show"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_show.returncode == 0

    # 4. MCP call
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=str(installed_gflow.gflow),
        args=["mcp", "run"],
        env=env,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("gflow_usage_stats", {"weeks": 4})
            assert not result.is_error

    if socket_log.exists():
        attempts = socket_log.read_text(encoding="utf-8").strip()
        assert not attempts, f"Unexpected network socket connection attempts:\n{attempts}"
