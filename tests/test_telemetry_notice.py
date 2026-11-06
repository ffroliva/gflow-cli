from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from gflow_cli import telemetry, update_check
from gflow_cli.cli import main


@pytest.fixture(autouse=True)
def _tele_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.delenv("GFLOW_CLI_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    home = tmp_path / "gflow_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(update_check, "_in_ci", lambda: False)
    monkeypatch.setattr(update_check, "_installed_from_index", lambda: True)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    telemetry.killed.cache_clear()
    telemetry._NOTICE_SHOWN_THIS_RUN = False
    yield home
    telemetry.killed.cache_clear()
    telemetry._NOTICE_SHOWN_THIS_RUN = False


def _meta(home: Path, key: str, value: str) -> None:
    with sqlite3.connect(home / "telemetry.db") as con:
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))


def _read_meta(home: Path) -> dict[str, str]:
    with sqlite3.connect(home / "telemetry.db") as con:
        return dict(con.execute("SELECT key, value FROM meta"))


def _enable_upload(home: Path) -> None:
    _meta(home, "notice_seen_version", str(telemetry.NOTICE_VERSION))
    _meta(home, "notice_seen_at", "2026-11-06T12:00:00+00:00")


def test_notice_not_seen_means_no_upload(tmp_path: Path) -> None:
    assert not telemetry.uploads_enabled()


def test_notice_seen_means_upload_is_enabled_by_default(tmp_path: Path) -> None:
    _enable_upload(tmp_path / "gflow_home")
    assert telemetry.uploads_enabled()


def test_nothing_is_sent_on_the_run_that_shows_the_notice(tmp_path: Path) -> None:
    assert telemetry.maybe_show_notice()
    assert telemetry._NOTICE_SHOWN_THIS_RUN


def test_notice_goes_to_stderr_never_stdout(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    assert telemetry.maybe_show_notice()
    output = capsys.readouterr()
    assert output.out == ""
    assert telemetry.NOTICE_TEXT in output.err


def test_notice_shows_once_per_version() -> None:
    assert telemetry.maybe_show_notice()
    assert not telemetry.maybe_show_notice()
    assert _read_meta(Path.cwd() / "gflow_home")["notice_seen_version"] == str(
        telemetry.NOTICE_VERSION
    )


def test_new_notice_version_stops_upload_until_shown(tmp_path: Path) -> None:
    home = tmp_path / "gflow_home"
    _meta(home, "notice_seen_version", str(telemetry.NOTICE_VERSION - 1))
    _meta(home, "notice_seen_at", "2026-11-01T00:00:00+00:00")
    assert not telemetry.uploads_enabled()
    assert telemetry.maybe_show_notice()
    assert telemetry.uploads_enabled()


@pytest.mark.parametrize(
    ("condition", "reason"),
    [
        ("ci", "CI"),
        ("not_index", "not installed from a package index"),
        ("server_disabled", "server stopped uploads"),
        ("killed", "killed: GFLOW_CLI_TELEMETRY"),
    ],
)
def test_upload_disabled_reason(
    condition: str, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = Path.cwd() / "gflow_home"
    _enable_upload(home)
    if condition == "ci":
        monkeypatch.setattr(update_check, "_in_ci", lambda: True)
    elif condition == "not_index":
        monkeypatch.setattr(update_check, "_installed_from_index", lambda: False)
    elif condition == "server_disabled":
        _meta(home, "upload_disabled", "1")
    else:
        monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "0")
        telemetry.killed.cache_clear()
    assert telemetry.upload_disabled_reason() == reason
    assert not telemetry.uploads_enabled()


def test_auth_login_shows_it_after_session_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli import auth as auth_mod
    from gflow_cli import cli

    notices = iter([False, True])

    def show_notice() -> bool:
        shown = next(notices)
        if shown:
            click.echo("NOTICE", err=True)
        return shown

    monkeypatch.setattr(cli.telemetry, "maybe_show_notice", show_notice)
    monkeypatch.setattr(cli, "_resolve_or_prompt", lambda **_kwargs: "test-profile")

    async def login(_name: str, *, browser: str) -> Path:
        assert browser == "auto"
        return tmp_path

    monkeypatch.setattr(auth_mod, "login", login)
    monkeypatch.setattr(cli.profile_store, "list_profiles", lambda: [])
    result = CliRunner().invoke(main, ["auth", "login"])
    assert result.exit_code == 0, result.output
    assert result.output.index("Session saved.") < result.output.index("NOTICE")


def test_mcp_setup_shows_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from gflow_cli import cli

    notices = iter([False, True])

    def show_notice() -> bool:
        shown = next(notices)
        if shown:
            click.echo("NOTICE", err=True)
        return shown

    monkeypatch.setattr(cli.telemetry, "maybe_show_notice", show_notice)
    monkeypatch.setattr("gflow_cli.mcp.setup.apply", lambda _target: (tmp_path / "config", True))
    result = CliRunner().invoke(main, ["mcp", "setup"])
    assert result.exit_code == 0, result.output
    assert "NOTICE" in result.output


@pytest.mark.parametrize(
    "case", ["ci", "stderr_piped", "killed", "opted_out", "already_seen", "telemetry off"]
)
def test_notice_is_not_shown(case: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "gflow_home"
    if case == "ci":
        monkeypatch.setattr(update_check, "_in_ci", lambda: True)
    elif case == "stderr_piped":
        monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    elif case == "killed":
        monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "0")
        telemetry.killed.cache_clear()
    elif case == "opted_out":
        telemetry.set_opted_out(True)
    elif case == "already_seen":
        _meta(home, "notice_seen_version", str(telemetry.NOTICE_VERSION))
    else:
        result = CliRunner().invoke(main, ["telemetry", "status"])
        assert result.exit_code == 0, result.output
        assert not telemetry._NOTICE_SHOWN_THIS_RUN
        return
    assert not telemetry.maybe_show_notice()


def test_off_stops_counting_and_upload_and_persists(tmp_path: Path) -> None:
    home = tmp_path / "gflow_home"
    _enable_upload(home)
    assert telemetry.uploads_enabled()
    result = CliRunner().invoke(main, ["telemetry", "off"])
    assert result.exit_code == 0, result.output
    assert (
        "Usage telemetry is off. Nothing will be counted or sent from this machine."
        in result.output
    )
    assert (home / "telemetry.off").exists()
    assert not telemetry.uploads_enabled()
    telemetry.record("cli", "video t2v", [], "0", 1.0)
    assert telemetry.read_counters() == []


def test_off_works_without_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    result = CliRunner().invoke(main, ["telemetry", "off"])
    assert result.exit_code == 0, result.output
    assert "Usage telemetry is off." in result.output


def test_off_that_cannot_write_exits_11_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("file")
    monkeypatch.setenv("GFLOW_CLI_HOME", str(blocker / "home"))
    result = CliRunner().invoke(main, ["telemetry", "off"])
    assert result.exit_code == 11
    assert str(blocker / "home" / "telemetry.off") in result.output


def test_off_delete_that_cannot_delete_exits_11_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "telemetry.db").write_bytes(b"x")
    monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
    real_unlink = Path.unlink

    def locked(self: Path, missing_ok: bool = False) -> None:
        if self.name == "telemetry.db":
            raise PermissionError("locked")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)
    result = CliRunner().invoke(main, ["telemetry", "off", "--delete"])
    assert result.exit_code == 11
    assert str(home / "telemetry.db") in result.output
    assert "Traceback" not in result.output
    assert (home / "telemetry.off").exists()  # the opt-out itself still persisted


def test_no_writer_can_undo_off() -> None:
    source = Path(telemetry.__file__).read_text(encoding="utf-8")
    lines = [
        (i, line) for i, line in enumerate(source.splitlines(), start=1) if "telemetry.off" in line
    ]
    assert lines
    for line_no, _line in lines:
        preceding = "\n".join(source.splitlines()[:line_no])
        assert (
            "def opted_out" in preceding[-300:]
            or "def set_opted_out" in preceding[-300:]
            or (
                'return "off", "telemetry.off"' == _line.strip() and "def state" in preceding[-300:]
            )
        )


def test_off_delete_wipes_local_data_and_says_remote_holds_nothing_per_install() -> None:
    home = Path.cwd() / "gflow_home"
    for suffix in ("", "-wal", "-shm"):
        (home / f"telemetry.db{suffix}").write_text("local data")
    result = CliRunner().invoke(main, ["telemetry", "off", "--delete"])
    assert result.exit_code == 0, result.output
    assert (home / "telemetry.off").exists()
    assert not list(home.glob("telemetry.db*"))
    assert (
        "Local usage data deleted. The server holds no per-install data, so there is nothing to "
        "delete remotely." in result.output
    )


def test_on_under_a_kill_switch_persists_says_what_blocks_and_exits_0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "0")
    telemetry.killed.cache_clear()
    result = CliRunner().invoke(main, ["telemetry", "on"])
    assert result.exit_code == 0, result.output
    assert "Telemetry is still off because GFLOW_CLI_TELEMETRY is set." in result.output
    assert (
        "Weekly upload starts only after the usage notice has been shown in an interactive "
        "terminal." in result.output
    )
    assert not (Path.cwd() / "gflow_home" / "telemetry.off").exists()


def test_corrupt_db_means_no_upload(tmp_path: Path) -> None:
    db = tmp_path / "gflow_home" / "telemetry.db"
    db.write_bytes(b"not a database" * 100)
    assert not telemetry.uploads_enabled()
