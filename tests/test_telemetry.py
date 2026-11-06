"""Local usage-counter store and kill switches (#1164). No network anywhere."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli import telemetry


@pytest.fixture(autouse=True)
def _tele_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """conftest turns telemetry off; these tests exercise it, so undo that."""
    monkeypatch.delenv("GFLOW_CLI_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    home = tmp_path / "gflow_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    telemetry.killed.cache_clear()
    yield home
    telemetry.killed.cache_clear()


@pytest.fixture
def tmp_home(_tele_env: Path) -> Path:
    return _tele_env


def _rows(home: Path) -> dict[tuple[str, str], int]:
    con = sqlite3.connect(home / "telemetry.db")
    try:
        return {(k, key): n for k, key, n in con.execute("SELECT kind, key, n FROM counters")}
    finally:
        con.close()


def _meta(home: Path) -> dict[str, str]:
    con = sqlite3.connect(home / "telemetry.db")
    try:
        return dict(con.execute("SELECT key, value FROM meta"))
    finally:
        con.close()


def _sql(home: Path, sql: str, args: tuple[object, ...] = ()) -> None:
    con = sqlite3.connect(home / "telemetry.db")
    con.execute(sql, args)
    con.commit()
    con.close()


def test_record_counts_command_flag_exit_duration(tmp_home: Path) -> None:
    for _ in range(2):
        telemetry.record("cli", "video t2v", ["--ref"], "0", 12.0)
    rows = _rows(tmp_home)
    assert rows[("command", "video t2v")] == 2
    assert rows[("flag", "video t2v --ref")] == 2
    assert rows[("exit", "video t2v 0")] == 2
    assert rows[("duration", "video t2v <1m")] == 2
    counters = telemetry.read_counters()
    assert counters
    assert all(c.surface == "cli" and c.week == telemetry.iso_week() for c in counters)


@pytest.mark.parametrize(
    ("seconds", "bucket"), [(9.9, "<10s"), (10, "<1m"), (299, "<5m"), (300, "5m+")]
)
def test_duration_buckets(tmp_home: Path, seconds: float, bucket: str) -> None:
    telemetry.record("cli", "x", [], "0", seconds)
    assert _rows(tmp_home)[("duration", f"x {bucket}")] == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", "DO_NOT_TRACK"),
        ("true", "DO_NOT_TRACK"),
        ("TRUE", "DO_NOT_TRACK"),
        ("0", None),
        ("false", None),
        ("", None),
    ],
)
def test_do_not_track(value: str, expected: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DO_NOT_TRACK", value)
    assert telemetry.killed() == expected


@pytest.mark.parametrize("value", ["0", "false", "off", "maybe", "OFF"])
def test_gflow_cli_telemetry_turns_off(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", value)
    assert telemetry.killed() == "GFLOW_CLI_TELEMETRY"


@pytest.mark.parametrize("value", ["1", "true", "on", "ON", ""])
def test_gflow_cli_telemetry_on_values(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", value)
    assert telemetry.killed() is None


def test_gflow_cli_telemetry_preferred_when_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "0")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    assert telemetry.killed() == "GFLOW_CLI_TELEMETRY"


def test_a_bad_value_never_fails_a_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.cli import main

    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "maybe")
    assert CliRunner().invoke(main, ["--help"]).exit_code == 0


def test_either_dotenv_can_turn_off_and_neither_can_turn_on(
    tmp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_home / ".env").write_text("DO_NOT_TRACK=1\n")
    assert telemetry.killed() == "DO_NOT_TRACK"
    (tmp_home / ".env").unlink()
    Path(".env").write_text("GFLOW_CLI_TELEMETRY=off\n")
    telemetry.killed.cache_clear()
    assert telemetry.killed() == "GFLOW_CLI_TELEMETRY"
    # an "on" value in a dotenv cannot override an off switch in the environment
    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "0")
    Path(".env").write_text("GFLOW_CLI_TELEMETRY=1\n")
    telemetry.killed.cache_clear()
    assert telemetry.killed() == "GFLOW_CLI_TELEMETRY"


def test_killed_records_nothing(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    telemetry.record("cli", "x", [], "0", 1.0)
    assert not (tmp_home / "telemetry.db").exists()


def test_opted_out_records_nothing(tmp_home: Path) -> None:
    (tmp_home / "telemetry.off").write_text("")
    assert telemetry.opted_out()
    telemetry.record("cli", "x", [], "0", 1.0)
    assert not (tmp_home / "telemetry.db").exists()
    (tmp_home / "telemetry.off").unlink()  # not cached: a running server notices
    assert not telemetry.opted_out()


def test_corrupt_db_is_silent(tmp_home: Path) -> None:
    (tmp_home / "telemetry.db").write_bytes(b"not a database" * 100)
    assert telemetry.record("cli", "x", [], "0", 1.0) is None


def test_read_only_home_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A regular file where the home should be: the db can never be created.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("GFLOW_CLI_HOME", str(blocker / "home"))
    assert telemetry.record("cli", "x", [], "0", 1.0) is None


_WORKER = (
    "from gflow_cli import telemetry\n"
    "for i in range(50):\n"
    "    telemetry.record('cli', 'video t2v', ['--ref'], '0', 1.0)\n"
)


def test_contention_is_lossy_and_silent(tmp_home: Path) -> None:
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(4)
    ]
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0
        assert out == b""
        assert err == b""
    assert 0 < _rows(tmp_home)[("command", "video t2v")] <= 200


def test_rows_older_than_53_weeks_are_pruned(tmp_home: Path) -> None:
    telemetry.record("cli", "x", [], "0", 1.0)
    old = telemetry.iso_week(datetime.now(UTC) - timedelta(weeks=60))
    _sql(tmp_home, "INSERT INTO counters VALUES (?, 'cli', 'command', 'old', 1)", (old,))
    telemetry.record("cli", "x", [], "0", 1.0)
    assert ("command", "old") not in _rows(tmp_home)
    assert _rows(tmp_home)[("command", "x")] == 2


def test_first_seen_is_set_once(tmp_home: Path) -> None:
    telemetry.record("cli", "x", [], "0", 1.0)
    assert _meta(tmp_home)["first_seen"] != "2020-01-01"
    _sql(tmp_home, "UPDATE meta SET value='2020-01-01' WHERE key='first_seen'")
    telemetry.record("cli", "x", [], "0", 1.0)
    assert _meta(tmp_home)["first_seen"] == "2020-01-01"


def test_transition_within_30_minutes_counts_a_pair(tmp_home: Path) -> None:
    telemetry.record("cli", "image t2i", [], "0", 1.0)
    telemetry.record("cli", "video i2v", [], "0", 1.0)
    assert _rows(tmp_home)[("transition", "image t2i -> video i2v")] == 1


def test_transition_after_30_minutes_counts_nothing(tmp_home: Path) -> None:
    telemetry.record("cli", "image t2i", [], "0", 1.0)
    stale = (datetime.now(UTC) - timedelta(minutes=31)).timestamp()
    _sql(tmp_home, "UPDATE meta SET value=? WHERE key='prev_time'", (str(stale),))
    telemetry.record("cli", "video i2v", [], "0", 1.0)
    assert not [k for k in _rows(tmp_home) if k[0] == "transition"]


def test_no_connection_outlives_record(tmp_home: Path) -> None:
    telemetry.record("cli", "x", [], "0", 1.0)
    # An open connection would hold the file on Windows; deletion proves none is left.
    for f in tmp_home.glob("telemetry.db*"):
        f.unlink()
