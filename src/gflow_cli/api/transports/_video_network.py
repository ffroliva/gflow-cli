"""Entity interception, status polling and clip download.

A part of `VideoGenerationMixin`, moved from `ui_automation_video.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api import routes
from gflow_cli.api.transports._common import (
    expired_link_hint,
    get_signed_media,
    recoverable_clip_hint,
)
from gflow_cli.api.transports._video_common import (
    _GENERATION_ROUTE_RE,
    ENTITY_ATTACH_DRIFT_HINT,
    VIDEO_GENERATE_ROUTES,
    VIDEO_STATUS_ROUTE,
    _JsonObj,
    _JsonObjList,
    _summarize_request_image_inputs,
)
from gflow_cli.api.video import VideoStatus, parse_video_status
from gflow_cli.errors import (
    AuthExpiredError,
    MediaDownloadError,
    TransportTimeoutError,
    WireFormatError,
)
from gflow_cli.storage import AnyPath, storage_path, write_asset_async

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

log = structlog.get_logger(__name__)


class FlowNetworkMixin:
    """Entity interception, status polling and clip download."""

    _page: Page | None
    _setup_done: bool
    _generate_lock: asyncio.Lock
    _out_dir: Path | None
    _deferred_park_pending: bool

    if TYPE_CHECKING:

        async def _park_composer_page(self, page: Any, *, event: str) -> None: ...
        async def park_deferred_page(self) -> None: ...

        async def _enter_editor(
            self,
            page: Page,
            out_dir: Path | None = None,
            *,
            project_id: str | None = None,
            project_name: str | None = None,
        ) -> None: ...
        async def _send_prompt(
            self,
            page: Page,
            prompt_text: str,
            out_dir: Path | None = None,
            format_prompt: bool = False,
        ) -> None: ...
        @classmethod
        async def _dismiss_blocking_overlays(
            cls,
            page: Page,
            out_dir: Path | None = None,
        ) -> bool: ...

    @contextlib.asynccontextmanager
    async def _intercept_reference_entities(
        self,
        page: Page,
        expected_entities: set[str],
    ) -> AsyncGenerator[None, None]:
        """Register a route handler to strip unrequested referenceEntities from outgoing requests.

        This prevents 'poisoned' character entities from smuggling themselves into unrelated
        image/video generation runs.
        """
        import json

        async def intercept_generation_request(route: Any) -> None:
            req_obj = route.request
            # Ran-at-all signal (#620): absence of the `finally` emission below
            # means no handler observed the submit.
            had_reference_entities = False
            modified = False
            outcome = "forwarded"
            try:
                post_data = req_obj.post_data
                body: dict[str, Any] = (
                    cast(dict[str, Any], json.loads(post_data)) if post_data else {}
                )
                if not post_data:
                    outcome = "empty_post_data"

                if "requests" in body and isinstance(body["requests"], list):
                    requests_list = cast(list[dict[str, Any]], body["requests"])
                    for item in requests_list:
                        if "referenceEntities" in item:
                            had_reference_entities = True
                            refs = item["referenceEntities"]
                            if isinstance(refs, list):
                                refs_list = cast(list[dict[str, Any]], refs)
                                filtered_refs: list[dict[str, Any]] = []
                                for r in refs_list:
                                    if "entityId" in r:
                                        ent_id = r["entityId"]
                                        if isinstance(ent_id, str) and ent_id in expected_entities:
                                            filtered_refs.append(r)

                                if len(filtered_refs) != len(refs_list):
                                    item["referenceEntities"] = filtered_refs
                                    modified = True

                                if not filtered_refs:
                                    item.pop("referenceEntities", None)
                                    modified = True

                if modified:
                    # Kept alongside batch_request_intercepted, though strictly
                    # subsumed by it: the #615 thread publicly told the reporter to
                    # grep their logs for THIS event name. Removing it would break
                    # instructions already given to an outside contributor.
                    log.info(
                        "ui_automation.batch_request_modified",
                        url=req_obj.url,
                        reason="stripped unrequested referenceEntities",
                        expected_entities=list(expected_entities),
                    )
                    await route.continue_(
                        post_data=json.dumps(body, separators=(",", ":"), ensure_ascii=False)
                    )
                else:
                    await route.continue_()
            except Exception as exc:
                outcome = f"error: {type(exc).__name__}"
                log.warning(
                    "ui_automation.batch_request_modify_failed",
                    url=req_obj.url,
                    error=str(exc),
                )
                await route.continue_()
            finally:
                # `finally`, not the happy path: an emission the decode-error
                # branch skips would make absence ambiguous again.
                log.info(
                    "ui_automation.batch_request_intercepted",
                    url=req_obj.url,
                    had_reference_entities=had_reference_entities,
                    modified=modified,
                    outcome=outcome,
                    expected_entities=list(expected_entities),
                )

        # CONTEXT, not page: these requests are Web-Worker-delegated in the current
        # Flow cohort, so `page.route` cannot see them at all (#615). The capture path
        # already recorded this — see ui_automation.py "page-level network capture is
        # dead in this cohort".
        #
        # ponytail: context-wide routing is only safe because every generation entry
        # point runs under `self._generate_lock`, so exactly one handler is registered
        # at a time. Playwright dispatches to the newest matching handler and this one
        # never calls `route.fallback()`, so if generation is ever parallelised across
        # the page pool (`Settings.concurrency` accepts up to 16), one run would filter
        # another run's request against the wrong `expected_entities` — stripping
        # legitimate references. Scope the handler per-page before parallelising; note
        # `route.request` has no owning page for Worker-delegated requests, so that
        # scoping needs a different key.
        await page.context.route(_GENERATION_ROUTE_RE, intercept_generation_request)
        try:
            yield
        finally:
            try:
                await page.context.unroute(_GENERATION_ROUTE_RE, intercept_generation_request)
            except Exception:
                pass

    @staticmethod
    def _attach_video_response_listener(page: Page) -> tuple[_JsonObjList, Any]:
        """Register a `page.on('response')` listener for the three
        batchAsyncGenerateVideo* routes (spec §2.1). Returns `(captured, handler)`
        — the caller awaits `captured` after submitting the prompt and MUST
        `page.remove_listener('response', handler)` in a `finally` (the Page is
        pooled and persistent; an un-removed handler leaks across calls).
        Registered synchronously before `_send_prompt` so a fast response is
        never missed.

        The captured `body` is kept for parsing only — it carries
        `remainingCredits` and media UUIDs and MUST NOT be logged.
        """
        captured: _JsonObjList = []

        async def on_response(response: Any) -> None:
            if not any(route in response.url for route in VIDEO_GENERATE_ROUTES):
                return
            try:
                body = await response.json()
            except Exception as e:
                log.warning("ui_automation_video.generate_parse_failed", error=str(e))
                return
            captured.append({"status": response.status, "url": response.url, "body": body})
            # Proof that the attached images actually made it into the request
            # (the user saw the UI start generating before the upload spinner
            # cleared). Parse the REQUEST post_data and log only counts +
            # mediaId prefixes (UUIDs, not secrets) — never the token/credits.
            inputs = _summarize_request_image_inputs(response.request)
            captured[-1]["image_inputs"] = inputs
            log.info(
                "ui_automation_video.generate_captured",
                status=response.status,
                url=response.url,
                image_inputs=inputs,
            )

        page.on("response", on_response)
        return captured, on_response

    @staticmethod
    def _attach_status_response_listener(page: Page) -> tuple[_JsonObjList, Any]:
        """Register a `page.on('response')` listener for the status route. Flow's
        SPA polls `batchCheckAsyncVideoGenerationStatus` itself while a
        generation runs; this captures that traffic. Returns `(captured, handler)`
        — the caller MUST `page.remove_listener('response', handler)` in a
        `finally`. Attached BEFORE `_send_prompt` so no early status response is
        missed (spec §5.5)."""
        captured: _JsonObjList = []

        async def on_response(response: Any) -> None:
            if VIDEO_STATUS_ROUTE not in response.url:
                return
            try:
                body = await response.json()
            except Exception as e:
                log.warning("ui_automation_video.status_parse_failed", error=str(e))
                return
            captured.append({"status": response.status, "url": response.url, "body": body})

        page.on("response", on_response)
        return captured, on_response

    @staticmethod
    def _scan_for_terminal_status(
        captured_status: _JsonObjList,
        media_name: str,
    ) -> tuple[VideoStatus | None, str | None]:
        """Scan all captured status responses for a terminal VideoStatus.

        Returns ``(terminal_status, last_seen_status_string)``. Raises
        ``AuthExpiredError`` on HTTP 401. Skips responses for other media.
        """
        terminal: VideoStatus | None = None
        last_status: str | None = None
        for response in captured_status:
            if response.get("status") == 401:
                raise AuthExpiredError(
                    detail=(
                        "batchCheckAsyncVideoGenerationStatus returned HTTP 401"
                        " — session expired mid-poll"
                    ),
                    status=401,
                    route="video:status",
                )
            try:
                status = parse_video_status(response.get("body") or {}, media_id=media_name)
            except ValueError:
                continue  # this response is for other media — skip
            last_status = status.status
            if status.is_terminal:
                terminal = status
        return terminal, last_status

    @staticmethod
    async def _nudge_tab_if_stalled(
        page: Page,
        captured_status: _JsonObjList,
        seen_count: int,
        last_progress: float,
        nudged: bool,
        stall_nudge_s: float,
        media_name: str,
    ) -> tuple[int, float, bool]:
        """Update stall-detection bookkeeping and nudge the tab once on stall.

        Returns the updated ``(seen_count, last_progress, nudged)`` tuple.
        """
        if len(captured_status) != seen_count:
            return len(captured_status), time.monotonic(), nudged
        if not nudged and time.monotonic() - last_progress > stall_nudge_s:
            # Distinguish "Flow never polled the status route at all" from
            # "Flow stalled mid-run".
            event = (
                "ui_automation_video.poll_no_status_traffic"
                if seen_count == 0
                else "ui_automation_video.poll_stall_nudge"
            )
            log.warning(event, media_name=media_name, status_responses_seen=seen_count)
            try:
                await page.bring_to_front()
            except Exception as e:
                log.debug("ui_automation_video.bring_to_front_failed", error=str(e))
            nudged = True
        return seen_count, last_progress, nudged

    @staticmethod
    async def _poll_video_status(
        page: Page,
        captured_status: _JsonObjList,
        media_name: str,
        *,
        timeout_s: float = 600.0,
        poll_interval_s: float = 2.0,
        stall_nudge_s: float = 120.0,
    ) -> VideoStatus:
        """Read terminal status from Flow's own captured status traffic.

        `captured_status` is the list filled by `_attach_status_response_listener`.
        Each tick scans the WHOLE list for a terminal status of `media_name`
        (Flow appends chronologically; a terminal status is the last it emits) —
        no early `break`, so a terminal entry is never skipped. Returns the
        `VideoStatus` once terminal; the caller maps a FAILED status to a typed
        error (spec §7).

        If Flow stops polling (a backgrounded tab can throttle its timers) the
        captured list stops growing; after `stall_nudge_s` with no new capture
        this brings the page to the foreground ONCE and keeps waiting (spec
        §5.5). Raises `TransportTimeoutError` (``maybe``/``reconcile``, with the media id)
        only at the hard `timeout_s` deadline: the submit already happened (#1201).
        """
        deadline = time.monotonic() + timeout_s
        last_status: str | None = None
        seen_count = len(captured_status)
        last_progress = time.monotonic()
        nudged = False
        while time.monotonic() < deadline:
            terminal, last_status = VideoGenerationMixin._scan_for_terminal_status(
                captured_status, media_name
            )
            if terminal is not None:
                log.info(
                    "ui_automation_video.poll_terminal",
                    media_name=media_name,
                    status=terminal.status,
                )
                return terminal
            seen_count, last_progress, nudged = await VideoGenerationMixin._nudge_tab_if_stalled(
                page, captured_status, seen_count, last_progress, nudged, stall_nudge_s, media_name
            )
            await asyncio.sleep(poll_interval_s)
        cause = (
            "Flow never polled the status route"
            if seen_count == 0
            else "Flow stopped polling before a terminal status"
        )
        msg = (
            f"no terminal status for {media_name!r} within {timeout_s:.0f}s — "
            f"{seen_count} status response(s) seen, last status: {last_status}. {cause}."
        )
        # #1201: this runs after the submit, so the clip may still finish and bill.
        raise TransportTimeoutError(msg, next_action="reconcile", operation_id=media_name)

    def _clip_dir(self, out_dir: Path | None) -> Path:
        """Where a downloaded clip goes on either host: per-call, then the client's
        ``--out-dir``, then the documented ``tmp/`` (#986)."""
        return out_dir or self._out_dir or Path("tmp")

    async def _download_video(
        self,
        media_id: str,
        out_dir: Path | None,
        page: Any,
    ) -> AnyPath:
        """Download a generated video to local disk or cloud storage.

        Calls ``media.getMediaUrlRedirect?name=<media_id>`` which 302s to a
        signed GCS URL; Playwright follows the redirect automatically.

        When the transport's ``_storage_uri`` is set the video is uploaded to
        the configured cloud backend; otherwise it is written to ``out_dir``.
        """
        from urllib.parse import urlsplit  # noqa: PLC0415 - local to the raise path

        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415 - cycle
            _is_allowed_download_host,  # pyright: ignore[reportPrivateUsage]
        )

        url = routes.media_download_url(media_id)
        # Redirects are followed here, unlike the migrated download's `max_redirects=0`:
        # this route IS a redirect (`getMediaUrlRedirect` 302s to signed GCS). The posture
        # the migrated arm gets from refusing redirects is recovered below by checking
        # where the chain actually landed, since the hop target is Flow's to choose and
        # ours to verify.
        resp = await get_signed_media(
            page,
            url,
            media_id=media_id,
            route="media.getMediaUrlRedirect",
            max_redirects=5,
            remediation=recoverable_clip_hint(media_id),
            retryable=False,  # billed: a re-run bills again (#941)
        )
        if resp.status >= 400:
            # #941: the clip is done and billed; this is a failed DOWNLOAD, recoverable.
            raise MediaDownloadError(
                media_id=media_id,
                detail=(
                    f"video download returned HTTP {resp.status} for {media_id!r} "
                    f"via media.getMediaUrlRedirect"
                ),
                status=resp.status,
                route="media.getMediaUrlRedirect",
                remediation_hint=expired_link_hint(media_id),
                retryable=False,
            )
        if not _is_allowed_download_host(resp.url):
            raise WireFormatError(
                detail=(
                    "video download: refusing bytes from "
                    f"{urlsplit(resp.url).hostname!r} (not an allowed Google host)"
                ),
                route="media.getMediaUrlRedirect",
            )
        return await self._save_clip(media_id, await resp.body(), out_dir)

    async def _save_clip(self, media_id: str, body: bytes, out_dir: Path | None) -> AnyPath:
        """Store a downloaded clip on either host (#1003): under the configured cloud
        ``storage_uri`` when set, otherwise in ``_clip_dir(out_dir)``."""
        from gflow_cli import paths as _paths

        # Both branches use the id as a filename; checked here, not upstream (#1004).
        _paths.validate_job_id(media_id)
        storage_uri: str | None = getattr(self, "_storage_uri", None)
        if storage_uri:
            from datetime import date

            key = f"videos/{date.today().isoformat()}/{media_id}.mp4"
            # output_dir fallback only used for key computation when cloud is active
            output_dir = getattr(self, "_output_dir", None) or Path("tmp")
            target: AnyPath = storage_path(storage_uri, output_dir, key)
        else:
            target = self._clip_dir(out_dir) / f"{media_id}.mp4"
        try:
            await write_asset_async(target, body)
        except Exception as exc:
            # The clip is already billed and still in Flow: type it so the recorder marks
            # the asset generated and the user is sent to the free recovery, not a re-run.
            raise MediaDownloadError(
                media_id=media_id,
                # Class name only, like get_signed_media: no bucket URI or home path in a
                # detail that is persisted to the catalog.
                detail=f"storing clip {media_id} failed ({type(exc).__name__})",
                remediation_hint=recoverable_clip_hint(media_id),
                retryable=False,  # billed: a re-run bills again (#941)
            ) from exc
        log.info(
            "ui_automation_video.video_saved",
            path=str(target),
            bytes=len(body),
            media_id=media_id,
        )
        return target

    @staticmethod
    def _collect_entity_ids_from_response_shape(body: dict[str, Any]) -> list[str]:
        """Extract entityIds from the live response shape.

        Path: ``media[].mediaMetadata.requestData.videoGenerationRequestData
                   .videoGenerationEntityInputs[].entityId``
        """
        ids: list[str] = []
        for media in cast(_JsonObjList, body.get("media") or []):
            meta = cast(_JsonObj, media.get("mediaMetadata") or {})
            req_data = cast(_JsonObj, meta.get("requestData") or {})
            vgrd = cast(_JsonObj, req_data.get("videoGenerationRequestData") or {})
            for e in cast(_JsonObjList, vgrd.get("videoGenerationEntityInputs") or []):
                entity_id = cast("str | None", e.get("entityId"))
                if entity_id:
                    ids.append(entity_id)
        return ids

    @staticmethod
    def _collect_entity_ids_from_request_shape(body: dict[str, Any]) -> list[str]:
        """Extract entityIds from the request-body shape (fallback).

        Path: ``requests[].referenceEntities[].entityId``
        """
        ids: list[str] = []
        for r in cast(_JsonObjList, body.get("requests") or []):
            for e in cast(_JsonObjList, r.get("referenceEntities") or []):
                entity_id = cast("str | None", e.get("entityId"))
                if entity_id:
                    ids.append(entity_id)
        return ids

    @staticmethod
    def _assert_entities_attached(generate_resp: dict[str, Any], *, expected: list[str]) -> None:
        """Defense-in-depth: confirm the character entities actually rode the wire.

        A UI attach miss would degrade to a text/image-only clip reported as a
        success. Raise loudly instead.

        The captured SUBMIT *response* echoes the accepted entities at:

            media[].mediaMetadata.requestData.videoGenerationRequestData
                  .videoGenerationEntityInputs[].entityId

        NOT ``requests[].referenceEntities`` — that is the *request* shape; the
        response re-keys it (verified against a live capture, 2026-06-06; an
        earlier version checked the request path against the response body and
        false-rejected every successful entity generation). The request-shape
        path is still accepted so the check also works against a request body.
        """
        if not expected:
            return
        body = cast(_JsonObj, generate_resp.get("body") or {})
        got = VideoGenerationMixin._collect_entity_ids_from_response_shape(
            body
        ) + VideoGenerationMixin._collect_entity_ids_from_request_shape(body)
        missing = [e for e in expected if e not in got]
        if missing:
            raise WireFormatError(
                detail=(
                    f"character entities not echoed in submit response (expected "
                    f"{expected}, got {got}); entity attach failed - refusing to "
                    f"report success"
                ),
                route="video:batchAsyncGenerateVideoReferenceImages",
                remediation_hint=ENTITY_ATTACH_DRIFT_HINT,
                discovery={"entity_attach_context": "video"},
            )
