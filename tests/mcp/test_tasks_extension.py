"""Unit and wire tests for the MCP Tasks extension (SEP-2663, #1102)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import METHOD_NOT_FOUND, CancelTaskRequestParams, GetTaskRequestParams, Request
from pydantic import TypeAdapter

from gflow_cli.data.store import DataStore
from gflow_cli.mcp.tasks_extension import TasksExtension
from gflow_cli.worker.queue import QueueRepository


@pytest.fixture
def test_db(tmp_path: Path) -> DataStore:
    db_path = tmp_path / "test_tasks.db"
    store = DataStore.open(db_path)
    store.conn.execute(
        "INSERT OR IGNORE INTO profiles (name, first_seen_at) VALUES ('default', CURRENT_TIMESTAMP)"
    )
    return store


def _enqueue(
    store: DataStore, task_id: str, status: str = "pending", error: dict[str, Any] | None = None
) -> None:
    repo = QueueRepository(store)
    repo.enqueue_task(task_id=task_id, profile_name="default", task_type="t2i", payload={})
    if status != "pending":
        repo.update_task_status(task_id, status, error=error)


async def _get(store: DataStore, task_id: str) -> dict[str, Any]:
    ext = TasksExtension(data_store=store)
    return await ext._handle_get_task(None, GetTaskRequestParams(task_id=task_id))  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_tasks_extension_get_task_success(test_db: DataStore) -> None:
    _enqueue(test_db, "task-uuid-001")

    result = await _get(test_db, "task-uuid-001")

    assert result["taskId"] == "task-uuid-001"
    assert result["status"] == "working"
    assert "result" not in result


@pytest.mark.asyncio
async def test_tasks_extension_get_task_not_found(test_db: DataStore) -> None:
    with pytest.raises(MCPError, match="Task not found"):
        await _get(test_db, "non-existent-task")


@pytest.mark.asyncio
async def test_tasks_extension_cancel_task(test_db: DataStore) -> None:
    _enqueue(test_db, "task-uuid-002")
    ext = TasksExtension(data_store=test_db)

    ack = await ext._handle_cancel_task(  # pyright: ignore[reportPrivateUsage]
        None, CancelTaskRequestParams(task_id="task-uuid-002")
    )

    assert ack == {}  # SEP-2663: an empty acknowledgement
    assert (await _get(test_db, "task-uuid-002"))["status"] == "cancelled"
    updated_task = QueueRepository(test_db).get_task("task-uuid-002")
    assert updated_task is not None
    assert updated_task.status == "cancelled"
    # #1201: the same members as every other envelope; nothing ran, so nothing billed.
    assert updated_task.error is not None
    err = updated_task.error
    assert (err["charged"], err["next_action"], err["retryable"]) == ("no", "none", False)
    assert err["type"] == "https://gflow-cli.dev/errors/cancelled"
    assert "status" not in err  # no invented HTTP status (was 499)


@pytest.mark.asyncio
async def test_a_failed_generation_reads_completed_with_an_error_result(
    test_db: DataStore,
) -> None:
    """SEP-2663: ``failed`` is for JSON-RPC errors; a tool's own error is a completed result."""
    _enqueue(test_db, "bad", "failed", {"title": "Auth expired", "status": 401})

    got = await _get(test_db, "bad")

    assert got["status"] == "completed"
    assert got["result"]["isError"] is True
    assert got["result"]["structuredContent"]["error"]["title"] == "Auth expired"


@pytest.mark.asyncio
async def test_an_indeterminate_row_never_reads_as_a_clean_success(test_db: DataStore) -> None:
    """A job stopped after submit may have billed: a cancel reads cancelled, anything else an
    error result, and both carry the credit warning."""
    _enqueue(test_db, "cancelled", "indeterminate", {"reason": "cancelled"})
    _enqueue(test_db, "killed", "indeterminate", {"title": "Interrupted"})

    cancelled = await _get(test_db, "cancelled")
    killed = await _get(test_db, "killed")

    assert cancelled["status"] == "cancelled" and "result" not in cancelled
    assert killed["status"] == "completed" and killed["result"]["isError"] is True
    for got in (cancelled, killed):
        assert "credit" in got["statusMessage"]


# ---------------------------------------------------------------------------
# The real wire, through the SDK's in-process client, in both protocol eras
# ---------------------------------------------------------------------------


class _Raw(Request[Any, str]):
    method: str
    params: dict[str, Any] | None = None


async def _wire(mode: str, method: str, task_id: str) -> dict[str, Any]:
    from gflow_cli.mcp.server import server

    async with Client(server, mode=mode) as client:
        session = client._session  # pyright: ignore[reportPrivateUsage]
        assert session is not None
        return await session.send_request(
            _Raw(method=method, params={"taskId": task_id}), TypeAdapter(dict[str, Any])
        )


def _leaves(group: BaseExceptionGroup[BaseException]) -> list[BaseException]:
    out: list[BaseException] = []
    for e in group.exceptions:
        out.extend(_leaves(e) if isinstance(e, BaseExceptionGroup) else [e])
    return out


@pytest.fixture
def served(test_db: DataStore, monkeypatch: pytest.MonkeyPatch) -> DataStore:
    from gflow_cli.mcp import server

    monkeypatch.setattr(server.tasks_extension, "_data_store", test_db)
    return test_db


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_tasks_result_is_not_a_method(served: DataStore, mode: str) -> None:
    """SEP-2663 removes tasks/result; a caller MUST get -32601."""
    _enqueue(served, "t")

    # The client's task group wraps the error it raises.
    with pytest.raises(BaseExceptionGroup) as caught:
        await _wire(mode, "tasks/result", "t")

    matched, _ = caught.value.split(MCPError)
    assert matched is not None
    codes = {e.code for e in _leaves(matched) if isinstance(e, MCPError)}
    assert codes == {METHOD_NOT_FOUND}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_tasks_get_speaks_sep_2663_on_the_wire(served: DataStore, mode: str) -> None:
    _enqueue(served, "t")

    got = await _wire(mode, "tasks/get", "t")

    assert got["taskId"] == "t" and got["status"] == "working"
    assert got["ttlMs"] is None  # rows are kept with no expiry
    assert "ttl" not in got  # the 2025-11-25 key
    assert {"createdAt", "lastUpdatedAt"} <= got.keys()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected"), [("legacy", {}), ("2026-07-28", {"resultType": "complete"})]
)
async def test_tasks_cancel_is_an_empty_ack_on_the_wire(
    served: DataStore, mode: str, expected: dict[str, Any]
) -> None:
    _enqueue(served, "t")

    ack = await _wire(mode, "tasks/cancel", "t")

    assert {k: v for k, v in ack.items() if k != "_meta"} == expected


@pytest.mark.asyncio
async def test_mcp_generate_image_non_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli.mcp.tools import gflow_generate_image

    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "profile_default").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home" / "profile_default" / ".gflow_account").write_text("user@example.com")
    # wait=false now runs the job in the background (#1097); this test is about the handle.
    monkeypatch.setattr("gflow_cli.mcp.tools._run_detached", AsyncMock())

    resp = await gflow_generate_image(prompt="test prompt", wait=False)
    assert resp.get("status") == "pending"
    assert "task_id" in resp


@pytest.mark.sharpit(allow_n_plus_one=True)  # one tasks/cancel and tasks/get per seeded state
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "2026-07-28"])
async def test_tasks_cancel_then_get_on_the_wire_per_state(served: DataStore, mode: str) -> None:
    """The MCP twin of `gflow cancel` (#1198), driven through a real client in both eras:
    queued and claimed-not-submitted read `cancelled` with nothing charged, a submitted job
    reads `cancelled` with the charge warning and stays so after its worker finishes, and a
    finished job is untouched."""
    repo = QueueRepository(served)
    for task_id in ("q", "w", "r", "d"):
        repo.enqueue_task(
            task_id=task_id,
            profile_name="default",
            task_type="t2i",
            payload={"prompt": "p", "aspect": "1:1", "count": 1},
        )
    repo.claim_task("w", "worker")
    repo.claim_task("r", "worker")
    repo.update_checkpoint("r", claimant="worker", phase="submit_attempted", may_have_spent=True)
    repo.update_task_status("d", "completed")

    for task_id in ("q", "w", "r", "d"):
        await _wire(mode, "tasks/cancel", task_id)
    repo.update_task_status("r", "completed", flow_media_id="m")  # its worker finishes later

    got = {t: await _wire(mode, "tasks/get", t) for t in ("q", "w", "r", "d")}
    assert {t: g["status"] for t, g in got.items()} == {
        "q": "cancelled",
        "w": "cancelled",
        "r": "cancelled",  # 2025-11-25: a cancelled task MUST remain cancelled
        "d": "completed",
    }
    assert "nothing was charged" in got["w"]["statusMessage"].lower()
    assert "may still" in got["r"]["statusMessage"]
