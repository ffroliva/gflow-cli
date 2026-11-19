"""#1375 scenarios 5 and 6 over MCP: a video job on a model Flow is retiring carries a
``warnings`` entry on the ``wait=true`` reply and on the queued ``tasks/get`` result, and the
tool descriptions an agent reads name the fields."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.types import GetTaskRequestParams

from gflow_cli.data.store import DataStore
from gflow_cli.mcp.tasks_extension import TasksExtension
from gflow_cli.mcp.tools import server, task_payload
from gflow_cli.worker.queue import QueueRepository

_RETIRING = {"type": "model_retiring", "model": "veo_3_1_fast", "retires_on": "2026-12-05"}


def _store(tmp_path: Path, model: object, status: str = "completed") -> DataStore:
    store = DataStore.open(tmp_path / "q.db")
    store.conn.execute(
        "INSERT OR IGNORE INTO profiles (name, first_seen_at) VALUES ('p', CURRENT_TIMESTAMP)"
    )
    repo = QueueRepository(store)
    payload: dict[str, Any] = {"prompt": "a lighthouse", "mode": "t2v"}
    if model is not None:
        payload["model"] = model
    repo.enqueue_task(task_id="t1", profile_name="p", task_type="t2v", payload=payload)
    repo.update_task_status("t1", status, error={"detail": "boom"} if status == "failed" else None)
    return store


def test_wait_true_reply_warns_about_a_retiring_model(tmp_path: Path) -> None:
    store = _store(tmp_path, "veo-fast")
    assert task_payload("p", "t1", store.path)["warnings"] == [_RETIRING]


def test_a_failed_job_on_a_retiring_model_still_warns(tmp_path: Path) -> None:
    store = _store(tmp_path, "veo-fast", status="failed")
    reply = task_payload("p", "t1", store.path)
    assert reply["status"] == "failed"
    assert reply["warnings"] == [_RETIRING]


@pytest.mark.parametrize("model", ["veo-lite", None, "a-model-gflow-no-longer-knows", 5])
def test_no_warnings_key_when_the_model_stays(tmp_path: Path, model: object) -> None:
    store = _store(tmp_path, model)
    assert "warnings" not in task_payload("p", "t1", store.path)


@pytest.mark.asyncio
async def test_queued_tasks_get_result_carries_the_warning(tmp_path: Path) -> None:
    store = _store(tmp_path, "veo-fast")
    ext = TasksExtension(data_store=store)
    reply = await ext._handle_get_task(None, GetTaskRequestParams(task_id="t1"))  # pyright: ignore[reportPrivateUsage]
    assert reply["result"]["structuredContent"]["warnings"] == [_RETIRING]


def test_tool_descriptions_name_the_retirement_fields() -> None:
    tools = server._tool_manager._tools  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    assert "retires_on" in (tools["gflow_list_models"].description or "")
    assert "model_retiring" in (tools["gflow_generate_video"].description or "")
