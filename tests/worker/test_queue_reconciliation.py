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

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import GeneratedImage, GenerationCheckpoint
from gflow_cli.api.image import Aspect, GenerateImageRequest
from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStarted, VideoStatus

if TYPE_CHECKING:
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
