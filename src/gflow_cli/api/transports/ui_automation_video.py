"""Video-generation methods for UiAutomationTransport.

Mixed into `UiAutomationTransport` via `VideoGenerationMixin` — kept in its own
module because `ui_automation.py` is already over the 800-line cap.

Video generation mirrors `generate_images`: the transport drives the Flow
editor UI and Flow's own JavaScript builds the request, sends it, and mints
reCAPTCHA on submit — the transport never POSTs a generate body. The status
endpoint returns HTTP 401 to `page.request.post`, so polling captures Flow's
own `batchCheckAsyncVideoGenerationStatus` responses instead of issuing the
POST (spec §5.5).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoStatus,
    media_name_from_generate_response,
    parse_video_status,
)
from gflow_cli.errors import AuthExpiredError, WafRejectionError, WireFormatError

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

log = structlog.get_logger(__name__)

# The three mode-specific generate routes (spec §2.1). The listener filters on
# these substrings only — video generate URLs carry no /projects/{id}/ path
# segment, so a project-id URL filter is impossible (deviation from §5.4).
VIDEO_GENERATE_ROUTES = (
    "batchAsyncGenerateVideoText",
    "batchAsyncGenerateVideoStartAndEndImage",
    "batchAsyncGenerateVideoReferenceImages",
)
# Status-poll route — Flow's SPA polls this itself while a generation runs.
VIDEO_STATUS_ROUTE = "batchCheckAsyncVideoGenerationStatus"


async def _capture_debug_screenshot(page: Any, out_dir: Path | None, filename: str) -> Path | None:
    """Best-effort viewport screenshot for debugging. Duplicated from
    `ui_automation.py` to keep this module free of a circular import."""
    if out_dir is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_path = out_dir / filename
    try:
        await page.screenshot(path=str(shot_path), full_page=False)
        log.warning(
            "ui_automation_video.debug_screenshot_may_contain_pii",
            path=str(shot_path),
            note="viewport may include the account avatar / email from the Google session",
        )
    except Exception as e:  # noqa: BLE001 — screenshot is best-effort
        log.debug("ui_automation_video.screenshot_capture_failed", error=str(e))
    return shot_path


class VideoGenerationMixin:
    """Video-generation methods mixed into `UiAutomationTransport`.

    The mixin depends on host state and helpers that `UiAutomationTransport`
    supplies; they are declared below as a TYPE-ONLY contract so
    `pyright --strict` resolves `self._page` / `self._enter_editor` etc. The
    bare annotations and `if TYPE_CHECKING` stubs create no runtime members —
    the real values come from `UiAutomationTransport.__init__` and its methods.
    This replaces a separate `_VideoHost` Protocol: pyright rejects an explicit
    `self: _VideoHost` annotation on a mixin method because `VideoGenerationMixin`
    itself does not satisfy that Protocol.
    """

    # --- host contract: supplied by UiAutomationTransport (type-only) ---
    _page: Page | None
    _setup_done: bool
    _generate_lock: asyncio.Lock

    if TYPE_CHECKING:

        async def _enter_editor(self, page: Page, out_dir: Path | None = None) -> None: ...
        async def _send_prompt(
            self, page: Page, prompt_text: str, out_dir: Path | None = None
        ) -> None: ...

    @staticmethod
    def _attach_video_response_listener(page: Page) -> tuple[list[dict[str, Any]], Any]:
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
        captured: list[dict[str, Any]] = []

        async def on_response(response: Any) -> None:
            if not any(route in response.url for route in VIDEO_GENERATE_ROUTES):
                return
            try:
                body = await response.json()
            except Exception as e:  # noqa: BLE001 — parse failures are non-fatal
                log.warning("ui_automation_video.generate_parse_failed", error=str(e))
                return
            captured.append({"status": response.status, "url": response.url, "body": body})
            log.info(
                "ui_automation_video.generate_captured",
                status=response.status,
                url=response.url,
            )

        page.on("response", on_response)
        return captured, on_response

    @staticmethod
    def _attach_status_response_listener(page: Page) -> tuple[list[dict[str, Any]], Any]:
        """Register a `page.on('response')` listener for the status route. Flow's
        SPA polls `batchCheckAsyncVideoGenerationStatus` itself while a
        generation runs; this captures that traffic. Returns `(captured, handler)`
        — the caller MUST `page.remove_listener('response', handler)` in a
        `finally`. Attached BEFORE `_send_prompt` so no early status response is
        missed (spec §5.5)."""
        captured: list[dict[str, Any]] = []

        async def on_response(response: Any) -> None:
            if VIDEO_STATUS_ROUTE not in response.url:
                return
            try:
                body = await response.json()
            except Exception as e:  # noqa: BLE001 — parse failures are non-fatal
                log.warning("ui_automation_video.status_parse_failed", error=str(e))
                return
            captured.append({"status": response.status, "url": response.url, "body": body})

        page.on("response", on_response)
        return captured, on_response

    @staticmethod
    async def _poll_video_status(
        page: Page,
        captured_status: list[dict[str, Any]],
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
        §5.5). Raises `TimeoutError` only at the hard `timeout_s` deadline.
        """
        deadline = time.monotonic() + timeout_s
        last_status: str | None = None
        seen_count = len(captured_status)
        last_progress = time.monotonic()
        nudged = False
        while time.monotonic() < deadline:
            terminal: VideoStatus | None = None
            for response in captured_status:
                try:
                    status = parse_video_status(response.get("body") or {}, media_id=media_name)
                except ValueError:
                    continue  # this response is for other media — skip
                last_status = status.status
                if status.is_terminal:
                    terminal = status
            if terminal is not None:
                log.info(
                    "ui_automation_video.poll_terminal",
                    media_name=media_name,
                    status=terminal.status,
                )
                return terminal
            # Stall detection: nudge the tab to the foreground ONCE if Flow's
            # own polling has stopped — or never started — appending responses.
            if len(captured_status) != seen_count:
                seen_count = len(captured_status)
                last_progress = time.monotonic()
            elif not nudged and time.monotonic() - last_progress > stall_nudge_s:
                nudged = True
                # Distinguish "Flow never polled the status route at all" from
                # "Flow stalled mid-run" — the former is the single most likely
                # production failure (spec §5.5 flags it as unconfirmed).
                event = (
                    "ui_automation_video.poll_no_status_traffic"
                    if seen_count == 0
                    else "ui_automation_video.poll_stall_nudge"
                )
                log.warning(event, media_name=media_name, status_responses_seen=seen_count)
                try:
                    await page.bring_to_front()
                except Exception as e:  # noqa: BLE001 — best-effort
                    log.debug("ui_automation_video.bring_to_front_failed", error=str(e))
            await asyncio.sleep(poll_interval_s)
        cause = (
            "Flow never polled the status route"
            if seen_count == 0
            else "Flow stopped polling before a terminal status"
        )
        raise TimeoutError(
            f"no terminal status for {media_name!r} within {timeout_s:.0f}s — "
            f"{seen_count} status response(s) seen, last status: {last_status}. {cause}."
        )
