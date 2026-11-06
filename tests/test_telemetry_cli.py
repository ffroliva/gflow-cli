from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import FunctionType

import click
import pytest
from click.testing import CliRunner

from gflow_cli import telemetry
from gflow_cli.cli import main


@pytest.fixture(autouse=True)
def _tele_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.delenv("GFLOW_CLI_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    home = tmp_path / "gflow_home"
    home.mkdir()
    monkeypatch.setenv("GFLOW_CLI_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    telemetry.killed.cache_clear()
    yield home
    telemetry.killed.cache_clear()


def _rows(home: Path) -> list[tuple[str, str, str, str, int]]:
    with sqlite3.connect(home / "telemetry.db") as con:
        return list(con.execute("SELECT week, surface, kind, key, n FROM counters"))


def _route_one() -> None:
    _route_two()


def _route_two() -> None:
    _route_three()


def _route_three() -> None:
    _route_four()


def _route_four() -> None:
    raise TimeoutError("secret prompt C:\\Users\\jane")


def _no_project_frame() -> None:
    raise ValueError("secret prompt C:\\Users\\jane")


def test_leaf_walks_groups() -> None:
    assert telemetry.leaf_and_flags(main, ["video", "t2v", "a cat", "--duration", "8"]) == (
        "video t2v",
        ["--duration"],
    )


def test_prompt_shaped_like_flag_is_not_a_key() -> None:
    assert telemetry.leaf_and_flags(main, ["video", "t2v", "--nonsense", "image t2i"]) == (
        "video t2v",
        [],
    )


def test_flag_with_equals_keeps_name_only() -> None:
    assert telemetry.leaf_and_flags(main, ["video", "t2v", "--duration=8", "x"])[1] == [
        "--duration"
    ]


def test_short_flag_normalises_to_long() -> None:
    _, flags = telemetry.leaf_and_flags(main, ["video", "t2v", "-t", "tool", "cat"])
    assert flags == ["--tool"]


def test_unknown_command_is_group_name() -> None:
    assert telemetry.leaf_and_flags(main, ["nonsense"])[0] == "gflow"


def test_help_records_nothing(_tele_env: Path) -> None:
    CliRunner().invoke(main, ["video", "t2v", "--help"])
    assert not (_tele_env / "telemetry.db").exists()


def test_runner_args_are_used_not_sys_argv(_tele_env: Path) -> None:
    CliRunner().invoke(main, ["data", "list", "projects", "--json"])
    assert any(r[2] == "command" and r[3] == "data list projects" for r in _rows(_tele_env))


@pytest.mark.parametrize(
    ("exception", "outcome"),
    [
        (SystemExit(11), "11"),
        (SystemExit("bad"), "1"),
        (KeyboardInterrupt(), "130"),
        (click.Abort(), "1"),
    ],
)
def test_invocation_outcomes_are_counted(
    _tele_env: Path, exception: BaseException, outcome: str
) -> None:
    @click.group(cls=telemetry.CountingGroup, name="probe")
    def probe() -> None:
        pass

    @probe.command()
    def fail() -> None:
        raise exception

    CliRunner().invoke(probe, ["fail"])
    assert any(row[2:4] == ("exit", f"fail {outcome}") for row in _rows(_tele_env))


def test_json_stdout_identical_with_telemetry_on_and_killed(
    _tele_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    enabled = runner.invoke(main, ["data", "list", "projects", "--json"], mix_stderr=False)
    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "0")
    telemetry.killed.cache_clear()
    killed = runner.invoke(main, ["data", "list", "projects", "--json"], mix_stderr=False)
    assert enabled.stdout.encode() == killed.stdout.encode()


def test_crash_key_is_class_and_innermost_three_gflow_frames() -> None:
    originals = (_route_one, _route_two, _route_three, _route_four)
    functions = {f.__name__: FunctionType(f.__code__, {}) for f in originals}
    globals_map = {
        "__name__": "gflow_cli.test_routes",
        **functions,
        "TimeoutError": TimeoutError,
    }
    for function in functions.values():
        function.__globals__.update(globals_map)
    try:
        functions["_route_one"]()
    except TimeoutError as exc:
        key = telemetry.crash_key(exc)
    assert key.startswith(
        "TimeoutError <- test_routes:_route_four <- test_routes:_route_three "
        "<- test_routes:_route_two"
    )
    assert "secret" not in key and "C:\\Users" not in key


def test_crash_key_never_holds_message_path_or_lineno() -> None:
    failure = FunctionType(_no_project_frame.__code__, {"__name__": "<string>"})
    try:
        failure()
    except ValueError as exc:
        key = telemetry.crash_key(exc)
    assert key == "ValueError at other"


def test_crash_key_is_capped_at_200_chars() -> None:
    try:
        raise RuntimeError
    except RuntimeError as exc:
        key = telemetry.crash_key(exc)
    assert len(key) <= 200


def test_crash_key_names_cause_class() -> None:
    try:
        try:
            raise OSError("secret")
        except OSError as cause:
            raise ValueError("also secret") from cause
    except ValueError as exc:
        key = telemetry.crash_key(exc)
    assert key == "ValueError from OSError at other"


def test_incident_bundle_holds_the_same_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli import observability

    events: list[dict[str, object]] = []

    class Logger:
        def error(self, event: str, **kwargs: object) -> None:
            events.append({"event": event, **kwargs})

    try:
        raise RuntimeError("secret")
    except RuntimeError as exc:
        fingerprint = telemetry.crash_key(exc)
        monkeypatch.setattr(
            observability, "_incident_manifest", lambda *a, **k: None, raising=False
        )
        observability.emit_unhandled_event(Logger(), exc, cli_command="x")  # type: ignore[arg-type]
    assert events and events[0].get("fingerprint") == fingerprint


def test_handled_crash_is_counted_on_both_arms(_tele_env: Path) -> None:
    from gflow_cli._cli_helpers import run_with_handlers

    for as_json in (False, True):
        with pytest.raises(SystemExit):
            run_with_handlers(
                lambda: _raise_runtime_error(), cli_command="video t2v", as_json=as_json
            )
    assert any(r[2] == "crash" and r[1] == "cli" for r in _rows(_tele_env))


def _raise_runtime_error() -> object:
    raise RuntimeError("secret prompt")
