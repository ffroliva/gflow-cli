from __future__ import annotations

import asyncio
import json
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


def _shared_failure() -> None:
    raise ValueError("shared")


def _caller_one() -> None:
    _shared_failure()


def _caller_two() -> None:
    _shared_failure()


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


def test_unhandled_exception_records_1_and_propagates(_tele_env: Path) -> None:
    failure = RuntimeError("secret")

    @click.group(cls=telemetry.CountingGroup, name="probe")
    def probe() -> None:
        pass

    @probe.command()
    def fail() -> None:
        raise failure

    result = CliRunner().invoke(probe, ["fail"])
    assert result.exception is failure
    assert any(row[2:4] == ("exit", "fail 1") for row in _rows(_tele_env))


def test_bad_config_exit_11_is_still_recorded(
    _tele_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_BROWSER_PROXY", "http://:pw@h:1")
    reset_settings()
    try:
        result = CliRunner().invoke(main, ["doctor"])
    finally:
        reset_settings()
    assert result.exit_code == 11
    assert any(row[2:4] == ("exit", "doctor 11") for row in _rows(_tele_env))


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


def test_crash_key_separates_two_routes_into_one_function() -> None:
    shared = FunctionType(_shared_failure.__code__, {})
    callers = {f.__name__: FunctionType(f.__code__, {}) for f in (_caller_one, _caller_two)}
    globals_map = {"__name__": "gflow_cli.routes", "_shared_failure": shared, **callers}
    for function in (shared, *callers.values()):
        function.__globals__.update(globals_map)
    keys = []
    for name in ("_caller_one", "_caller_two"):
        try:
            callers[name]()
        except ValueError as exc:
            keys.append(telemetry.crash_key(exc))
    assert keys[0] != keys[1]
    assert all("routes:_shared_failure" in key for key in keys)


def test_third_party_run_collapses_to_one_marker() -> None:
    package_globals: dict[str, object] = {"__name__": "playwright._impl.x"}
    exec(  # noqa: S102 - synthetic third-party frames are required by this regression test
        "def raise_site():\n    raise RuntimeError('secret')\ndef wrapper(fn):\n    fn()\n",
        package_globals,
    )
    raise_site = package_globals["raise_site"]
    wrapper = package_globals["wrapper"]
    inner = FunctionType(
        # "<...>" filenames are never measured by coverage; a plain name made CI's
        # `coverage combine` fail with "No source for code: .../tmp/pytest/.../test".
        compile("def inner():\n    raise_site()", "<synthetic>", "exec").co_consts[0],
        {"__name__": "gflow_cli.middle", "raise_site": raise_site},
    )
    outer = FunctionType(
        compile("def outer():\n    wrapper(inner)", "<synthetic>", "exec").co_consts[0],
        {"__name__": "gflow_cli.outer", "wrapper": wrapper, "inner": inner},
    )
    try:
        outer()
    except RuntimeError as exc:
        key = telemetry.crash_key(exc)
    assert key.count("<playwright>") == 2


def test_stdlib_and_exec_frames_are_dropped() -> None:
    def raise_from_json(_: object) -> object:
        raise RuntimeError("secret")

    try:
        import json as json_module

        json_module.loads("{}", object_hook=raise_from_json)
    except RuntimeError as exc:
        assert telemetry.crash_key(exc) == "RuntimeError at other"

    exec_globals: dict[str, object] = {"__name__": "<string>"}
    exec("def fail():\n    raise ValueError('secret')", exec_globals)  # noqa: S102
    try:
        exec_globals["fail"]()  # type: ignore[operator]
    except ValueError as exc:
        assert telemetry.crash_key(exc) == "ValueError at other"


def test_crash_with_no_gflow_frame_is_other() -> None:
    try:
        raise ValueError("secret")
    except ValueError as exc:
        assert telemetry.crash_key(exc) == "ValueError at other"


def test_incident_bundle_holds_the_same_fingerprint(_tele_env: Path) -> None:
    from gflow_cli.config import Settings
    from gflow_cli.diagnostics import IncidentRecorder

    failure = RuntimeError("secret exception message")
    try:
        raise failure
    except RuntimeError as exc:
        expected = telemetry.crash_key(exc)
        recorder = IncidentRecorder(Settings(home=_tele_env))
        ref = asyncio.run(recorder.capture_metadata_only(exc, phase="test"))
        assert ref is not None and ref.path is not None
        asyncio.run(recorder.finalize_all(close_ok=True))
    manifest = json.loads((ref.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fingerprint"] == expected
    assert "secret exception message" not in manifest["fingerprint"]


def test_crash_reporting_never_changes_the_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from structlog.testing import capture_logs

    from gflow_cli import telemetry
    from gflow_cli._cli_helpers import run_with_handlers

    crash_key = telemetry.crash_key
    monkeypatch.setattr(telemetry, "crash_key", lambda _: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(telemetry, "record_crash", lambda *_: (_ for _ in ()).throw(RuntimeError()))
    with capture_logs() as events, pytest.raises(SystemExit) as result:
        run_with_handlers(lambda: _raise_runtime_error(), cli_command="video t2v")
    assert result.value.code == 1
    assert any(event["event"] == "error_unhandled" for event in events)

    monkeypatch.setattr(telemetry, "crash_key", crash_key)
    monkeypatch.setattr(
        telemetry,
        "record_crash",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError()),
    )
    with capture_logs() as events, pytest.raises(SystemExit) as result:
        run_with_handlers(lambda: _raise_runtime_error(), cli_command="video t2v")
    assert result.value.code == 1
    assert any(event["event"] == "error_unhandled" for event in events)


def test_handled_crash_is_counted_on_both_arms(_tele_env: Path) -> None:
    from gflow_cli._cli_helpers import run_with_handlers

    for as_json in (False, True):
        with pytest.raises(SystemExit):
            run_with_handlers(
                lambda: _raise_runtime_error(), cli_command="video t2v", as_json=as_json
            )
    assert any(r[2] == "crash" and r[1] == "cli" for r in _rows(_tele_env))


def test_unguarded_command_crash_records_fingerprint(_tele_env: Path) -> None:
    failure = RuntimeError("secret C:\\Users\\jane")

    @click.group(cls=telemetry.CountingGroup, name="probe")
    def probe() -> None:
        pass

    @probe.command()
    def fail() -> None:
        raise failure

    result = CliRunner().invoke(probe, ["fail"])
    assert result.exception is failure
    crash_rows = [r for r in _rows(_tele_env) if r[1] == "cli" and r[2] == "crash"]
    assert len(crash_rows) == 1
    key = crash_rows[0][3]
    assert key.startswith("RuntimeError <- ")
    assert "secret" not in key
    assert "jane" not in key
    assert "\\" not in key


def test_handled_crash_is_not_double_counted(_tele_env: Path) -> None:
    from gflow_cli._cli_helpers import run_with_handlers

    @click.group(cls=telemetry.CountingGroup, name="probe")
    def probe() -> None:
        pass

    @probe.command()
    def fail() -> None:
        run_with_handlers(lambda: _raise_runtime_error(), cli_command="probe fail")

    result = CliRunner().invoke(probe, ["fail"])
    assert result.exit_code == 1
    crash_rows = [r for r in _rows(_tele_env) if r[1] == "cli" and r[2] == "crash"]
    assert len(crash_rows) == 1


def _raise_runtime_error() -> object:
    raise RuntimeError("secret prompt")
