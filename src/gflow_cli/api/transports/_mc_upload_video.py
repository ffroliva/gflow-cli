"""Toolbar local video upload for ``flow.google.com`` (video edit / extend).

Mirrors ``_mc_upload_frame.py`` for video assets: opens Flow's toolbar ``+ -> upload``
file chooser, hands Playwright a staged hardlink/copy inside a private ``0700``
temporary directory with a run-unique filename (avoiding loading up to 1 GB into RAM),
and parses Flow's ``POST /upload/v1/flow/upload/video/<uuid>`` JSON reply for
``(mediaId, media.workflowId)``.

Never auto-accepts Flow's one-time "rights to use this video" confirmation dialog:
detects an unexpected dialog with no upload POST leaving the page and fails with
:data:`VIDEO_UPLOAD_TERMS_REMEDIATION`.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api.transports._mc_common import (
    DIALOG,
    FRAME_PICKER_OPEN_S,
    MENU_ITEM,
    TOOLBAR_ADD,
    UPLOAD_MENU_ITEM,
    UUID_RE,
    _unique_display_name,
)
from gflow_cli.errors import MediaUploadRejectedError, UiSelectorDriftError

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)

VIDEO_UPLOAD_PATH_MARKER = "/upload/v1/flow/upload/video/"
VIDEO_UPLOAD_S = 180.0

#: What to do about Flow's one-time "rights to use this video" dialog.
VIDEO_UPLOAD_TERMS_REMEDIATION = (
    "Open the project on flow.google.com, upload any video by hand, "
    "and accept the one-time 'rights to use this video' dialog. gflow "
    "does not accept it for you: it affirms that YOU hold the rights "
    "to what you upload. It appears once per account — after that, "
    "video uploads work unattended. If you see a different dialog instead "
    "(an error, a quota notice, a re-login), that is the one blocking "
    "the upload. Nothing was spent; re-run when done."
)


@dataclass(frozen=True, slots=True)
class UploadedVideo:
    """Identity returned by Flow's toolbar video upload."""

    media_id: str
    workflow_id: str
    display_name: str
    #: Query term Flow lists the uploaded video under in its ``@`` picker (Flow strips
    #: the filename extension for uploaded videos, unlike uploaded images).
    query_name: str


@contextlib.asynccontextmanager
async def _staged_video_path(video_path: Path, display_name: str) -> AsyncGenerator[Path]:
    """Expose *video_path* under *display_name* inside a private ``0700`` temp dir.

    Uses a hardlink when on the same filesystem (zero copy, zero RAM), falling back
    to an off-thread file copy across filesystems.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="gflow-vid-"))
    with contextlib.suppress(OSError):
        os.chmod(tmp_dir, 0o700)
    staged = tmp_dir / display_name
    try:
        try:
            os.link(video_path, staged)
        except OSError:
            await asyncio.to_thread(shutil.copyfile, video_path, staged)
        yield staged
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp_dir, True)


def _parse_upload_reply(raw: str, *, project_id: str) -> tuple[str, str] | None:
    """Extract ``(media_id, workflow_id)`` in ``batchexecute`` coordinates from Flow's upload JSON.

    Measured live 2026-11-24: Flow's REST upload reply names the two UUIDs opposite to
    ``batchexecute`` (``Zzl0ze`` / ``jIps6`` / ``fZytfe``):
      - REST ``media.workflowId`` == ``Zzl0ze`` ``rec[2]`` (``/project/<pid>/edit/<media_id>``)
      - REST ``mediaId`` / ``media.name`` == ``Zzl0ze`` ``rec[0]`` (submit ``entry[0][1]``)
    We normalize here so callers everywhere share one ``(media_id=rec[2], workflow_id=rec[0])``
    coordinate system.
    """
    stripped = raw.strip()
    if not stripped.startswith("{"):
        return None
    try:
        data: Any = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    data_dict = cast("dict[str, Any]", data)
    media_obj = data_dict.get("media")
    media_dict: dict[str, Any] = (
        cast("dict[str, Any]", media_obj) if isinstance(media_obj, dict) else {}
    )
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
    return rec2_media_id, rec0_workflow_id


async def _open_upload_chooser(page: Page) -> Any:
    """Open toolbar ``+ -> upload`` and return Playwright's FileChooser."""
    add = page.locator(TOOLBAR_ADD).first
    if not await add.count():
        raise UiSelectorDriftError(
            detail=(
                "migrated host: the toolbar add button (mat-icon 'add' outside "
                "flow-prompt-box) is missing (host=migrated)"
            ),
        )
    await add.click(timeout=5000)
    item = page.locator(UPLOAD_MENU_ITEM).first
    try:
        await item.wait_for(state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000))
    except Exception as exc:
        raise UiSelectorDriftError(
            detail=(
                "migrated host: the toolbar menu rendered no upload entry "
                f"({MENU_ITEM} with the 'upload' ligature) within "
                f"{FRAME_PICKER_OPEN_S:.0f}s (host=migrated)"
            ),
        ) from exc
    try:
        async with page.expect_file_chooser(timeout=int(FRAME_PICKER_OPEN_S * 1000)) as fc_info:
            await item.click(timeout=4000)
        return await fc_info.value
    except Exception as exc:
        raise UiSelectorDriftError(
            detail=(
                "migrated host: the upload entry opened no file chooser within "
                f"{FRAME_PICKER_OPEN_S:.0f}s (host=migrated)"
            ),
        ) from exc


async def _raise_upload_timeout(page: Page, *, dialogs_before: int, sent: bool, route: str) -> None:
    try:
        opened = await page.locator(DIALOG).count() > dialogs_before
    except Exception:  # noqa: BLE001 - diagnostic probe must not mask upload timeout
        opened = False
    if opened and not sent:
        raise MediaUploadRejectedError(
            detail=(
                "migrated host: a dialog opened after the video file was chosen and no "
                "video upload request left the page — most likely Flow's one-time "
                "video upload-terms confirmation (host=migrated)"
            ),
            route=route,
            remediation_hint=VIDEO_UPLOAD_TERMS_REMEDIATION,
        ) from None
    went_out = (
        "the request left the page and Flow did not finish accepting it in time"
        if sent
        else "no video upload request ever left the page"
    )
    raise MediaUploadRejectedError(
        detail=(
            f"migrated host: no video upload reply within {VIDEO_UPLOAD_S:.0f}s "
            f"of choosing the file — {went_out}"
        ),
        route=route,
        remediation_hint=(
            "If nothing left the page, check flow.google.com for a one-time video "
            "upload-terms confirmation dialog or file-type notice. Nothing was spent."
        ),
    ) from None


def _verify_upload_response(
    status: int, raw: str, *, project_id: str, route: str
) -> tuple[str, str]:
    if status != 200:
        raise MediaUploadRejectedError(
            detail=f"migrated host: Flow video upload answered HTTP {status}",
            status=status,
            route=route,
        )
    parsed = _parse_upload_reply(raw, project_id=project_id)
    if parsed is None:
        raise MediaUploadRejectedError(
            detail="migrated host: Flow video upload answered 200 without mediaId/workflowId",
            status=status,
            route=route,
        )
    return parsed


def _make_video_upload_listeners(
    reply: asyncio.Future[tuple[int, str]],
) -> tuple[list[bool], Any, Any]:
    sent_flag = [False]

    def on_request(request: Any) -> None:
        if VIDEO_UPLOAD_PATH_MARKER in str(getattr(request, "url", "")):
            sent_flag[0] = True

    async def on_response(response: Any) -> None:
        url = str(getattr(response, "url", ""))
        if VIDEO_UPLOAD_PATH_MARKER not in url or reply.done():
            return
        status = int(getattr(response, "status", 0) or 0)
        if status >= 400:
            reply.set_result((status, ""))
            return
        if status != 200:
            return
        try:
            raw = await response.text()
        except Exception:  # noqa: BLE001 - unreadable chunk/reply
            return
        # Flow's resumable video upload sends an initial 200 with an empty body, then
        # a final 200 carrying JSON {"mediaId": ..., "media": {"workflowId": ...}}.
        if raw.strip().startswith("{") and not reply.done():
            reply.set_result((status, raw))

    return sent_flag, on_request, on_response


async def upload_video(
    page: Page,
    project_id: str,
    video_path: Path,
    *,
    timeout_s: float = VIDEO_UPLOAD_S,
) -> UploadedVideo:
    """Validate and upload *video_path* into *project_id* via the toolbar uploader."""
    from gflow_cli.api._flow_workspace import validate_video_file  # noqa: PLC0415

    await validate_video_file(video_path)
    display_name = _unique_display_name(video_path)
    query_name = Path(display_name).stem
    route = "flow:upload/video"
    reply: asyncio.Future[tuple[int, str]] = asyncio.get_running_loop().create_future()
    sent_flag, on_request, on_response = _make_video_upload_listeners(reply)
    page.on("request", on_request)
    page.on("response", on_response)
    try:
        chooser = await _open_upload_chooser(page)
        dialogs_before = await page.locator(DIALOG).count()
        async with _staged_video_path(video_path, display_name) as staged:
            await chooser.set_files(str(staged))
            try:
                status, raw = await asyncio.wait_for(reply, timeout=timeout_s)
            except TimeoutError:
                await _raise_upload_timeout(
                    page, dialogs_before=dialogs_before, sent=sent_flag[0], route=route
                )
                raise  # pragma: no cover - unreachable, keeps type checker narrow
        media_id, workflow_id = _verify_upload_response(
            status, raw, project_id=project_id, route=route
        )
        log.info(
            "migrated.video_uploaded",
            media_id=media_id,
            workflow_id=workflow_id,
            status=status,
        )
        return UploadedVideo(
            media_id=media_id,
            workflow_id=workflow_id,
            display_name=display_name,
            query_name=query_name,
        )
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)
