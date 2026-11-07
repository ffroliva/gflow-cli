from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing
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
    monkeypatch.setenv("TZ", "America/New_York")
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


@pytest.mark.parametrize(
    ("zone", "expected"),
    [
        ("Europe/Paris", True),
        ("Europe/London", False),
        ("America/New_York", False),
        ("Atlantic/Canary", True),
        ("America/Cayenne", True),
        ("America/Guadeloupe", True),
        ("America/Martinique", True),
        ("America/Marigot", True),
        ("America/St_Barthelemy", True),
        ("Indian/Reunion", True),
        ("Indian/Mayotte", True),
        ("CET", True),
        ("EET", True),
        ("MET", True),
        ("WET", True),
        ("Poland", True),
        ("Portugal", True),
        ("Eire", True),
        ("Iceland", True),
        ("Atlantic/Faeroe", False),
        ("Europe/Belgrade", False),
    ],
)
def test_consent_region_from_iana_timezone(
    zone: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", zone)
    assert telemetry.consent_region() is expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/usr/share/zoneinfo/Europe/Zagreb", "Europe/Zagreb"),
        ("/usr/share/zoneinfo/posix/Europe/Zagreb", "Europe/Zagreb"),
        ("/usr/share/zoneinfo/right/Atlantic/Reykjavik", "Atlantic/Reykjavik"),
    ],
)
def test_posix_timezone_reads_the_link_without_resolving(
    target: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(telemetry.os, "readlink", lambda path: calls.append(path) or target)
    monkeypatch.setattr(Path, "resolve", lambda *_args, **_kwargs: pytest.fail("must not resolve"))
    assert telemetry._posix_timezone_name() == expected
    assert calls == ["/etc/localtime"]


@pytest.mark.parametrize("zone", [None, ""])
def test_unknown_timezone_requires_consent(
    zone: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TZ", raising=False) if zone is None else monkeypatch.setenv("TZ", zone)
    monkeypatch.setattr(telemetry, "_posix_timezone_name", lambda: None)
    monkeypatch.setattr(telemetry, "_read_windows_timezone_key_name", lambda: None)
    assert telemetry.consent_region()


def test_windows_timezone_mapping_requires_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(telemetry, "_posix_timezone_name", lambda: None)
    monkeypatch.setattr(telemetry.sys, "platform", "win32")
    monkeypatch.setattr(
        telemetry, "_read_windows_timezone_key_name", lambda: "Romance Standard Time"
    )
    assert telemetry.consent_region()


def test_windows_timezone_outside_eu_eea_does_not_require_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(telemetry, "_posix_timezone_name", lambda: None)
    monkeypatch.setattr(telemetry.sys, "platform", "win32")
    monkeypatch.setattr(
        telemetry, "_read_windows_timezone_key_name", lambda: "Eastern Standard Time"
    )
    assert not telemetry.consent_region()


def test_timezone_env_precedes_windows_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "America/New_York")
    monkeypatch.setattr(telemetry.sys, "platform", "win32")
    monkeypatch.setattr(
        telemetry, "_read_windows_timezone_key_name", lambda: "Romance Standard Time"
    )
    assert not telemetry.consent_region()


def test_consent_region_blocks_upload_until_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    home = Path.cwd() / "gflow_home"
    _enable_upload(home)
    monkeypatch.setattr(telemetry, "consent_region", lambda: True)
    assert (
        telemetry.upload_disabled_reason()
        == "consent needed in your region: run gflow telemetry on"
    )
    telemetry.set_consent()
    assert telemetry.uploads_enabled()


def test_consent_region_keeps_counting_local_without_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(telemetry, "consent_region", lambda: True)
    telemetry.record("cli", "image t2i", [], "0", 1.0)
    assert any(row.key == "image t2i" for row in telemetry.read_counters())


def test_on_and_off_persist_consent_only_in_consent_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = Path.cwd() / "gflow_home"
    monkeypatch.setattr(telemetry, "consent_region", lambda: True)
    telemetry.set_opted_out(True)
    result = CliRunner().invoke(main, ["telemetry", "on"])
    assert result.exit_code == 0, result.output
    assert "Usage telemetry is on." in result.output
    assert not (home / "telemetry.off").exists()
    assert (home / "telemetry.consent").exists()

    result = CliRunner().invoke(main, ["telemetry", "off"])
    assert result.exit_code == 0, result.output
    assert not (home / "telemetry.consent").exists()


def test_on_does_not_write_consent_outside_consent_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(telemetry, "consent_region", lambda: False)
    result = CliRunner().invoke(main, ["telemetry", "on"])
    assert result.exit_code == 0, result.output
    assert not (Path.cwd() / "gflow_home" / "telemetry.consent").exists()


def test_eu_notice_is_selected_and_mentions_explicit_consent(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(telemetry, "consent_region", lambda: True)
    assert telemetry.maybe_show_notice()
    output = capsys.readouterr()
    assert telemetry.NOTICE_TEXT_CONSENT in output.err
    assert "nothing is sent unless you agree" in output.err


def test_telemetry_status_shows_consent_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    home = Path.cwd() / "gflow_home"
    _enable_upload(home)
    monkeypatch.setattr(telemetry, "consent_region", lambda: True)
    result = CliRunner().invoke(main, ["telemetry", "status"])
    assert result.exit_code == 0, result.output
    assert "Upload: off (consent needed in your region: run gflow telemetry on)" in result.output


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

    monkeypatch.setattr(sys, "argv", ["gflow", "auth", "login"])
    real_notice = telemetry.maybe_show_notice

    def show_notice() -> bool:
        monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
        return real_notice()

    monkeypatch.setattr(cli.telemetry, "maybe_show_notice", show_notice)
    monkeypatch.setattr(cli, "_resolve_or_prompt", lambda **_kwargs: "test-profile")

    async def login(_name: str, *, browser: str) -> Path:
        assert browser == "auto"
        return tmp_path

    monkeypatch.setattr(auth_mod, "login", login)
    monkeypatch.setattr(cli.profile_store, "list_profiles", lambda: [])
    result = CliRunner().invoke(main, ["auth", "login"])
    assert result.exit_code == 0, result.output
    assert result.output.index("Session saved.") < result.output.index(
        telemetry.NOTICE_TEXT.splitlines()[0]
    )


def test_mcp_setup_shows_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from gflow_cli import cli

    monkeypatch.setattr(sys, "argv", ["gflow", "mcp", "setup"])
    real_notice = telemetry.maybe_show_notice

    def show_notice() -> bool:
        monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
        return real_notice()

    monkeypatch.setattr(cli.telemetry, "maybe_show_notice", show_notice)
    monkeypatch.setattr("gflow_cli.mcp.setup.apply", lambda _target: (tmp_path / "config", True))
    result = CliRunner().invoke(main, ["mcp", "setup"])
    assert result.exit_code == 0, result.output
    assert telemetry.NOTICE_TEXT.splitlines()[0] in result.output


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
    for constant in ("OFF_FILE", "CONSENT_FILE"):
        lines = [
            (i, line)
            for i, line in enumerate(source.splitlines(), start=1)
            if constant in line and not line.strip().startswith(f"{constant} =")
        ]
        assert lines
        for line_no, _line in lines:
            owner = next(
                line.strip().split("def ", 1)[1].split("(", 1)[0]
                for line in reversed(source.splitlines()[:line_no])
                if line.lstrip().startswith("def ")
            )
            allowed = {"opted_out", "set_opted_out", "state"}
            if constant == "CONSENT_FILE":
                allowed |= {"set_consent", "upload_disabled_reason"}
            assert owner in allowed


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


def test_off_delete_preserves_server_upload_stop() -> None:
    home = Path.cwd() / "gflow_home"
    with closing(telemetry._connect(home)) as con, con:  # pyright: ignore[reportPrivateUsage]
        con.executemany(
            "INSERT OR REPLACE INTO meta VALUES (?, ?)",
            [
                ("notice_seen_version", str(telemetry.NOTICE_VERSION)),
                ("notice_seen_at", "2026-11-06T12:00:00+00:00"),
                ("upload_disabled", "1"),
            ],
        )
    result = CliRunner().invoke(main, ["telemetry", "off", "--delete"])
    assert result.exit_code == 0, result.output
    with closing(sqlite3.connect(home / telemetry.DB_FILE)) as con:
        assert dict(con.execute("SELECT key, value FROM meta")) == {"upload_disabled": "1"}


def test_on_under_a_kill_switch_persists_says_what_blocks_and_exits_0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "0")
    telemetry.killed.cache_clear()
    result = CliRunner().invoke(main, ["telemetry", "on"])
    assert result.exit_code == 0, result.output
    assert "Usage telemetry is on." in result.output
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
