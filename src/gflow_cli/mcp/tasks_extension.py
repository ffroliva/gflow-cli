"""MCP Tasks extension implementation (SEP-2663) for gflow-cli."""

from __future__ import annotations

from typing import Any

from mcp.server.extension import Extension, MethodBinding
from mcp.types import (
    CancelTaskRequestParams,
    CancelTaskResult,
    GetTaskRequestParams,
    GetTaskResult,
    TaskStatus,
)

from gflow_cli.config import get_settings
from gflow_cli.data.store import DataStore
from gflow_cli.worker.queue import QueueRepository


class TasksExtension(Extension):
    """Extension subclass handling SEP-2663 tasks/get and tasks/cancel requests."""

    identifier = "io.modelcontextprotocol/tasks"

    def __init__(self, data_store: DataStore | None = None) -> None:
        self._data_store = data_store

    def set_data_store(self, data_store: DataStore) -> None:
        self._data_store = data_store

    def _get_store(self) -> DataStore:
        if self._data_store is None:
            self._data_store = DataStore.open(get_settings().resolved_db_path())
        return self._data_store

    def methods(self) -> list[MethodBinding]:
        return [
            MethodBinding(
                method="tasks/get",
                params_type=GetTaskRequestParams,
                handler=self._handle_get_task,
            ),
            MethodBinding(
                method="tasks/cancel",
                params_type=CancelTaskRequestParams,
                handler=self._handle_cancel_task,
            ),
        ]

    async def _handle_get_task(self, context: Any, params: GetTaskRequestParams) -> GetTaskResult:
        task_id = params.task_id
        store = self._get_store()
        repo = QueueRepository(store)
        queue_task = repo.get_task(task_id)
        if queue_task is None:
            raise KeyError(f"Task not found: {task_id}")

        mcp_status: TaskStatus = "working"
        if queue_task.status == "completed":
            mcp_status = "completed"
        elif queue_task.status in ("failed", "indeterminate"):
            mcp_status = "failed"

        return GetTaskResult(
            task_id=queue_task.task_id,
            status=mcp_status,
            created_at=queue_task.created_at or "",
            last_updated_at=queue_task.updated_at or "",
            ttl=3600,
        )

    async def _handle_cancel_task(
        self, context: Any, params: CancelTaskRequestParams
    ) -> CancelTaskResult:
        task_id = params.task_id
        store = self._get_store()
        repo = QueueRepository(store)
        queue_task = repo.get_task(task_id)
        if queue_task is None:
            raise KeyError(f"Task not found: {task_id}")

        repo.update_task_status(task_id, "failed")
        return CancelTaskResult(
            task_id=queue_task.task_id,
            status="cancelled",
            created_at=queue_task.created_at or "",
            last_updated_at=queue_task.updated_at or "",
            ttl=3600,
        )
