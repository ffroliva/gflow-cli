"""URL constants — pin the wire surface so we notice if it changes."""

from __future__ import annotations

import pytest

from flow_cli.api import routes


def test_upload_image_url() -> None:
    assert routes.UPLOAD_IMAGE == "https://aisandbox-pa.googleapis.com/v1/flow/uploadImage"


def test_generate_video_url() -> None:
    assert (
        routes.GENERATE_VIDEO
        == "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText"
    )


def test_check_status_url() -> None:
    assert routes.CHECK_VIDEO_STATUS == (
        "https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus"
    )


def test_archive_workflow_base() -> None:
    assert routes.ARCHIVE_WORKFLOW_BASE == "https://aisandbox-pa.googleapis.com/v1/flowWorkflows"


def test_create_project_url() -> None:
    assert routes.CREATE_PROJECT == "https://labs.google/fx/api/trpc/project.createProject"


def test_media_download_url_appends_name() -> None:
    url = routes.media_download_url("abc-123")
    assert "name=abc-123" in url
    assert "getMediaUrlRedirect" in url


def test_batch_generate_images_url() -> None:
    assert routes.batch_generate_images_url("abc-123") == (
        "https://aisandbox-pa.googleapis.com/v1/projects/abc-123/flowMedia:batchGenerateImages"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "../evil",
        "/leading-slash",
        "with\\backslash",
        "",
    ],
)
def test_batch_generate_images_url_rejects_path_traversal(bad: str) -> None:
    with pytest.raises(ValueError):
        routes.batch_generate_images_url(bad)
