"""Task C1 — submit-boundary reconciliation checkpoints.

The generation boundary must emit a typed :class:`GenerationCheckpoint` at two
phases so later queue tasks (C3/C5) can persist recovery state:

* ``submit_attempted`` — emitted immediately BEFORE the credit-spending gesture
  (so a crash mid-submit is recoverable as "may have spent credits").
* ``remote_started`` — emitted when the authoritative Flow handle is first
  observed (video: operation name; image: generated media/workflow UUIDs).

A ``None`` observer is zero behaviour change. The seam records ONLY handle
identifiers + phase — never prompts, headers, cookies, or signed URLs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import GeneratedImage, GenerationCheckpoint
from gflow_cli.api.image import Aspect, GenerateImageRequest
from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStarted, VideoStatus
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError
from gflow_cli.worker.daemon import FlowWorker
from gflow_cli.worker.queue import QueueRepository, recover_processing

if TYPE_CHECKING:
    from collections.abc import Iterator

    from gflow_cli.api.video import VideoStartedCallback


_IMAGE = GeneratedImage(
    media_name="media-uuid-1",
    workflow_id="wf-uuid-1",
    seed=1,
    prompt="a warrior",
    model_name_type="NARWHAL",
    aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
    fife_url="https://flow-content.google/image/abc?Signature=SECRET",
    dimensions=(768, 1376),
)


class _FakeImageTransport:
    """Records the submit gesture onto a shared timeline."""

    name = "fake"

    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def setup(self, profile_dir: Path, *, page: object = None) -> None: ...
    async def refresh_auth(self) -> None: ...
    async def teardown(self) -> None: ...

    async def generate_images(
        self, *, project_id: str | None, request: GenerateImageRequest
    ) -> list[GeneratedImage]:
        self._events.append("gesture")
        return [_IMAGE]


class _FakeVideoTransport:
    """Fires ``on_started`` with a real Flow operation handle after the gesture."""

    name = "fake-video"

    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def setup(self, profile_dir: Path, *, page: object = None) -> None: ...
    async def refresh_auth(self) -> None: ...
    async def teardown(self) -> None: ...
    async def generate_images(
        self, *, project_id: str | None, request: GenerateImageRequest
    ) -> list[GeneratedImage]:
        return [_IMAGE]

    async def generate_video(
        self,
        *,
        request: GenerateVideoRequest,
        project_id: str | None = None,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
        download: bool = True,
        on_started: VideoStartedCallback | None = None,
    ) -> VideoResult:
        self._events.append("gesture")
        if on_started is not None:
            res = on_started(
                VideoStarted(
                    media_id="vid-media-1",
                    project_id=project_id,
                    flow_operation_id="operations/op-abc-123",
                )
            )
            if res is not None:
                await res
        return VideoResult(
            status=VideoStatus(media_id="vid-media-1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            local_path=None,
            project_id=project_id,
            flow_operation_id="operations/op-abc-123",
        )


def _client(tmp_path: Path, transport: object) -> FlowApiClient:
    c = FlowApiClient(profile_dir=tmp_path / "prof", transport=transport)  # type: ignore[arg-type]
    c.transport = transport  # type: ignore[assignment]
    c._page = MagicMock()
    c._mint_recaptcha_token = AsyncMock(return_value="tok")  # type: ignore[method-assign]
    return c


async def test_image_batch_emits_submit_then_remote_started(tmp_path: Path) -> None:
    events: list[str] = []
    observed: list[GenerationCheckpoint] = []
    transport = _FakeImageTransport(events)
    client = _client(tmp_path, transport)

    def observe(cp: GenerationCheckpoint) -> None:
        events.append(cp.phase)
        observed.append(cp)

    await client.generate_images_batch(
        project_id="proj-1",
        req=GenerateImageRequest(prompt="a warrior", aspect=Aspect.PORTRAIT),
        count=1,
        on_checkpoint=observe,
    )

    # submit_attempted MUST precede the credit-spending gesture.
    assert events == ["submit_attempted", "gesture", "remote_started"]
    assert observed[0].phase == "submit_attempted"
    # remote_started carries the authoritative image handle (media/workflow UUIDs).
    remote = observed[-1]
    assert remote.phase == "remote_started"
    assert remote.media_ids == ("media-uuid-1",)
    assert remote.workflow_ids == ("wf-uuid-1",)


async def test_video_emits_submit_then_remote_started_with_operation(tmp_path: Path) -> None:
    events: list[str] = []
    observed: list[GenerationCheckpoint] = []
    transport = _FakeVideoTransport(events)
    client = _client(tmp_path, transport)

    def observe(cp: GenerationCheckpoint) -> None:
        events.append(cp.phase)
        observed.append(cp)

    await client.generate_video(
        req=GenerateVideoRequest(prompt="a warrior walks"),
        project_id="proj-1",
        download=False,
        on_checkpoint=observe,
    )

    assert events == ["submit_attempted", "gesture", "remote_started"]
    remote = observed[-1]
    assert remote.phase == "remote_started"
    # video handle == the batchAsyncGenerateVideo* operation name.
    assert remote.operation_id == "operations/op-abc-123"
    assert remote.media_ids == ("vid-media-1",)


async def test_none_observer_is_zero_behaviour_change(tmp_path: Path) -> None:
    events: list[str] = []
    transport = _FakeImageTransport(events)
    client = _client(tmp_path, transport)

    result = await client.generate_images_batch(
        project_id="proj-1",
        req=GenerateImageRequest(prompt="a warrior", aspect=Aspect.PORTRAIT),
        count=1,
    )

    assert events == ["gesture"]
    assert result == [_IMAGE]


# --- Task C5: cancellation + crash recovery persist truthful outcomes ---------
#
# A submitted-but-uncertain task must NEVER be silently failed and NEVER
# resubmitted: pre-submit cancel -> failed, post-submit cancel -> indeterminate,
# restart with a persisted handle -> reconcile without a new submit.


@pytest.fixture
def temp_db(tmp_path: Path) -> Iterator[DataStore]:
    store = DataStore.open(tmp_path / "recovery.db")
    store.conn.execute(
        "INSERT INTO profiles(name, profile_dir, first_seen_at) "
        "VALUES ('default', 'C:/profiles/default', '2026-06-24T00:00:00Z')"
    )
    try:
        yield store
    finally:
        store.close()


class _FakeClient:
    """Async-context client stub for driving process_task."""

    def __init__(self, **_: Any) -> None:
        self.create_project = AsyncMock()
        self.generate_image = AsyncMock()
        self.generate_images_batch = AsyncMock()
        self.download_image = AsyncMock(return_value=Path("/tmp/x.png"))

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False


class _CountingClient:
    """Recovery reconcile-hook stub; proves recovery never resubmits."""

    def __init__(self) -> None:
        self.submit_count = 0

    async def generate_image(self, **_: Any) -> None:
        self.submit_count += 1

    async def generate_video(self, **_: Any) -> None:
        self.submit_count += 1


async def test_cancel_before_submit_is_safe_failure(temp_db: DataStore) -> None:
    repo = QueueRepository(temp_db)
    repo.enqueue_task("t1", "default", "t2i", {"prompt": "x", "aspect": "1:1", "count": 1})
    claimed = repo.claim_next_pending("default", "worker:default:1")
    assert claimed is not None

    worker = FlowWorker("default", str(temp_db.path))
    fake = _FakeClient()
    # Cancelled during project creation — BEFORE any submit_attempted checkpoint.
    fake.create_project.side_effect = asyncio.CancelledError

    with (  # noqa: PT012
        patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker.process_task(claimed)

    task = repo.get_task("t1")
    assert task is not None
    assert task.status == "failed"
    checkpoint = repo.read_checkpoint("t1")
    assert checkpoint is not None
    assert checkpoint["phase"] == "claimed"
    assert checkpoint["may_have_spent"] is False
    worker.close()


async def test_cancel_after_submit_without_handle_is_indeterminate(temp_db: DataStore) -> None:
    repo = QueueRepository(temp_db)
    repo.enqueue_task(
        "t2", "default", "t2i", {"prompt": "x", "aspect": "1:1", "count": 1, "project_id": "proj-1"}
    )
    claimed = repo.claim_next_pending("default", "worker:default:1")
    assert claimed is not None

    worker = FlowWorker("default", str(temp_db.path))
    fake = _FakeClient()

    async def _submit_then_cancel(
        *, on_checkpoint: object = None, **_: Any
    ) -> list[GeneratedImage]:
        # Mirror the real client: emit submit_attempted, then the gesture is
        # cancelled before a handle (remote_started) is ever observed.
        if on_checkpoint is not None:
            on_checkpoint(GenerationCheckpoint(phase="submit_attempted"))  # type: ignore[operator]
        raise asyncio.CancelledError

    fake.generate_image = _submit_then_cancel  # type: ignore[assignment]

    with (  # noqa: PT012
        patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker.process_task(claimed)

    task = repo.get_task("t2")
    assert task is not None
    assert task.status == "indeterminate"
    checkpoint = repo.read_checkpoint("t2")
    assert checkpoint is not None
    assert checkpoint["phase"] == "submit_attempted"
    assert checkpoint["may_have_spent"] is True
    worker.close()


async def test_cancel_persistence_failure_still_raises_cancelled(temp_db: DataStore) -> None:
    """A DataStoreError raised while persisting the interrupted-task state
    (read_checkpoint / mark_interrupted -> update_task_status) must be logged
    and swallowed — the original CancelledError must still propagate, never
    be replaced by the persistence failure (Important finding on C5)."""
    repo = QueueRepository(temp_db)
    repo.enqueue_task(
        "t2b",
        "default",
        "t2i",
        {"prompt": "x", "aspect": "1:1", "count": 1, "project_id": "proj-1"},
    )
    claimed = repo.claim_next_pending("default", "worker:default:1")
    assert claimed is not None

    worker = FlowWorker("default", str(temp_db.path))
    fake = _FakeClient()

    async def _submit_then_cancel(
        *, on_checkpoint: object = None, **_: Any
    ) -> list[GeneratedImage]:
        if on_checkpoint is not None:
            on_checkpoint(GenerationCheckpoint(phase="submit_attempted"))  # type: ignore[operator]
        raise asyncio.CancelledError

    fake.generate_image = _submit_then_cancel  # type: ignore[assignment]

    def _boom(*_: Any, **__: Any) -> None:
        raise DataStoreError(detail="disk full")

    worker.repo.update_task_status = _boom  # type: ignore[method-assign]

    with (  # noqa: PT012
        patch("gflow_cli.worker.daemon.FlowApiClient", return_value=fake),
        pytest.raises(asyncio.CancelledError),
    ):
        await worker.process_task(claimed)

    worker.close()


def test_restart_reconciles_handle_without_resubmitting(temp_db: DataStore) -> None:
    repo = QueueRepository(temp_db)
    repo.enqueue_task("t3", "default", "t2v", {"prompt": "a walk"})
    repo.update_task_status("t3", "processing")
    repo.update_checkpoint(
        "t3",
        claimant="worker:default:1",
        phase="remote_started",
        may_have_spent=True,
        operation_id="operations/1",
        project_id="proj-1",
        media_ids=("vid-1",),
    )

    client = _CountingClient()
    counts = recover_processing(repo, "default", client)

    # Recovery must NOT resubmit a task that already has a remote handle.
    assert client.submit_count == 0
    task = repo.get_task("t3")
    assert task is not None
    assert task.status == "indeterminate"
    # Handle is preserved in the checkpoint for a future live-page reconcile (F1).
    checkpoint = repo.read_checkpoint("t3")
    assert checkpoint is not None
    assert checkpoint["operation_id"] == "operations/1"
    assert counts["indeterminate"] == 1


def test_recover_pre_submit_task_is_failed(temp_db: DataStore) -> None:
    repo = QueueRepository(temp_db)
    repo.enqueue_task("t4", "default", "t2v", {"prompt": "a walk"})
    repo.update_task_status("t4", "processing")
    repo.update_checkpoint("t4", claimant="worker:default:1", phase="claimed", may_have_spent=False)

    counts = recover_processing(repo, "default")

    task = repo.get_task("t4")
    assert task is not None
    assert task.status == "failed"
    assert counts["failed"] == 1
