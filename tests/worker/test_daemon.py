from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.video import VideoResult, VideoStatus
from gflow_cli.data.store import DataStore
from gflow_cli.errors import FlowApiError
from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import QueueRepository


@dataclass
class FakeGeneratedImage:
    media_name: str
    dimensions: tuple[int, int] = (1024, 1024)
    workflow_id: str = "workflow-123"
    media_generation_id: str = "gen-123"
    model_name_type: str = "model-123"
    aspect_ratio: str = "1:1"
    seed: int = 12345
    fife_url: str = "http://fake"


def _completed_video_result(media_id: str = "media-vid-123") -> VideoResult:
    """A real, successful VideoResult — the worker checks ``status.succeeded``,
    which only exists on the real VideoStatus (a hand-rolled fake silently broke
    this path and flipped the task to ``failed``)."""
    return VideoResult(
        status=VideoStatus(
            media_id=media_id,
            status="MEDIA_GENERATION_STATUS_SUCCESSFUL",
        ),
        local_path=None,
        project_id="proj-abc",
        flow_operation_id="op-123",
    )


class FakeFlowApiClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.generate_image = AsyncMock()
        self.generate_images_batch = AsyncMock()
        self.generate_video = AsyncMock()
        self.create_project = AsyncMock()
        self.download_image = AsyncMock(return_value=Path("/tmp/fake.png"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


@pytest.fixture
def temp_db(tmp_path: Path) -> Iterator[DataStore]:
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
    try:
        yield store
    finally:
        store.close()


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
    fake_client.create_project.return_value = MagicMock(
        project_id="project-abc", title="Test Project"
    )
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
    fake_client.create_project.return_value = MagicMock(
        project_id="project-abc", title="Test Project"
    )
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
    fake_client.generate_video.return_value = _completed_video_result("media-vid-123")

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
async def test_worker_t2v_recording_failure_does_not_fail_task(temp_db: DataStore) -> None:
    """A credit-spent video that succeeds must stay 'completed' even if the
    post-success data-layer recording raises — recording is best-effort."""
    repo = QueueRepository(temp_db)
    task = repo.enqueue_task(
        task_id="task-rec-fail",
        profile_name="default",
        task_type="t2v",
        payload={"prompt": "cinematic camera movement", "aspect": "16:9"},
    )

    worker = FlowWorker("default", str(temp_db.path))
    fake_client = FakeFlowApiClient()
    fake_client.generate_video.return_value = _completed_video_result("media-vid-rec")

    failing_recorder = MagicMock()
    failing_recorder.record_completed_video.side_effect = RuntimeError("DB write failed")

    with (
        patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake_client),
        patch("gflow_cli.worker.daemon.OperationRecorder", return_value=failing_recorder),
    ):
        await worker.process_task(task)

    updated = repo.get_task("task-rec-fail")
    assert updated is not None
    assert updated.status == "completed"
    assert updated.flow_media_id == "media-vid-rec"
    assert updated.error is None
    worker.close()


@pytest.mark.asyncio
async def test_worker_applies_tool_specs_to_prompt(
    temp_db: DataStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tool_specs in the payload must expand the prompt before generation —
    mirroring the CLI --tool flag. Previously they were packed but never applied,
    making the MCP `tools` parameter a silent no-op."""
    repo = QueueRepository(temp_db)
    task = repo.enqueue_task(
        task_id="task-tool",
        profile_name="default",
        task_type="t2i",
        payload={"prompt": "a cat", "tool_specs": ["creative-director"], "count": 1},
    )

    worker = FlowWorker("default", str(temp_db.path))
    fake_client = FakeFlowApiClient()
    fake_client.create_project.return_value = MagicMock(project_id="p", title="T")
    fake_client.generate_image.return_value = FakeGeneratedImage(media_name="m")

    def _fake_apply(text: str, specs: tuple[str, ...], *, category: str, quiet: bool):
        return (f"EXPANDED::{text}", text, None)

    monkeypatch.setattr("gflow_cli.worker.daemon.apply_tool_option", _fake_apply)

    with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake_client):
        await worker.process_task(task)

    req = fake_client.generate_image.call_args.kwargs["req"]
    assert req.prompt == "EXPANDED::a cat"
    worker.close()


@pytest.mark.asyncio
async def test_worker_applies_tool_specs_to_video_prompt(
    temp_db: DataStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tool_specs must also expand the prompt on the video path (_build_video_request)."""
    repo = QueueRepository(temp_db)
    task = repo.enqueue_task(
        task_id="task-tool-vid",
        profile_name="default",
        task_type="t2v",
        payload={"prompt": "a dog", "aspect": "16:9", "tool_specs": ["creative-director"]},
    )

    worker = FlowWorker("default", str(temp_db.path))
    fake_client = FakeFlowApiClient()
    fake_client.generate_video.return_value = _completed_video_result("media-vid-tool")

    def _fake_apply(text: str, specs: tuple[str, ...], *, category: str, quiet: bool):
        return (f"EXPANDED::{text}", text, None)

    monkeypatch.setattr("gflow_cli.worker.daemon.apply_tool_option", _fake_apply)

    with patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake_client):
        await worker.process_task(task)

    req = fake_client.generate_video.call_args.kwargs["req"]
    assert req.prompt == "EXPANDED::a dog"
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
    fake_client.create_project.return_value = MagicMock(project_id="proj", title="Test Project")
    fake_client.generate_image.side_effect = [
        FakeGeneratedImage(media_name="img-1"),
        FakeGeneratedImage(media_name="img-2"),
    ]

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


@pytest.mark.asyncio
async def test_worker_loop_reraises_cancellation(temp_db: DataStore) -> None:
    """Cancelling the worker loop must propagate asyncio.CancelledError instead
    of swallowing it, so cooperative cancellation works on daemon shutdown.

    Regression for SonarCloud python:S7497 (cancellation must be re-raised).
    """
    worker = FlowWorker("default", str(temp_db.path))
    # No pending tasks, so the loop parks in `await asyncio.sleep(...)`.
    loop_task = asyncio.create_task(worker.start())
    await asyncio.sleep(0.05)

    loop_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await loop_task

    worker.close()
