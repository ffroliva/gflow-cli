"""Recover a clip that finished and billed but whose download failed (#865, #871).

A finished, billed clip can reach no file: its download fails, or the run ends before it.
The asset is then left in the project with ``local_path: null``. Since #1153 a normal run
uses this same route, through :func:`read_clip`, whenever its record names no clip URL.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import structlog

from gflow_cli.api import _engine
from gflow_cli.api.transports._common import get_signed_media, retry_the_recovery_hint
from gflow_cli.api.transports.batchexecute import generation_record, parse_frames
from gflow_cli.exceptions import WireFormatError
from gflow_cli.paths import validate_job_id

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Page

log = structlog.get_logger(__name__)

#: The per-clip editor route. Both ids come from the catalog row the orphan already has.
MIGRATED_CLIP_URL = "https://flow.google.com/project/{project_id}/edit/{media_id}"

#: How long to wait for the app's own ``as29s`` status call after the route loads.
#: Measured: the reply lands within a few seconds: this is slack, not a poll budget.
SIGNED_URL_WAIT_S = 45.0

#: Route loads within :data:`SIGNED_URL_WAIT_S` (#1484). ``as29s`` is sent once as the clip
#: route loads; if Chrome evicts that reply body, an already loaded page will not send a
#: second ``as29s`` without re-opening the route.
_SIGNED_URL_ATTEMPTS = 3

#: Timeout for the first load of the clip route; the wait budget starts after it.
_NAVIGATION_TIMEOUT_S = 90.0

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


@dataclass
class _As29sWatch:
    """Collects this clip's ``as29s`` record and notes which load lost its body."""

    media_id: str
    found: dict[str, Any] = field(default_factory=dict[str, Any])
    attempt: int = 0
    unreadable_on: int = 0
    #: The load each as29s request was sent on; a loss belongs to that load.
    sent_on: dict[Any, int] = field(default_factory=dict[Any, int])

    @property
    def waiting(self) -> bool:
        """Still no record, and the current load has not lost its ``as29s`` body."""
        return not self.found and self.unreadable_on != self.attempt

    def on_request(self, request: Any) -> None:
        if "as29s" in str(getattr(request, "url", "")):
            self.sent_on[request] = self.attempt

    async def on_response(self, response: Any) -> None:
        url = str(getattr(response, "url", ""))
        if self.found or "batchexecute" not in url:
            return
        load = self.sent_on.get(getattr(response, "request", None), self.attempt)
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 - an aborted/streamed body is not our frame
            # Any lost as29s counts: its request does not name the clip (live e2e, #1484).
            if "as29s" in url and load == self.attempt:
                self.unreadable_on = load
            return
        _collect(text, media_id=self.media_id, into=self.found)


async def _await_signed_record(
    page: Page, *, media_id: str, project_id: str, wait_s: float
) -> dict[str, Any]:
    """Open the clip's route and return the first status record Flow reports for it.

    The app fetches the clip's status on ``as29s`` as the route loads; that reply is the
    only source of a freshly signed URL, because signed URLs expire and are therefore
    never persisted in the catalog.
    """
    watch = _As29sWatch(media_id)
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline: float | None = None
    page.on("request", watch.on_request)
    page.on("response", watch.on_response)
    try:
        # Total time is capped at the first navigation's timeout plus ``wait_s``: the wait
        # starts once the route has loaded, and a reload spends what is left of it.
        while not watch.found and watch.attempt < _SIGNED_URL_ATTEMPTS:
            nav_timeout_s = _NAVIGATION_TIMEOUT_S
            if deadline is not None:
                nav_timeout_s = deadline - loop.time()
                if nav_timeout_s <= 0:
                    break
            watch.attempt += 1
            log.info(
                "migrated.recover_navigate",
                project_id=project_id,
                media_id=media_id,
                attempt=watch.attempt,
            )
            try:
                await page.goto(
                    _clip_url(project_id, media_id),
                    wait_until="domcontentloaded",
                    timeout=nav_timeout_s * 1000,
                )
            except _engine.retryable_engine_errors():  # patchright's Error is a separate class
                if deadline is None:
                    raise
                # A reload only has what is left of the wait; running out is "no URL".
                log.warning("migrated.recover_reload_failed", attempt=watch.attempt)
                break
            if deadline is None:
                deadline = loop.time() + wait_s
            # A healthy load keeps the whole remaining budget: re-opening the route would
            # cancel an as29s still in flight. Only an observed unreadable body reloads.
            while watch.waiting and loop.time() < deadline:
                await page.wait_for_timeout(500)
    finally:
        page.remove_listener("request", watch.on_request)
        page.remove_listener("response", watch.on_response)

    if not watch.found:
        # #877: the class-default remediation on WireFormatError is written for
        # generation payloads — "check request payload parameters or retry with a
        # simpler prompt text" — and this command has neither a payload nor a prompt.
        # An image id no longer reaches here (the service refuses on the catalog row),
        # so the remaining causes really are about the clip.
        raise WireFormatError(
            detail=(
                f"migrated host: no signed media URL for {media_id} after {watch.attempt} "
                f"load(s) of its clip route ({loop.time() - started:.0f}s including "
                f"navigation; {wait_s:.0f}s wait once the route loaded)."
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
    return watch.found


def _collect(text: str, *, media_id: str, into: dict[str, Any]) -> None:
    """Record the first frame that is a status record for *media_id* with a URL."""
    for rpcid, payload in parse_frames(text):
        # Only the clip route's own status reply: the project load's `Zzl0ze` listing
        # carries this clip's record too, with an unsigned lh3 URL that answers 302
        # (measured 2026-10-06, #948).
        if rpcid != "as29s":
            continue
        try:
            record = generation_record(rpcid, payload, media_id=media_id)
        except WireFormatError:
            # Most frames on a project load are not generation records. A frame that
            # does not decode is not evidence about THIS media id.
            continue
        if not record.video_url:
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


async def read_clip(
    page: Page, *, project_id: str, media_id: str, wait_s: float = SIGNED_URL_WAIT_S
) -> tuple[str, bytes]:
    """(workflow id, verified bytes) of a finished clip, read through its own route.

    The route replaces the page, so only call this once the generation is over. Raises
    :class:`WireFormatError` when no signed URL comes or the bytes are not the asset.
    """
    found = await _await_signed_record(
        page, media_id=media_id, project_id=project_id, wait_s=wait_s
    )
    body = await _fetch_verified(
        page, url=str(found["url"]), expected=found.get("size"), media_id=media_id
    )
    return str(found["workflow_id"]), body


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
    validate_job_id(media_id)  # it becomes the filename below (#1004)
    workflow_id, body = await read_clip(
        page, project_id=project_id, media_id=media_id, wait_s=wait_s
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{media_id}.mp4"
    path.write_bytes(body)
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
