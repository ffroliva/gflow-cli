"""Unit tests for MCP Tasks extension (SEP-2663)."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.types import CancelTaskRequestParams, GetTaskRequestParams

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


@pytest.mark.asyncio
async def test_tasks_extension_get_task_success(test_db: DataStore) -> None:
    repo = QueueRepository(test_db)
    task_id = "task-uuid-001"
    repo.enqueue_task(
        task_id=task_id,
        profile_name="default",
        task_type="image",
        payload={"prompt": "cyberpunk city"},
    )

    ext = TasksExtension(data_store=test_db)
    result = await ext._handle_get_task(
        context=None,
        params=GetTaskRequestParams(task_id=task_id),
    )

    assert result.task_id == task_id
    assert result.status == "working"


@pytest.mark.asyncio
async def test_tasks_extension_get_task_not_found(test_db: DataStore) -> None:
    ext = TasksExtension(data_store=test_db)
    with pytest.raises(KeyError, match="Task not found"):
        await ext._handle_get_task(
            context=None,
            params=GetTaskRequestParams(task_id="non-existent-task"),
        )


@pytest.mark.asyncio
async def test_tasks_extension_cancel_task(test_db: DataStore) -> None:
    repo = QueueRepository(test_db)
    task_id = "task-uuid-002"
    repo.enqueue_task(
        task_id=task_id,
        profile_name="default",
        task_type="video",
        payload={"prompt": "ocean sunset"},
    )

    ext = TasksExtension(data_store=test_db)
    result = await ext._handle_cancel_task(
        context=None,
        params=CancelTaskRequestParams(task_id=task_id),
    )

    assert result.task_id == task_id
    assert result.status == "cancelled"

    updated_task = repo.get_task(task_id)
    assert updated_task is not None
    assert updated_task.status == "failed"


@pytest.mark.asyncio
async def test_mcp_generate_image_non_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli.mcp.tools import gflow_generate_image

    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path / "home"))
    (tmp_path / "home" / "profile_default").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home" / "profile_default" / ".gflow_account").write_text("user@example.com")

    resp = await gflow_generate_image(prompt="test prompt", wait=False)
    assert resp.get("status") == "pending"
    assert "task_id" in resp
