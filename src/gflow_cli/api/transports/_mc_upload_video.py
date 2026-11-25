"""Toolbar local video upload for ``flow.google.com`` (video extend).

The same toolbar ``+ -> upload`` chooser as an image upload (:func:`toolbar_upload`), fed
a staged hardlink/copy inside a private temporary directory with a run-unique filename
(so a clip of up to 1 GB is never loaded into memory), and observed for Flow's
``POST /upload/v1/flow/upload/video/<uuid>`` JSON reply. Flow's one-time "rights to use
this video" dialog is never accepted for the user.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api.transports._mc_common import (
    UUID_RE,
    _unique_display_name,  # pyright: ignore[reportPrivateUsage]
    toolbar_upload,
)
from gflow_cli.errors import MediaUploadRejectedError

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)

VIDEO_UPLOAD_PATH_MARKER = "/upload/v1/flow/upload/video/"
#: Upload budget = base + size / rate. The rate is deliberately conservative (1 MiB/s),
#: so a 1 GB clip (the cap) is given ~19 minutes rather than a fixed 3 that only fits a
#: fast link.
VIDEO_UPLOAD_BASE_S = 120.0
VIDEO_UPLOAD_BYTES_PER_S = 1024 * 1024


def video_upload_timeout_s(size_bytes: int) -> float:
    """How long the upload of a ``size_bytes`` clip may take before it is a timeout."""
    return VIDEO_UPLOAD_BASE_S + size_bytes / VIDEO_UPLOAD_BYTES_PER_S


@contextlib.asynccontextmanager
async def _staged_video_path(video_path: Path, display_name: str) -> AsyncGenerator[Path]:
    """Expose *video_path* under *display_name* inside a private temporary directory.

    ``mkdtemp`` creates the directory readable by the user only. A hardlink costs no copy
    and no memory; across filesystems an off-thread copy is used instead.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="gflow-vid-"))
    staged = tmp_dir / display_name
    try:
        try:
            os.link(video_path, staged)
        except OSError:
            await asyncio.to_thread(shutil.copyfile, video_path, staged)
        yield staged
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp_dir, True)


def _parse_upload_reply(raw: str, *, project_id: str) -> str | None:
    """The clip's media id (``batchexecute`` coordinates) from Flow's upload JSON.

    Measured live 2026-11-24: Flow's REST upload reply names the two UUIDs opposite to
    ``batchexecute`` (``Zzl0ze`` / ``fZytfe``): REST ``media.workflowId`` is ``Zzl0ze``
    ``rec[2]``, the id in ``/project/<pid>/edit/<media_id>``. The REST ``mediaId`` must
    also be a UUID, or the reply shape has moved.
    """
    try:
        data: Any = json.loads(raw.strip())
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    data_dict = cast("dict[str, Any]", data)
    media_obj = data_dict.get("media")
    media_dict = cast("dict[str, Any]", media_obj) if isinstance(media_obj, dict) else {}
    rec0_workflow_id = data_dict.get("mediaId") or media_dict.get("name")
    rec2_media_id = media_dict.get("workflowId") or data_dict.get("workflowId")
    if (
        not isinstance(rec2_media_id, str)
        or not UUID_RE.fullmatch(rec2_media_id)
        or rec2_media_id.lower() == project_id.lower()
    ):
        return None
    if not isinstance(rec0_workflow_id, str) or not UUID_RE.fullmatch(rec0_workflow_id):
        return None
    return rec2_media_id


async def _read_video_reply(response: Any) -> tuple[int, str | None] | None:
    """A final reply, or ``None`` to keep waiting.

    Flow's resumable upload answers an initial 200 with an empty body, then a final 200
    carrying the JSON. An error status is final with no body.
    """
    status = int(getattr(response, "status", 0) or 0)
    if status >= 400:
        return status, ""
    if status != 200:
        return None
    try:
        raw = await response.text()
    except Exception:  # noqa: BLE001 - an unreadable chunk is not the final reply
        return None
    return (status, raw) if raw.strip().startswith("{") else None


async def upload_video(page: Page, project_id: str, video_path: Path) -> str:
    """Upload a validated *video_path* into *project_id*; return the clip's media id."""
    display_name = _unique_display_name(video_path)
    route = "flow:upload/video"
    async with _staged_video_path(video_path, display_name) as staged:
        status, raw = await toolbar_upload(
            page,
            str(staged),
            is_upload_url=lambda url: VIDEO_UPLOAD_PATH_MARKER in url,
            read_reply=_read_video_reply,
            timeout_s=video_upload_timeout_s(video_path.stat().st_size),
            noun="video",
            request_name="video upload",
            route=route,
            timeout_hint=(
                "If nothing left the page, check flow.google.com for a one-time video "
                "upload-terms confirmation dialog or file-type notice. If the request left "
                "the page, the link may be slower than the budget allows; re-run. Nothing "
                "was spent."
            ),
        )
    if status != 200:
        raise MediaUploadRejectedError(
            detail=f"migrated host: Flow video upload answered HTTP {status}",
            status=status,
            route=route,
        )
    media_id = _parse_upload_reply(raw or "", project_id=project_id)
    if media_id is None:
        raise MediaUploadRejectedError(
            detail="migrated host: Flow video upload answered 200 without mediaId/workflowId",
            status=status,
            route=route,
        )
    log.info("migrated.video_uploaded", media_id=media_id, status=status)
    return media_id
