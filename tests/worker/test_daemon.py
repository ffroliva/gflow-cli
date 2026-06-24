from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.data.store import DataStore
from gflow_cli.errors import FlowApiError
from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import QueueRepository


@dataclass
class FakeGeneratedImage:
    media_name: str


@dataclass
class FakeVideoStatus:
    media_id: str


@dataclass
class FakeVideoResult:
    status: FakeVideoStatus


class FakeFlowApiClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.generate_image = AsyncMock()
        self.generate_images_batch = AsyncMock()
        self.generate_video = AsyncMock()
        self.create_project = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


@pytest.fixture
def temp_db(tmp_path: Path) -> DataStore:
    db_file = tmp_path / "gflow_test.db"
    # Ensure tables are created by applying migrations
    store = DataStore.open(db_file)
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('default', 'C:/profiles/default', '2026-06-24T00:00:00Z')"
    )
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('other_profile', 'C:/profiles/other', '2026-06-24T00:00:00Z')"
    )
    return store


def test_queue_repository_crud(temp_db: DataStore) -> None:
    repo = QueueRepository(temp_db)

    # 1. Enqueue a task
    task = repo.enqueue_task(
        task_id="task-123",
        profile_name="default",
        task_type="t2i",
        payload={"prompt": "test prompt", "aspect": "1:1"},
    )
    assert task.task_id == "task-123"
    assert task.status == "pending"
    assert task.payload == {"prompt": "test prompt", "aspect": "1:1"}

    # 2. Get the task
    task_fetched = repo.get_task("task-123")
    assert task_fetched is not None
    assert task_fetched.task_id == "task-123"
    assert task_fetched.status == "pending"
    assert task_fetched.payload == {"prompt": "test prompt", "aspect": "1:1"}

    # 3. Get next pending task
    pending = repo.get_next_pending_task("default")
    assert pending is not None
    assert pending.task_id == "task-123"

    # 4. Try getting pending task for a non-existent profile
    none_pending = repo.get_next_pending_task("other_profile")
    assert none_pending is None

    # 5. Update task status
    repo.update_task_status("task-123", status="processing")
    task_updated = repo.get_task("task-123")
    assert task_updated is not None
    assert task_updated.status == "processing"

    # 6. Fail processing tasks (sweep)
    count = repo.fail_processing_tasks("default", "Daemon crashed")
    assert count == 1
    task_swept = repo.get_task("task-123")
    assert task_swept is not None
    assert task_swept.status == "failed"
    assert task_swept.error is not None
    assert "Daemon crashed" in task_swept.error["detail"]


@pytest.mark.asyncio
async def test_worker_process_t2i_single(temp_db: DataStore) -> None:
    repo = QueueRepository(temp_db)
    task = repo.enqueue_task(
        task_id="task-t2i-single",
        profile_name="default",
        task_type="t2i",
        payload={"prompt": "scenic landscape", "aspect": "16:9", "count": 1},
    )

    worker = FlowWorker("default", str(temp_db.path))
    fake_client = FakeFlowApiClient()
    fake_client.create_project.return_value = MagicMock(project_id="project-abc")
    fake_client.generate_image.return_value = FakeGeneratedImage(media_name="media-img-123")

    with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake_client):
        await worker.process_task(task)

    # Check database updates
    updated = repo.get_task("task-t2i-single")
    assert updated is not None
    assert updated.status == "completed"
    assert updated.flow_media_id == "media-img-123"
    assert updated.error is None
    fake_client.generate_image.assert_called_once()
    worker.close()


@pytest.mark.asyncio
async def test_worker_process_t2i_batch(temp_db: DataStore) -> None:
    repo = QueueRepository(temp_db)
    task = repo.enqueue_task(
        task_id="task-t2i-batch",
        profile_name="default",
        task_type="t2i",
        payload={"prompt": "scenic landscape", "aspect": "16:9", "count": 3},
    )

    worker = FlowWorker("default", str(temp_db.path))
    fake_client = FakeFlowApiClient()
    fake_client.create_project.return_value = MagicMock(project_id="project-abc")
    fake_client.generate_images_batch.return_value = [
        FakeGeneratedImage(media_name="media-img-batch-1"),
        FakeGeneratedImage(media_name="media-img-batch-2"),
    ]

    with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake_client):
        await worker.process_task(task)

    # Check database updates
    updated = repo.get_task("task-t2i-batch")
    assert updated is not None
    assert updated.status == "completed"
    assert updated.flow_media_id == "media-img-batch-1"
    fake_client.generate_images_batch.assert_called_once()
    worker.close()


@pytest.mark.asyncio
async def test_worker_process_t2v(temp_db: DataStore) -> None:
    repo = QueueRepository(temp_db)
    task = repo.enqueue_task(
        task_id="task-t2v",
        profile_name="default",
        task_type="t2v",
        payload={"prompt": "cinematic camera movement", "aspect": "16:9"},
    )

    worker = FlowWorker("default", str(temp_db.path))
    fake_client = FakeFlowApiClient()
    fake_client.generate_video.return_value = FakeVideoResult(
        status=FakeVideoStatus(media_id="media-vid-123")
    )

    with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake_client):
        await worker.process_task(task)

    # Check database updates
    updated = repo.get_task("task-t2v")
    assert updated is not None
    assert updated.status == "completed"
    assert updated.flow_media_id == "media-vid-123"
    fake_client.generate_video.assert_called_once()
    worker.close()


@pytest.mark.asyncio
async def test_worker_process_failure_logs_rfc9457(temp_db: DataStore) -> None:
    repo = QueueRepository(temp_db)
    task = repo.enqueue_task(
        task_id="task-fail",
        profile_name="default",
        task_type="t2v",
        payload={"prompt": "failsafe"},
    )

    worker = FlowWorker("default", str(temp_db.path))
    fake_client = FakeFlowApiClient()
    # Trigger a typed GFlowError subclass or general error
    fake_client.generate_video.side_effect = FlowApiError(
        429,
        "Rate limit exceeded",
        route="video.generate",
    )

    with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake_client):
        await worker.process_task(task)

    # Check database updates
    updated = repo.get_task("task-fail")
    assert updated is not None
    assert updated.status == "failed"
    assert updated.flow_media_id is None
    assert updated.error is not None
    assert updated.error["type"] == "https://gflow-cli.dev/errors/api-error"
    assert updated.error["title"] == "Flow API error"
    assert updated.error["exit_code"] == 1
    assert "Rate limit exceeded" in updated.error["detail"]
    worker.close()


@pytest.mark.asyncio
async def test_worker_poll_loop_processes_tasks(temp_db: DataStore) -> None:
    repo = QueueRepository(temp_db)
    repo.enqueue_task(
        task_id="task-poll-1",
        profile_name="default",
        task_type="t2i",
        payload={"prompt": "prompt 1"},
    )
    repo.enqueue_task(
        task_id="task-poll-2",
        profile_name="default",
        task_type="t2i",
        payload={"prompt": "prompt 2"},
    )

    worker = FlowWorker("default", str(temp_db.path))
    fake_client = FakeFlowApiClient()
    fake_client.create_project.return_value = MagicMock(project_id="proj")
    fake_client.generate_image.return_value = FakeGeneratedImage(media_name="img-id")

    with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake_client):
        # Run worker loop in background task
        loop_task = asyncio.create_task(worker.start())
        # Let it run briefly
        await asyncio.sleep(0.5)
        # Stop worker
        worker.stop()
        await loop_task

    # Both tasks should be completed
    t1 = repo.get_task("task-poll-1")
    t2 = repo.get_task("task-poll-2")
    assert t1 is not None and t1.status == "completed"
    assert t2 is not None and t2.status == "completed"
    worker.close()
