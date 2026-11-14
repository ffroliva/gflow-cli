"""Batch response listeners, request logging, response polling and image downloads.

Mixed into `UiAutomationTransport` via `BatchCaptureMixin` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api.transports._ui_common import (
    _ALLOWED_DOWNLOAD_HOST_SUFFIXES,
    _is_allowed_download_host,
    _reference_field_count,
    _summarize_batch_request_body,
)
from gflow_cli.api.transports._ui_settings import GenerationSettingsMixin
from gflow_cli.api.transports.ui_automation_entities import (
    entity_ids_from_request_body as _entity_ids_from_request_body,
)

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page

    from gflow_cli.api.transports.base import GenerationRequestRecorder
    from gflow_cli.api.transports.ui_automation import UiAutomationTransport


log = structlog.get_logger(__name__)


class BatchCaptureMixin(GenerationSettingsMixin):
    """Batch response listeners, request logging, response polling and image downloads."""

    @staticmethod
    def _attach_batch_response_listener(
        page: Page,
        *,
        project_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], Callable[[], None]]:
        """Synchronously register a ``page.on('response', ...)`` listener
        that records ``batchGenerateImages`` responses into a shared list.

        When ``project_id`` is provided, only responses whose URL contains
        ``/projects/{project_id}/`` are captured — this prevents stale
        responses from previously-visited projects accumulating in the list.

        Returns ``(captured, detach_fn)``:
        - ``captured`` is the shared list — the caller submits the prompt
          next, then polls / awaits that list via :meth:`_await_captured`.
        - ``detach_fn`` removes the handler from the page when called; it is
          idempotent (safe to call multiple times).

        Registering the listener BEFORE issuing the prompt click eliminates
        the race where the click could fire before an ``asyncio.create_task``-
        scheduled listener attaches.
        """
        captured: list[dict[str, Any]] = []

        async def on_response(response: Any) -> None:
            if "batchGenerateImages" not in response.url:
                return
            # Log EVERY batchGenerateImages response BEFORE the project_id
            # filter so live verification can diagnose listener-miss bugs
            # (e.g., URL contains a different project_id than the editor URL).
            log.info(
                "ui_automation.batch_response_seen",
                url=response.url,
                status=response.status,
                filter_project_id=project_id,
            )
            if project_id and f"/projects/{project_id}/" not in response.url:
                log.warning(
                    "ui_automation.batch_response_dropped_project_id_mismatch",
                    url=response.url,
                    filter_project_id=project_id,
                )
                return
            try:
                body = await response.json()
            except Exception as e:
                log.warning(
                    "ui_automation.batch_response_parse_failed",
                    error=str(e),
                    url=response.url,
                )
                return
            headers: dict[str, str] = {}
            raw_hdrs: Any = getattr(response, "headers", None)
            if raw_hdrs is not None:
                try:
                    if callable(raw_hdrs):
                        raw_hdrs = raw_hdrs()
                    if isinstance(raw_hdrs, dict):
                        raw_dict: dict[Any, Any] = cast("dict[Any, Any]", raw_hdrs)
                        headers = {str(k): str(v) for k, v in raw_dict.items()}
                except Exception:
                    pass
            captured.append(
                {
                    "status": response.status,
                    "url": response.url,
                    "body": body,
                    "headers": headers,
                    "ts": time.monotonic(),
                },
            )
            log.info(
                "ui_automation.batch_response_captured",
                status=response.status,
                url=response.url,
            )

        page.on("response", on_response)

        _detached = False

        def detach() -> None:
            nonlocal _detached
            if _detached:
                return
            _detached = True
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass

        return captured, detach

    @staticmethod
    def _attach_batch_request_logger(
        page: Page,
        *,
        project_id: str | None = None,
        sink: list[dict[str, Any]] | None = None,
        record_generation_request: GenerationRequestRecorder | None = None,
    ) -> Callable[[], None]:
        """Register a ``page.on('request', ...)`` that logs a compact summary of
        each outgoing ``batchGenerateImages`` body.

        The summary (see :func:`_summarize_batch_request_body`) surfaces whether
        the submit carries ``referenceEntities`` — the entity-reference spike's
        make-or-break signal — without dumping i2i image bytes. Returns an
        idempotent detach fn, torn down alongside the response listener.

        When *sink* is given, each matching request also appends
        ``{"url", "entity_ids"}`` so the caller can enforce the #170 submit
        backstop (:meth:`_assert_image_entities_attached`) after the run.
        """

        def on_request(request_obj: Any) -> None:
            if "batchGenerateImages" not in request_obj.url:
                return
            try:
                post_data = request_obj.post_data
            except Exception:
                post_data = None
            log.info(
                "ui_automation.batch_request_body",
                url=request_obj.url,
                filter_project_id=project_id,
                summary=_summarize_batch_request_body(post_data),
            )
            entity_ids = _entity_ids_from_request_body(post_data)
            if sink is not None:
                sink.append({"url": request_obj.url, "entity_ids": entity_ids})
            # #528: persist a counts-only echo into the incident bundle. The
            # stderr log above is richer but evaporates; the bundle is what a
            # reporter actually attaches to an issue.
            if record_generation_request is not None:
                try:
                    record_generation_request(
                        url=request_obj.url,
                        body_bytes=len(post_data or ""),
                        reference_entity_count=len(entity_ids),
                        reference_field_count=_reference_field_count(post_data),
                        mentions_reference_entities="referenceEntit" in (post_data or ""),
                    )
                except Exception:  # noqa: BLE001, S110 — observation only, never break a submit
                    pass

        page.on("request", on_request)

        _detached = False

        def detach() -> None:
            nonlocal _detached
            if _detached:
                return
            _detached = True
            try:
                page.remove_listener("request", on_request)
            except Exception:
                pass

        return detach

    @staticmethod
    async def _await_captured(
        captured: list[dict[str, Any]],
        timeout_s: float = 180.0,
        *,
        expected_count: int = 1,
        submit_time: float = 0.0,
        poll_interval_s: float = 0.5,
        straggler_window_s: float = 2.5,
    ) -> list[dict[str, Any]]:
        """Wait for ``expected_count`` batchGenerateImages responses.

        Flow generates N images via N separate API calls (not one call with
        N URLs). We poll until we have enough fresh responses (those whose
        ``ts >= submit_time``) or the timeout expires.

        **Defense A — post-submit-time filter (primary correctness fix):**
        Each captured entry carries a ``ts`` field written by the handler at
        append time (``time.monotonic()``). Only entries with
        ``entry["ts"] >= submit_time`` count toward ``expected_count``.  This
        eliminates the cross-contamination bug where a listener attached
        *before* the submit click inherits stale responses from prior prompts
        that arrived in the window between attach and click.

        When ``submit_time`` is 0.0 (the default, used by
        ``_capture_batch_response`` and legacy callers), all entries pass the
        filter, preserving backwards compatibility.

        **Straggler window:** after the count threshold is first reached the
        method waits an additional ``straggler_window_s`` seconds so that any
        slower same-submission responses (e.g. the last of a 2-image batch)
        can arrive before the list is snapshotted. This mirrors the Worker
        pattern (``_wait_for_n_new_images`` in the compile-growth monorepo).

        Raises ``TimeoutError`` if no fresh responses arrive within
        ``timeout_s``.  Returns the underlying response dicts (entries without
        the ``ts`` wrapper key) for entries with ``ts >= submit_time``.
        """
        deadline = time.monotonic() + timeout_s

        def _fresh() -> list[dict[str, Any]]:
            return [e for e in captured if e.get("ts", 0.0) >= submit_time]

        # Poll until we have enough fresh responses or the deadline passes.
        while time.monotonic() < deadline and len(_fresh()) < expected_count:
            await asyncio.sleep(poll_interval_s)

        fresh = _fresh()
        if not fresh:
            msg = f"No batchGenerateImages response within {timeout_s:.1f}s."
            raise TimeoutError(msg)
        if len(fresh) < expected_count:
            log.warning(
                "ui_automation.fewer_responses_than_expected",
                got=len(fresh),
                expected=expected_count,
            )
        else:
            # Threshold reached — wait for any slow stragglers from this same
            # submission before snapshotting the list.
            await asyncio.sleep(straggler_window_s)
            fresh = _fresh()

        # Return entries stripped of the internal `ts` bookkeeping key so
        # callers (_images_from_responses, tests) receive plain response dicts.
        return [{k: v for k, v in e.items() if k != "ts"} for e in fresh]

    @staticmethod
    async def _capture_batch_response(
        page: Page,
        timeout_s: float = 120.0,
        *,
        poll_interval_s: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Convenience wrapper: attach + await in one call.

        Useful when the caller has no work to interleave between attach
        and wait. ``generate_images`` does NOT use this — it splits the
        two halves so the listener is attached synchronously before
        ``_send_prompt`` issues the click.
        """
        captured, _detach = UiAutomationTransport._attach_batch_response_listener(page)
        return await UiAutomationTransport._await_captured(
            captured,
            timeout_s,
            poll_interval_s=poll_interval_s,
        )

    # ------------------------------------------------------------------
    # Internal helpers — image download (unit 3.8)
    # ------------------------------------------------------------------

    @staticmethod
    async def _download(
        urls: list[str],
        out_dir: Path,
        cookies: dict[str, str],
    ) -> list[Path]:
        """Download each URL into ``out_dir`` using session cookies.

        Saves to ``out_dir / image_NN.png`` (zero-padded index). Individual
        download failures are logged and skipped — the function returns the
        list of paths that DID write successfully.

        URLs whose host is not in :data:`_ALLOWED_DOWNLOAD_HOST_SUFFIXES`
        are skipped before any HTTP request is made — this prevents
        session cookies from being forwarded to a non-Google host through
        a malicious or compromised fifeUrl. Redirects are also disabled
        (``follow_redirects=False``) so an open-redirect on an allowed
        host cannot rebound the request to a third party.
        """
        import httpx  # local import — httpx is a runtime dependency

        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=False,
            cookies=cookies,
        ) as client:
            for i, url in enumerate(urls):
                if not _is_allowed_download_host(url):
                    log.error(
                        "ui_automation.download_host_rejected",
                        url=url,
                        allowed_suffixes=list(_ALLOWED_DOWNLOAD_HOST_SUFFIXES),
                    )
                    continue
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    # Auto-detect extension from Content-Type / magic bytes.
                    ct = resp.headers.get("content-type", "")
                    if "jpeg" in ct or "jpg" in ct or resp.content[:3] == b"\xff\xd8\xff":
                        ext = ".jpg"
                    else:
                        ext = ".png"
                    p = out_dir / f"image_{i:02d}{ext}"
                    p.write_bytes(resp.content)
                    paths.append(p)
                    log.info(
                        "ui_automation.image_saved",
                        path=str(p),
                        bytes=len(resp.content),
                        format=ext,
                    )
                except Exception as e:
                    log.exception(
                        "ui_automation.download_failed",
                        url=url,
                        error=str(e),
                    )
        return paths
