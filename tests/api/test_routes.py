"""URL constants — pin the wire surface so we notice if it changes."""

from __future__ import annotations

import pytest

from gflow_cli.api import routes


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
        # Original denylist regression cases — must still be rejected.
        "../evil",
        "/leading-slash",
        "with\\backslash",
        "",
        # Percent-encoded slash (GCP/nginx L7 LBs normalize %2F → /).
        "proj%2F..%2Fevil",
        # Unicode lookalikes for '/'.
        "proj／evil",  # U+FF0F FULLWIDTH SOLIDUS
        "proj∕evil",  # U+2215 DIVISION SLASH
        "proj⧸evil",  # U+29F8 BIG SOLIDUS
        # CRLF / NUL byte injection.
        "proj\nevil",
        "proj\revil",
        "proj\x00evil",
        # URL metacharacters that would corrupt path semantics.
        "proj?injected=param",
        "proj#frag",
        # Whitespace-only is truthy but invalid.
        "   ",
        # Length cap: 129 chars exceeds the 128 limit.
        "a" * 129,
    ],
)
def test_batch_generate_images_url_rejects_invalid_project_id(bad: str) -> None:
    with pytest.raises(ValueError):
        routes.batch_generate_images_url(bad)


@pytest.mark.parametrize(
    "good",
    [
        "abc-123",
        "a",
        "A1",
        "my-project-id",
        "a" * 128,  # exactly at the length cap
    ],
)
def test_batch_generate_images_url_accepts_valid_project_id(good: str) -> None:
    url = routes.batch_generate_images_url(good)
    assert url == (
        f"https://aisandbox-pa.googleapis.com/v1/projects/{good}/flowMedia:batchGenerateImages"
    )
