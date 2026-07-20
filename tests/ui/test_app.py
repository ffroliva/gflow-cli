from __future__ import annotations

import asyncio
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


def test_startup_recovery_classifies_by_checkpoint(mock_worker) -> None:
    """Startup recovery (C5) replaces the blanket sweep with per-task
    classification: a processing task with NO/pre-submit checkpoint is failed
    (nothing spent); one whose checkpoint reached submit_attempted is
    indeterminate (a credit may have been spent — never silently failed)."""
    settings = get_settings()
    db_path = settings.resolved_db_path()
    profile_name = "default"

    # Pre-populate two processing tasks: one pre-submit, one post-submit.
    with DataStore.open(db_path) as store:
        repo = QueueRepository(store)
        store.conn.execute(
            "INSERT OR IGNORE INTO profiles(name, profile_dir, first_seen_at) "
            "VALUES ('default', 'C:/profiles/default', '2026-06-24T00:00:00Z')"
        )
        repo.enqueue_task("task-presubmit", profile_name, "t2i", {"prompt": "safe fail"})
        repo.update_task_status("task-presubmit", "processing")

        repo.enqueue_task("task-postsubmit", profile_name, "t2i", {"prompt": "uncertain"})
        repo.update_task_status("task-postsubmit", "processing")
        repo.update_checkpoint(
            "task-postsubmit",
            claimant="worker:default:1",
            phase="submit_attempted",
            may_have_spent=True,
        )

    with TestClient(app):
        pass

    with DataStore.open(db_path) as store:
        repo = QueueRepository(store)
        pre = repo.get_task("task-presubmit")
        assert pre is not None
        assert pre.status == "failed"
        assert pre.error is not None
        assert "before any submit" in pre.error["detail"]

        post = repo.get_task("task-postsubmit")
        assert post is not None
        assert post.status == "indeterminate"
        assert post.error is not None
        assert "credit may have been spent" in post.error["detail"]


def test_daemon_holds_no_profile_lock_while_idle(mock_worker) -> None:
    """D3: the daemon no longer writes an overwriteable ``profile.lock`` and
    holds no profile lease while idle. Ownership is acquired per browser task
    inside FlowApiClient's launch path, so an idle daemon leaves the profile
    free — proven by a clean ProfileLease.try_acquire during its lifetime."""
    from gflow_cli.profile_lease import ProfileLease

    settings = get_settings()
    profile_name = "default"
    profile_dir = settings.profile_subdir(profile_name)
    legacy_lock = profile_dir / "profile.lock"
    if legacy_lock.exists():
        legacy_lock.unlink()

    with TestClient(app):
        # No lifetime lock file is created, and the profile is not owned while
        # the daemon idles: a fresh lease acquires cleanly and releases.
        assert not legacy_lock.exists()
        lease = ProfileLease(profile_dir)
        assert lease.try_acquire() is True
        lease.release()

    assert not legacy_lock.exists()


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


@pytest.mark.asyncio
async def test_daemon_lifespan_releases_lease_after_worker_cancellation() -> None:
    """D4: when the lifespan itself is cancelled mid-run, its try/finally
    shutdown still runs (stop -> cancel worker -> close store), leaving the
    profile free — a fresh ProfileLease.try_acquire succeeds (nothing leaked)."""
    from gflow_cli.profile_lease import ProfileLease

    settings = get_settings()
    profile_dir = settings.profile_subdir("default")

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

        async def _run() -> None:
            # Block forever INSIDE the lifespan body so cancelling this task
            # exercises the lifespan's try/finally shutdown path.
            async with lifespan(app):
                await asyncio.Event().wait()

        task = asyncio.create_task(_run())
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # Shutdown ran despite the cancellation.
    instance.stop.assert_called_once()
    instance.close.assert_called_once()
    # Nothing leaked the profile: it acquires cleanly afterwards.
    lease = ProfileLease(profile_dir)
    assert lease.try_acquire() is True
    lease.release()
