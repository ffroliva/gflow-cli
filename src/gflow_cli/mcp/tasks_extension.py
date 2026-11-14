"""MCP Tasks extension implementation (SEP-2663) for gflow-cli.

``tasks/get`` and ``tasks/cancel`` follow SEP-2663, which this server declares. The mcp
SDK's task types model only the older 2025-11-25 spec, so the replies are built by hand
(#1102). SEP-2663 removes ``tasks/result``: the finished result rides on ``tasks/get``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from mcp.server.extension import Extension, MethodBinding
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INVALID_PARAMS,
    CallToolResult,
    CancelTaskRequestParams,
    GetTaskRequestParams,
    TaskStatus,
    TextContent,
)

from gflow_cli.config import get_settings
from gflow_cli.data.store import DataStore
from gflow_cli.worker.queue import CANCELLED_ERROR, QueueRepository, QueueTask

log = structlog.get_logger()

# ``wait=false`` jobs running in this server's loop, by task id (#1097). The strong
# reference is what keeps asyncio from collecting a task mid-run.
DETACHED: dict[str, asyncio.Task[None]] = {}

_CANCEL_WAIT_S = 10.0


def detach(task_id: str, job: Coroutine[Any, Any, None], settle: Callable[[], None]) -> None:
    """Run ``job`` in the background and track it until it finishes.

    ``settle`` runs when the job is cancelled, and must be idempotent: a cancel that
    lands before the job's first step skips the job's own handlers entirely.
    """
    task = asyncio.create_task(job)
    DETACHED[task_id] = task

    def done(t: asyncio.Task[None]) -> None:
        DETACHED.pop(task_id, None)
        if t.cancelled():
            settle()

    task.add_done_callback(done)


async def drain_detached() -> None:
    """Cancel every running job and wait for each to record how far it got.

    Both servers call this before ``asyncio.run`` tears down. Its blanket cancel would
    also cancel Playwright's connection, so a job's browser could not close; and on HTTP,
    uvicorn re-raises the stop signal and kills the process before that point anyway
    (spike 2026-10-25-mcp-detached-task-shutdown).
    """
    tasks = list(DETACHED.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=_CANCEL_WAIT_S)


def _mcp_status(task: QueueTask) -> TaskStatus:
    # SEP-2663 keeps ``failed`` for JSON-RPC errors. A generation that failed is a tool
    # result with ``isError``, which the spec files under ``completed``.
    if task.status in ("failed", "indeterminate"):
        return "cancelled" if (task.error or {}).get("reason") == "cancelled" else "completed"
    if task.status == "completed":
        return "completed"
    return "working"


def _status_message(task: QueueTask) -> str | None:
    if task.status == "indeterminate":
        return "Stopped after submit; a credit may have been spent. Check the Flow project."
    return None


class TasksExtension(Extension):
    """Extension subclass handling SEP-2663 tasks/get and tasks/cancel."""

    identifier = "io.modelcontextprotocol/tasks"

    def __init__(self, data_store: DataStore | None = None) -> None:
        self._data_store = data_store

    def set_data_store(self, data_store: DataStore) -> None:
        self._data_store = data_store

    def _get_store(self) -> DataStore:
        if self._data_store is None:
            self._data_store = DataStore.open(get_settings().resolved_db_path())
        return self._data_store

    def _get_task(self, task_id: str) -> QueueTask:
        queue_task = QueueRepository(self._get_store()).get_task(task_id)
        if queue_task is None:
            raise MCPError(INVALID_PARAMS, f"Task not found: {task_id}")
        return queue_task

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

    async def _handle_get_task(self, context: Any, params: GetTaskRequestParams) -> dict[str, Any]:
        queue_task = self._get_task(params.task_id)
        status = _mcp_status(queue_task)
        reply: dict[str, Any] = {
            "taskId": queue_task.task_id,
            "status": status,
            "createdAt": queue_task.created_at or "",
            "lastUpdatedAt": queue_task.updated_at or "",
            "ttlMs": None,  # rows are kept with no expiry
        }
        if (message := _status_message(queue_task)) is not None:
            reply["statusMessage"] = message
        if status == "completed":
            reply["result"] = self._tool_result(queue_task)
        return reply

    def _tool_result(self, queue_task: QueueTask) -> dict[str, Any]:
        """The finished job's tool reply: the same payload ``wait=true`` returns."""
        from gflow_cli.mcp.tools import task_payload  # tools imports this module

        payload = task_payload(queue_task.profile_name, queue_task.task_id, self._get_store().path)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, indent=2))],
            structured_content=payload,
            is_error=payload["status"] != "completed",
        ).model_dump(by_alias=True, mode="json", exclude_none=True)

    async def _handle_cancel_task(
        self, context: Any, params: CancelTaskRequestParams
    ) -> dict[str, Any]:
        task_id = params.task_id
        before = self._get_task(task_id)
        repo = QueueRepository(self._get_store())
        running = DETACHED.get(task_id)
        log.info("mcp.task.cancel_requested", task_id=task_id, running_here=running is not None)
        if before.status in ("completed", "failed", "indeterminate"):
            pass  # already finished: a cancel is cooperative and changes nothing
        elif running is not None:
            # The job's own CancelledError handler records how far it got: a job past
            # submit becomes indeterminate (a credit may be spent), never failed. Waiting
            # for it keeps the next tasks/get consistent with this ack.
            running.cancel()
            await asyncio.wait({running}, timeout=_CANCEL_WAIT_S)
        else:
            repo.update_task_status(
                task_id,
                "failed",
                error=dict(CANCELLED_ERROR),
                only_from=("pending",),
            )
        return {}  # SEP-2663: an empty acknowledgement; tasks/get reads the outcome
