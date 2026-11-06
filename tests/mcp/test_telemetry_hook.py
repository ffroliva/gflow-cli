"""MCP tool usage is counted at the shared error funnel (#1164)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gflow_cli import telemetry
from gflow_cli.mcp import tools


@pytest.fixture(autouse=True)
def _telemetry_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.delenv("GFLOW_CLI_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path / "home"))
    telemetry.killed.cache_clear()
    yield
    telemetry.killed.cache_clear()


def _rows(tmp_path: Path) -> dict[tuple[str, str, str, str], int]:
    db = tmp_path / "home" / "telemetry.db"
    if not db.exists():
        return {}
    with sqlite3.connect(db) as con:
        return {
            (week, surface, kind, key): n
            for week, surface, kind, key, n in con.execute(
                "SELECT week, surface, kind, key, n FROM counters"
            )
        }


def _context(name: str | None = "pytest") -> Any:
    client_info = SimpleNamespace(name=name) if name is not None else None
    params = SimpleNamespace(client_info=client_info) if client_info is not None else None
    session = SimpleNamespace(client_params=params)
    return SimpleNamespace(request_context=SimpleNamespace(session=session))


@tools._guarded
async def _success() -> dict[str, Any]:
    return {"status": "ok"}


@tools._guarded
async def _error_envelope() -> dict[str, Any]:
    return {"status": "error"}


@tools._guarded
async def _rate_limited() -> dict[str, Any]:
    return tools._rate_limited_envelope()


@tools._guarded
async def _invalid_tools() -> dict[str, Any]:
    return {"status": "invalid_tools", "error": "bad tool"}


@tools._guarded
async def _pending() -> dict[str, Any]:
    return {"status": "pending", "task_id": "opaque"}


@tools._guarded
async def _gflow_error() -> dict[str, Any]:
    from gflow_cli.errors import InsufficientCreditsError

    raise InsufficientCreditsError("not enough credits")


@tools._guarded
async def _unexpected() -> dict[str, Any]:
    raise _UNEXPECTED_EXCEPTION


_UNEXPECTED_EXCEPTION = RuntimeError("private details")
_UNEXPECTED_RESULT = {
    "status": "error",
    "error": {
        "type": "https://gflow-cli.dev/errors/unknown",
        "title": "Unexpected Error",
        "status": 500,
        "detail": "Unexpected RuntimeError; details were logged server-side.",
        "message": "Unexpected RuntimeError; details were logged server-side.",
        "retryable": False,
    },
}


@pytest.mark.asyncio
async def test_success_records_outcome_0(tmp_path: Path) -> None:
    await _success(ctx=_context())
    assert any(row[1:4] == ("mcp", "exit", "_success 0") for row in _rows(tmp_path))


@pytest.mark.asyncio
async def test_error_envelope_is_not_success(
    tmp_path: Path,
) -> None:
    await _error_envelope(ctx=_context())
    assert any(row[1:4] == ("mcp", "exit", "_error_envelope error") for row in _rows(tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize("fn", [_rate_limited, _invalid_tools])
async def test_tool_local_refusals_are_not_success(fn: Any, tmp_path: Path) -> None:
    await fn(ctx=_context())
    assert any(row[1:3] == ("mcp", "exit") and row[3].endswith(" error") for row in _rows(tmp_path))


@pytest.mark.asyncio
async def test_pending_records_queued(tmp_path: Path) -> None:
    await _pending(ctx=_context())
    assert any(row[1:4] == ("mcp", "exit", "_pending queued") for row in _rows(tmp_path))


@pytest.mark.asyncio
async def test_gflow_error_records_its_exit_code(
    tmp_path: Path,
) -> None:
    await _gflow_error(ctx=_context())
    assert any(row[1:4] == ("mcp", "exit", "_gflow_error 37") for row in _rows(tmp_path))


@pytest.mark.asyncio
async def test_unexpected_exception_records_1_and_envelope_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await _unexpected(ctx=_context())
    encoded = json.dumps(result, sort_keys=True).encode()
    assert encoded == json.dumps(_UNEXPECTED_RESULT, sort_keys=True).encode()
    assert any(row[1:4] == ("mcp", "exit", "_unexpected 1") for row in _rows(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_TELEMETRY", "0")
    telemetry.killed.cache_clear()
    killed_result = await _unexpected()
    assert json.dumps(killed_result, sort_keys=True).encode() == encoded


@pytest.mark.asyncio
async def test_unexpected_exception_records_its_crash_key(
    tmp_path: Path,
) -> None:
    await _unexpected(ctx=_context())
    key = telemetry.crash_key(_UNEXPECTED_EXCEPTION)
    assert any(row[1:4] == ("mcp", "crash", key) for row in _rows(tmp_path))


@pytest.mark.asyncio
async def test_record_failure_never_reaches_client(monkeypatch: pytest.MonkeyPatch) -> None:

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("telemetry failure")

    monkeypatch.setattr(telemetry, "record", fail)
    result = await _unexpected(ctx=_context())
    assert (
        json.dumps(result, sort_keys=True).encode()
        == json.dumps(_UNEXPECTED_RESULT, sort_keys=True).encode()
    )


@pytest.mark.asyncio
async def test_record_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    callback_finished = asyncio.Event()

    def stalled(*_args: Any, **_kwargs: Any) -> None:
        time.sleep(0.5)

    async def concurrent_task() -> float:
        await asyncio.sleep(0.05)
        callback_finished.set()
        return time.monotonic()

    monkeypatch.setattr(telemetry, "record", stalled)
    started = time.monotonic()
    callback = asyncio.create_task(concurrent_task())
    await _success(ctx=_context())
    assert callback_finished.is_set()
    assert await callback - started < 0.25


@pytest.mark.asyncio
async def test_a_running_server_honours_off(
    tmp_path: Path,
) -> None:
    await _success(ctx=_context())
    before = _rows(tmp_path)
    (tmp_path / "home" / "telemetry.off").write_text("")
    await _success()
    assert _rows(tmp_path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_name", "expected"),
    [
        ("codex-mcp-client", "codex-mcp-client"),
        ("claude-code", "claude-code"),
        ("unobserved-client", "other"),
    ],
)
async def test_known_client_name_is_counted_and_unknown_becomes_other(
    client_name: str,
    expected: str,
    tmp_path: Path,
) -> None:
    await _success(ctx=_context(client_name))
    assert any(row[1:4] == ("mcp", "client", expected) for row in _rows(tmp_path))


@pytest.mark.asyncio
async def test_missing_client_info_is_not_an_error(
    tmp_path: Path,
) -> None:
    assert await _success(ctx=_context(None)) == {"status": "ok"}
    assert not any(row[2] == "client" for row in _rows(tmp_path))


def test_returned_error_exit_code_is_preserved() -> None:
    assert tools.outcome_of({"status": "error", "error": {"exit_code": 37}}) == "37"


def test_registered_tool_receives_sdk_context_without_schema_change(mcp_server: Any) -> None:
    tool = mcp_server._tool_manager._tools["gflow_list_tools"]
    assert tool.context_kwarg == "ctx"
    assert "ctx" not in tool.parameters["properties"]
