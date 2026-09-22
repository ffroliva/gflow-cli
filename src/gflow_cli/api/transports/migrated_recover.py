"""Recover a clip that finished and billed but whose download failed (#865, #871).

When the migrated composer's 20 s URL grace expires, the generation has already
happened and the credit is already spent, but no signed media URL was ever observed —
so the run raises ``WireFormatError`` (exit 7) and the asset is left in the project with
``local_path: null``. Until now the only recovery the CLI offered was to pay for the
generation again.

The retrieval path measured on 2026-09-17 (see
``docs/superpowers/spikes/2026-09-17-stranded-clip-recovery.md``):

* ``flow.google.com/project/<project_id>/edit/<media_id>`` is a real per-clip route —
  both ids are already in the local catalog, so nothing has to be searched for;
* loading it makes the app fetch that clip's status on ``as29s``, whose reply carries a
  freshly signed ``flow-content.google/video/<workflow_id>`` URL. Both the rpc and the
  decoder (:func:`generation_record`) already exist for the in-run download path;
* the bytes are checked against the record's own ``size_bytes``.

**Why the size check is not optional.** The listing's own media token also answers
``=m18`` and ``=m22`` with valid MP4s — 360p and 720p transcodes of the same clip. They
pass an ``ftyp`` magic-byte test and are *not* the asset the user paid for. Saving one
silently is the failure class of #281, so a mismatch is a hard error here, never a
warning.

This module deliberately touches no DOM: no selector, no menu, no tile. Flow's grid is
virtualized (~13 rendered tiles against 183 records on the measured project), so a
tile-based approach cannot reach an older clip at all, and every text label it would
need is locale-dependent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import structlog

from gflow_cli.api.transports._common import get_signed_media, retry_the_recovery_hint
from gflow_cli.api.transports.batchexecute import generation_record, parse_frames
from gflow_cli.exceptions import WireFormatError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Page

log = structlog.get_logger(__name__)

#: The per-clip editor route. Both ids come from the catalog row the orphan already has.
MIGRATED_CLIP_URL = "https://flow.google.com/project/{project_id}/edit/{media_id}"

#: How long to wait for the app's own ``as29s`` status call after the route loads.
#: Measured: the reply lands within a few seconds: this is slack, not a poll budget.
SIGNED_URL_WAIT_S = 45.0

_ROUTE = "batchexecute:as29s"


@dataclass(frozen=True)
class RecoveredClip:
    """One clip retrieved from an already-billed generation."""

    media_id: str
    workflow_id: str
    path: Path
    bytes: int


def _clip_url(project_id: str, media_id: str) -> str:
    return MIGRATED_CLIP_URL.format(project_id=project_id, media_id=media_id)


async def _await_signed_record(
    page: Page, *, media_id: str, project_id: str, wait_s: float
) -> dict[str, Any]:
    """Open the clip's route and return the first status record Flow reports for it.

    The app fetches the clip's status on ``as29s`` as the route loads; that reply is the
    only source of a freshly signed URL, because signed URLs expire and are therefore
    never persisted in the catalog.
    """
    found: dict[str, Any] = {}

    async def on_response(response: Any) -> None:
        if found or "batchexecute" not in str(getattr(response, "url", "")):
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 - an aborted/streamed body is not our frame
            return
        _collect(text, media_id=media_id, into=found)

    page.on("response", on_response)
    try:
        log.info("migrated.recover_navigate", project_id=project_id, media_id=media_id)
        await page.goto(
            _clip_url(project_id, media_id), wait_until="domcontentloaded", timeout=90_000
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_s
        while not found and loop.time() < deadline:
            await page.wait_for_timeout(500)
    finally:
        page.remove_listener("response", on_response)

    if not found:
        # #877: the class-default remediation on WireFormatError is written for
        # generation payloads — "check request payload parameters or retry with a
        # simpler prompt text" — and this command has neither a payload nor a prompt.
        # An image id no longer reaches here (the service refuses on the catalog row),
        # so the remaining causes really are about the clip.
        raise WireFormatError(
            detail=(
                f"migrated host: no signed media URL for {media_id} within "
                f"{wait_s:.0f}s of opening its clip route."
            ),
            remediation_hint=(
                "Flow did not report a media URL for this clip. Open the project in "
                "Flow and check the clip is still there — a clip moved to trash, or a "
                "media id belonging to a different project, both look like this. If it "
                "is visible and playable, re-run once: the record is fetched as the "
                "route loads and a slow load can miss the window."
            ),
            route=_ROUTE,
        )
    return found


def _collect(text: str, *, media_id: str, into: dict[str, Any]) -> None:
    """Record the first frame that is a status record for *media_id* with a URL."""
    for rpcid, payload in parse_frames(text):
        try:
            record = generation_record(rpcid, payload)
        except WireFormatError:
            # Most frames on a project load are not generation records. A frame that
            # does not decode is not evidence about THIS media id.
            continue
        if record.media_id != media_id or not record.video_url:
            continue
        into.update(
            url=record.video_url,
            size=record.size_bytes,
            workflow_id=record.workflow_id,
            rpcid=rpcid,
        )
        return


async def _fetch_verified(page: Page, *, url: str, expected: int | None, media_id: str) -> bytes:
    """GET the signed URL and prove the bytes are this clip's original."""
    from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415 - import cycle
        _is_allowed_download_host,  # pyright: ignore[reportPrivateUsage]
    )

    if not _is_allowed_download_host(url):
        raise WireFormatError(
            detail=(
                "migrated host: refusing to download from "
                f"{urlsplit(url).hostname!r} (not an allowed Google host)"
            ),
            route=_ROUTE,
        )
    # No redirects: an open redirect on the CDN must not rebound the request
    # elsewhere — same posture as every other download in this codebase.
    resp = await get_signed_media(
        page,
        url,
        media_id=media_id,
        route="flow-content.google",
        max_redirects=0,
        # Not the recover-it-with-`data download` hint: this IS `data download`.
        remediation=retry_the_recovery_hint(media_id),
    )
    if resp.status >= 300:
        raise WireFormatError(
            detail=f"migrated host: signed media URL returned HTTP {resp.status}",
            status=resp.status,
            route="flow-content.google",
            remediation_hint=(
                "Flow's signed link for this clip may have expired — they are short-lived. "
                "Re-run this command: it opens the clip's own route, so Flow issues a fresh "
                "link. No credits are spent."
            ),
        )
    body = await resp.body()
    _verify(body, expected=expected, media_id=media_id)
    return body


async def recover_clip(
    page: Page,
    *,
    project_id: str,
    media_id: str,
    out_dir: Path,
    wait_s: float = SIGNED_URL_WAIT_S,
) -> RecoveredClip:
    """Retrieve an already-generated clip by media id and write it to ``out_dir``.

    Spends no credits: the generation already happened. Raises
    :class:`WireFormatError` when the app never reports a signed URL for this media id,
    or when the bytes it serves are not the recorded asset.
    """
    found = await _await_signed_record(
        page, media_id=media_id, project_id=project_id, wait_s=wait_s
    )
    body = await _fetch_verified(
        page, url=str(found["url"]), expected=found.get("size"), media_id=media_id
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{media_id}.mp4"
    path.write_bytes(body)
    workflow_id = str(found["workflow_id"])
    log.info(
        "migrated.recover_download",
        media_id=media_id,
        workflow_id=workflow_id,
        path=str(path),
        bytes=len(body),
    )
    return RecoveredClip(media_id=media_id, workflow_id=workflow_id, path=path, bytes=len(body))


def _verify(body: bytes, *, expected: int | None, media_id: str) -> None:
    """Both checks, because either alone passes on the wrong file.

    ``ftyp`` alone accepts the 360p/720p transcodes Flow also serves for this clip; a
    size match alone would accept a same-length non-MP4 body.
    """
    if body[4:8] != b"ftyp":
        raise WireFormatError(
            detail=(
                f"migrated host: the media URL for {media_id} did not return an MP4 "
                f"(ftyp magic); got {body[:4].hex()} ({len(body)} B)"
            ),
            route=_ROUTE,
        )
    if expected is not None and len(body) != expected:
        raise WireFormatError(
            detail=(
                f"migrated host: {media_id} downloaded {len(body)} B but Flow reports "
                f"{expected} B for this clip — refusing to save a partial file or a "
                "lower-resolution transcode"
            ),
            route=_ROUTE,
        )
