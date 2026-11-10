"""Fakes shared by the CLI tests: a stub image client, a GeneratedImage builder and a
video recorder that records calls."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from gflow_cli.api.dto import GeneratedImage


def _make_generated_image(
    *,
    media_name: str = "img-uuid-1",
    seed: int = 12345,
    model: str = "NARWHAL",
    width: int = 768,
    height: int = 1344,
) -> GeneratedImage:
    return GeneratedImage(
        media_name=media_name,
        workflow_id="wf-1",
        seed=seed,
        prompt="a cat",
        model_name_type=model,
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://flow-content.google/x?Signature=abc",
        dimensions=(width, height),
    )


def _make_t2i_client(
    *,
    images: list[GeneratedImage] | None = None,
    payload: bytes = b"\x89PNG\r\n\x1a\n",
) -> MagicMock:
    """Stub FlowApiClient: create_project + generate_image[s_batch] + download_image.

    ``payload`` is the bytes written by the fake ``download_image``. The fake
    mirrors the real client (``api/client.py``): write bytes, then pass through
    ``correct_image_extension`` so the CLI sees the post-rename path. Pass a
    JPEG header to exercise the issue-#96 codepath end-to-end.
    """
    from gflow_cli.paths import correct_image_extension

    images = images or [_make_generated_image()]
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.create_project = AsyncMock(
        return_value=MagicMock(project_id="proj-1", title="gflow-cli t2i")
    )
    client.generate_image = AsyncMock(return_value=images[0])
    client.generate_images_batch = AsyncMock(return_value=images)

    async def _fake_download(image: GeneratedImage, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(payload)
        return correct_image_extension(out_path)

    client.download_image = AsyncMock(side_effect=_fake_download)
    return client


class FakeVideoRecorder:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.completed: list[dict] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def record_started_video(self, **kwargs):
        self.started.append(kwargs)

    def record_completed_video(self, **kwargs):
        self.completed.append(kwargs)
