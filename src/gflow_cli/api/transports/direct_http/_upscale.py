# pyright: reportPrivateUsage=false
"""Direct 1080p video upscale (``p0UkFb``) on flow.google.com.

One attempt covers the submit and everything after it: the ``jwpduf`` poll of
``<workflow_id>_upsampled`` (found by its exact id), the ``as29s`` read with that id, the
original mp4's download and the 1080p dimension check. All of it runs after the POST was
sent, so a failure there is ``Indeterminate`` and is never resent. A 1080p export costs 0
credits (spike 2026-11-27, "Direct video works end to end").
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING, Any, NamedTuple, TypeGuard

import structlog

from gflow_cli.api.generation_route import Route, choose_upscale_route
from gflow_cli.api.submit_policy import DirectAttemptEnded, Indeterminate
from gflow_cli.api.transports.batchexecute import UNUSUAL_ACTIVITY_REASON
from gflow_cli.api.transports.direct_http._gateway import SubmitAttempt, _client, _post_request
from gflow_cli.api.transports.direct_http._session import (
    _proxy_configured,
    _session_headers,
    _shell,
)
from gflow_cli.api.transports.direct_http._video import _fetch_clip, _poll, _reader
from gflow_cli.api.transports.frame_builder import build_rpc_body
from gflow_cli.api.transports.migrated_video_upscale import _check_mp4_resolution
from gflow_cli.config import get_settings
from gflow_cli.errors import UpscaleUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import httpx
    from playwright.async_api import Page

    from gflow_cli.api.submit_policy import RecaptchaTokenSource, SubmitOutcome

log = structlog.get_logger(__name__)

_RECONCILE_HINT = (
    "Flow may have accepted this upscale before the error, so gflow did not resend it. "
    "Check the clip in Flow for the 1080p export before running the command again "
    "(a 1080p upscale spends no credits)."
)
_P0UKFB = ("p0UkFb", "VIDEO_GENERATION", "upscale submit", _RECONCILE_HINT)
_INVALID_ARGUMENT = 3
_UPSAMPLER_1080P = "veo_3_1_upsampler_1080p"
#: The ``_upsampled`` poll's budget, in seconds.
_POLL_TIMEOUT_S = 600.0
#: The wire's 16:9 slot, the only aspect a direct upscale was measured on.
_LANDSCAPE_SLOT = 2


class UpscaleSource(NamedTuple):
    """A catalogued clip the ``p0UkFb`` item names: its media id and workflow id."""

    media_id: str
    workflow_id: str


def upscale_source(
    media_id: str, scale: str, catalog_row: dict[str, Any] | None
) -> UpscaleSource | None:
    """The source for a direct upscale, or ``None`` for the menu path.

    ``choose_upscale_route`` decides from the local catalog row alone (no network read).
    """
    settings = get_settings()
    route = choose_upscale_route(
        scale,
        catalog_row,
        flow_host=settings.flow_host,
        proxy_configured=_proxy_configured(),
    )
    if route is not Route.DIRECT or catalog_row is None:
        return None
    return UpscaleSource(media_id, str(catalog_row["flow_workflow_id"]))


async def run_direct_upscale(
    *,
    tokens: RecaptchaTokenSource,
    page: Page,
    profile_dir: Path,
    project_id: str,
    source: UpscaleSource,
    poll_interval_s: float,
    http_transport: httpx.AsyncBaseTransport | None = None,
    on_before_submit: Callable[[], None] | None = None,
) -> bytes:
    """One direct 1080p upscale: mint, send once, poll, read, download, check; one attempt."""
    started = time.perf_counter()
    attempt = SubmitAttempt(_P0UKFB, profile_dir, tokens, on_before_submit=on_before_submit)
    async with _client(http_transport) as client, attempt:
        shell = await _shell(client, page, profile_dir)
        token = await attempt.mint()
        body = build_rpc_body("p0UkFb", _payload(source, project_id, token), shell.at)
        headers = await _session_headers(page, profile_dir)
        edit_path = f"/project/{project_id}/edit/{source.media_id}"
        try:
            await attempt.send(client, _post_request("p0UkFb", edit_path, shell, headers, body))
        except DirectAttemptEnded as ended:
            if _flow_refused(ended.outcome):
                why = f"{', '.join(ended.outcome.reasons)}, gRPC {ended.outcome.code}"
                raise _unavailable(source.media_id, why) from ended
            raise
        log.info("direct.upscale_post", media_id=source.media_id)
        read = _reader(client, page, profile_dir, shell, f"/project/{project_id}")
        record = await _poll(
            read, f"{source.workflow_id}_upsampled", _POLL_TIMEOUT_S, poll_interval_s
        )
        if not record.is_done:
            raise _unavailable(source.media_id, record.failure_message)
        data = await _fetch_clip(client, read, record)
        _check_mp4_resolution(data, "1080p")
    log.info(
        "direct.upscale",
        media_id=source.media_id,
        bytes=len(data),
        total_ms=int((time.perf_counter() - started) * 1000),
    )
    return data


def _payload(source: UpscaleSource, project_id: str, token: str) -> list[object]:
    """``[[item], ctx, [batch]]``; the item's 32 slots as the spike captured them."""
    item: list[object] = [None] * 32
    item[0] = [None, source.workflow_id]
    item[2] = _LANDSCAPE_SLOT
    item[4] = [None, source.media_id, None, None, str(uuid.uuid4()).upper()]
    item[6] = 2
    item[31] = _UPSAMPLER_1080P
    ctx: list[object] = [None, 22, None, None, None, project_id, None, None, None, None]
    ctx.append([token, 1])
    return [[item], ctx, [str(uuid.uuid4()).upper()]]


def _flow_refused(outcome: SubmitOutcome) -> TypeGuard[Indeterminate]:
    """HTTP 200 with Flow's error frame for ``p0UkFb`` naming reasons: Flow answered and
    refused this item. Not gRPC 3 (INVALID_ARGUMENT, our request) nor ``UNUSUAL_ACTIVITY``."""
    return (
        isinstance(outcome, Indeterminate)
        and outcome.status == 200
        and bool(outcome.reasons)
        and outcome.code != _INVALID_ARGUMENT
        and UNUSUAL_ACTIVITY_REASON not in outcome.reasons
    )


def _unavailable(media_id: str, why: str | None) -> UpscaleUnavailableError:
    return UpscaleUnavailableError(
        detail=f"Flow refused the 1080p upscale of {media_id} ({why or 'no reason given'})",
        route="batchexecute:p0UkFb",
        remediation_hint=(
            "Flow does not offer a 1080p upscale for this clip. Check the clip's download "
            "options in Google Flow."
        ),
    )
