# pyright: reportPrivateUsage=false
"""Direct text-to-video (``YhhmEf``) and image-to-video (``eb1hJf``) on flow.google.com.

One attempt covers the submit and everything after it: the ``jwpduf`` poll, the ``as29s``
read with the bare workflow id, and the original mp4's download. All of it runs after the
billed POST was sent, so any failure there is ``Indeterminate`` and the clip is never
resubmitted (spike 2026-11-27, "Direct video works end to end").
"""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from typing import TYPE_CHECKING

import httpx
import structlog

from gflow_cli.api.generation_route import (
    VIDEO_FAMILY_MODELS,
    selected_video_request,
    video_target,
)
from gflow_cli.api.submit_policy import not_sent
from gflow_cli.api.transports._common import recoverable_clip_hint
from gflow_cli.api.transports._ui_common import _is_allowed_download_host
from gflow_cli.api.transports.batchexecute import generation_record, parse_frames
from gflow_cli.api.transports.direct_http._gateway import (
    SubmitAttempt,
    _client,
    _post_request,
)
from gflow_cli.api.transports.direct_http._parse import (
    existing_record,
    failed_record,
    original_video_url,
)
from gflow_cli.api.transports.direct_http._session import (
    _prompt_box_settings,
    _session_headers,
    _shell,
)
from gflow_cli.api.transports.frame_builder import build_i2v_body, build_rpc_body, build_t2v_body
from gflow_cli.api.transports.migrated_recover import _verify
from gflow_cli.api.video import VideoResult, VideoStarted, VideoStatus
from gflow_cli.errors import (
    MediaDownloadError,
    NetworkError,
    TransportTimeoutError,
    WireFormatError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from playwright.async_api import Page

    from gflow_cli.api import flow_wire
    from gflow_cli.api.generation_route import VideoWireTarget
    from gflow_cli.api.submit_policy import RecaptchaTokenSource
    from gflow_cli.api.transports.batchexecute import GenerationRecord
    from gflow_cli.api.transports.migrated_composer import ClipSaver
    from gflow_cli.api.video import GenerateVideoRequest, VideoStartedCallback

log = structlog.get_logger(__name__)

_RECONCILE_HINT = (
    "Flow may have accepted this video request before the error, so gflow did not resend "
    "it. Check the project in Flow for the new clip before running the command again."
)
_T2V = ("YhhmEf", "VIDEO_GENERATION", "video submit", _RECONCILE_HINT)
_I2V = ("eb1hJf", "VIDEO_GENERATION", "video submit", _RECONCILE_HINT)

#: A start frame as the ``eb1hJf`` item carries it: the uploaded media id and its crop box.
StartFrame = tuple[str, list[float | int | None]]


async def run_direct_video(
    *,
    tokens: RecaptchaTokenSource,
    page: Page,
    profile_dir: Path,
    project_id: str,
    request: GenerateVideoRequest,
    target: VideoWireTarget,
    start: StartFrame | None,
    save: ClipSaver,
    download: bool,
    poll_timeout_s: float,
    poll_interval_s: float,
    on_started: VideoStartedCallback | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    on_before_submit: Callable[[], None] | None = None,
) -> VideoResult:
    """One direct video: mint, send once, poll, read, download; all inside one attempt.

    ``request`` is the resolved request (a bare t2v's carries the account's selection, read
    before the mint by :func:`selected_target`) and ``target`` its measured key.
    """
    started = time.perf_counter()
    spec = _T2V if start is None else _I2V
    attempt = SubmitAttempt(spec, profile_dir, tokens, on_before_submit=on_before_submit)
    async with _client(http_transport) as client, attempt:
        shell = await _shell(client, page, profile_dir)
        token = await attempt.mint()
        body = _submit_body(request, target, start, project_id, token, shell.at)
        log.info("direct.frame_built", rpcid=spec[0], wire_key=target.wire_key)
        source = f"/project/{project_id}"
        headers = await _session_headers(page, profile_dir)
        response = await attempt.send(client, _post_request(spec[0], source, shell, headers, body))
        submitted = _submitted(spec[0], response.text)
        log.info(
            "direct.video_post", status=response.status_code, workflow_id=submitted.workflow_id
        )
        await _notify(on_started, submitted, project_id)
        read = _reader(client, page, profile_dir, shell, source)
        record = await _poll(read, submitted.workflow_id, poll_timeout_s, poll_interval_s)
        local_path: Path | None = None
        if download and record.is_done:
            local_path = await _download(client, read, record, save)
    log.info("direct.video", total_ms=int((time.perf_counter() - started) * 1000))
    return VideoResult(
        status=VideoStatus(
            media_id=record.media_id,
            status=(
                "MEDIA_GENERATION_STATUS_SUCCESSFUL"
                if record.is_done
                else "MEDIA_GENERATION_STATUS_FAILED"
            ),
            error_message=None if record.is_done else record.failure_message,
        ),
        local_path=local_path,
        project_id=project_id,
        flow_operation_id=record.workflow_id,
        workflow_id=record.workflow_id,
        transport="direct_http",
        sent_request=request,
    )


async def selected_target(
    page: Page, request: GenerateVideoRequest
) -> tuple[GenerateVideoRequest, VideoWireTarget]:
    """A bare t2v's request and key, from the account's selection; ``NotSent`` otherwise.

    Read before the balance read and the mint, on ``page`` once armed (on flow.google.com);
    ``localStorage`` is shared by every page of the context, a re-armed one included. An
    unread selection, an unmapped or rejected one or an unmeasured tuple sends nothing: the
    retry loop runs the UI path, which applies the selection itself.
    """
    selection = await _prompt_box_settings(page)
    resolved = None if selection is None else selected_video_request(request, selection)
    target = None if resolved is None else video_target(resolved)
    if resolved is None or target is None:
        log.info(
            "direct.t2v_key_unresolved",
            selection_read=selection is not None,
            mapped=selection is not None
            and selection.get("videoModelFamilyId") in VIDEO_FAMILY_MODELS,
        )
        raise not_sent("t2v_key_unresolved")
    log.info(
        "direct.t2v_key_resolved",
        wire_key=target.wire_key,
        model=None if resolved.model is None else resolved.model.value,
        source="local_storage",
    )
    return resolved, target


def _submit_body(
    request: GenerateVideoRequest,
    target: VideoWireTarget,
    start: StartFrame | None,
    project_id: str,
    token: str,
    at: str,
) -> str:
    ids = (str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper())
    batch_id = str(uuid.uuid4()).upper()
    if start is None:
        return build_t2v_body(
            prompt=request.prompt,
            wire_key=target.wire_key,
            aspect_slot=target.aspect_slot,
            duration=request.duration,
            project_id=project_id,
            recaptcha_token=token,
            at_token=at,
            request_ids=ids,
            batch_id=batch_id,
        )
    return build_i2v_body(
        prompt=request.prompt,
        wire_key=target.wire_key,
        aspect_slot=target.aspect_slot,
        start_media_id=start[0],
        crop=start[1],
        project_id=project_id,
        recaptcha_token=token,
        at_token=at,
        request_ids=ids,
        batch_id=batch_id,
    )


def _reader(
    client: httpx.AsyncClient,
    page: Page,
    profile_dir: Path,
    shell: flow_wire.Tokens,
    source: str,
) -> Callable[[str, object], Awaitable[str]]:
    """A read after the send: cookies are read live, and a failure is past the send."""

    async def read(rpcid: str, payload: object) -> str:
        read_headers = await _session_headers(page, profile_dir)
        body = build_rpc_body(rpcid, payload, shell.at)
        reply = await client.send(_post_request(rpcid, source, shell, read_headers, body))
        if reply.status_code != 200:
            raise NetworkError(
                detail=f"batchexecute {rpcid} answered HTTP {reply.status_code}",
                status=reply.status_code,
                route=f"batchexecute:{rpcid}",
                next_action="reconcile",
                remediation_hint=_RECONCILE_HINT,
            )
        return reply.text

    return read


def _submitted(rpcid: str, text: str) -> GenerationRecord:
    payloads = [payload for frame, payload in parse_frames(text) if frame == rpcid]
    return generation_record(rpcid, payloads[0] if payloads else None)


async def _notify(
    on_started: VideoStartedCallback | None, record: GenerationRecord, project_id: str
) -> None:
    if on_started is None:
        return
    started = VideoStarted(
        media_id=record.media_id,
        project_id=project_id,
        flow_operation_id=record.workflow_id,
        workflow_id=record.workflow_id,
    )
    pending = on_started(started)
    if inspect.isawaitable(pending):
        await pending


async def _poll(
    read: Callable[[str, object], Awaitable[str]],
    workflow_id: str,
    timeout_s: float,
    interval_s: float,
) -> GenerationRecord:
    """``jwpduf`` until the record is done or failed; a stub (no media id) is neither."""
    deadline = time.monotonic() + timeout_s
    polls = 0
    while True:
        text = await read("jwpduf", [None, None, [[workflow_id]]])
        polls += 1
        record = existing_record(text, "jwpduf", workflow_id) or failed_record(
            text, "jwpduf", workflow_id
        )
        if record is not None and (record.is_done or record.is_failed):
            log.info("direct.video_poll", polls=polls, done=record.is_done)
            return record
        if time.monotonic() >= deadline:
            raise TransportTimeoutError(
                detail=f"direct video {workflow_id} not finished after {timeout_s:.0f} s of jwpduf",
                route="batchexecute:jwpduf",
                next_action="reconcile",
                remediation_hint=_RECONCILE_HINT,
            )
        await asyncio.sleep(interval_s)


async def _download(
    client: httpx.AsyncClient,
    read: Callable[[str, object], Awaitable[str]],
    record: GenerationRecord,
    save: ClipSaver,
) -> Path:
    """Fetch the record's original mp4 (:func:`_fetch_clip`) and store it."""
    return await save(record.media_id, await _fetch_clip(client, read, record))


async def _fetch_clip(
    client: httpx.AsyncClient,
    read: Callable[[str, object], Awaitable[str]],
    record: GenerationRecord,
) -> bytes:
    """Read the original mp4's URL with ``as29s`` and fetch it: allowlisted host, no cookies,
    no redirects, size checked against the record."""
    media_id, workflow_id = record.media_id, record.workflow_id
    text = await read("as29s", [workflow_id])
    url = original_video_url(text, workflow_id)
    if url is None:
        raise WireFormatError(
            detail=f"as29s named no original mp4 for workflow {workflow_id}",
            route="batchexecute:as29s",
            remediation_hint=recoverable_clip_hint(media_id),
        )
    if not _is_allowed_download_host(url):
        raise WireFormatError(
            detail=(
                f"video download: refusing {httpx.URL(url).host!r} (not an allowed Google host)"
            ),
            route="batchexecute:as29s",
            remediation_hint=recoverable_clip_hint(media_id),
        )
    reply = await client.get(url)
    if reply.status_code != 200:
        raise MediaDownloadError(
            media_id=media_id,
            detail=f"video download answered HTTP {reply.status_code} for {media_id!r}",
            status=reply.status_code,
            route="flow-content.google",
            remediation_hint=recoverable_clip_hint(media_id),
            retryable=False,
        )
    expected = existing_record(text, "as29s", workflow_id)
    size = expected.size_bytes if expected is not None and expected.size_bytes else None
    _verify(reply.content, expected=size or record.size_bytes, media_id=media_id)
    return reply.content
