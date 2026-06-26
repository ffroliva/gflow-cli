from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gflow_cli.config import get_settings
from gflow_cli.data.store import DataStore
from gflow_cli.ui.app import app, lifespan
from gflow_cli.worker.queue import QueueRepository


@pytest.fixture
def mock_worker():
    with patch("gflow_cli.ui.app.FlowWorker") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.start = AsyncMock()
        mock_instance.stop = MagicMock()
        mock_instance.close = MagicMock()
        mock_cls.return_value = mock_instance
        yield mock_instance


def test_app_routes() -> None:
    # Verify the FastMCP and proxy routes are registered in the FastAPI app
    route_paths = [route.path for route in app.routes]
    assert "/mcp" in route_paths
    assert "/mcp/message" in route_paths


def test_message_proxying(mock_worker) -> None:
    calls = []

    async def dummy_asgi(scope, receive, send):
        calls.append(scope)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"OK",
            }
        )

    with patch("gflow_cli.ui.app.mcp_sse_app", new=dummy_asgi):
        with TestClient(app) as client:
            payload = {"jsonrpc": "2.0", "method": "ping", "id": 1}
            response = client.post("/mcp/message", json=payload, headers={"Host": "localhost:8000"})
            assert response.status_code == 200
            assert len(calls) == 1
            assert calls[0]["path"] == "/messages"


def test_startup_db_sweep(mock_worker) -> None:
    settings = get_settings()
    db_path = settings.resolved_db_path()
    profile_name = "default"

    # Pre-populate database with a task that is "processing"
    with DataStore.open(db_path) as store:
        repo = QueueRepository(store)
        store.conn.execute(
            "INSERT OR IGNORE INTO profiles(name, profile_dir, first_seen_at) "
            "VALUES ('default', 'C:/profiles/default', '2026-06-24T00:00:00Z')"
        )
        repo.enqueue_task(
            task_id="task-swept",
            profile_name=profile_name,
            task_type="t2i",
            payload={"prompt": "sweep me"},
        )
        repo.update_task_status("task-swept", "processing")

    with TestClient(app):
        pass

    # Verify task has been swept to "failed"
    with DataStore.open(db_path) as store:
        repo = QueueRepository(store)
        task_after = repo.get_task("task-swept")
        assert task_after is not None
        assert task_after.status == "failed"
        assert task_after.error is not None
        assert "Daemon shut down or restarted unexpectedly." in task_after.error["detail"]


def test_profile_lockfile_lifecycle(mock_worker) -> None:
    settings = get_settings()
    profile_name = "default"
    lockfile_path = settings.profile_subdir(profile_name) / "profile.lock"

    if lockfile_path.exists():
        lockfile_path.unlink()

    with TestClient(app):
        assert lockfile_path.exists()
        pid = lockfile_path.read_text()
        assert pid == str(os.getpid())

    assert not lockfile_path.exists()


@pytest.mark.asyncio
async def test_lifespan_cancels_running_worker() -> None:
    """Lifespan shutdown must cancel a still-running worker task and return
    cleanly, without leaking the worker's asyncio.CancelledError.

    Regression for SonarCloud python:S7497 (cancellation must be re-raised).
    """
    started = asyncio.Event()

    async def _never_ending() -> None:
        started.set()
        await asyncio.Event().wait()  # blocks until cancelled

    with patch("gflow_cli.ui.app.FlowWorker") as mock_cls:
        instance = MagicMock()
        instance.start = _never_ending
        instance.stop = MagicMock()
        instance.close = MagicMock()
        mock_cls.return_value = instance

        async with lifespan(app):
            # Worker task is created and running by the time startup completes.
            await asyncio.wait_for(started.wait(), timeout=1)

    # Reaching here means shutdown cancelled the worker and returned cleanly.
    instance.stop.assert_called_once()
    instance.close.assert_called_once()
