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
from typing import TYPE_CHECKING, Any, cast

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

# Mode switching is a 2-step dropdown (spec §6, §10.5). The trigger is the
# unified generation-settings button — the only button[aria-haspopup='menu']
# carrying an aspect-ratio crop_* icon; clicking it opens a role='menu' with
# the Imagem/Vídeo role='tablist' (the tabs are not in the DOM until it opens).
MODE_SWITCH_TRIGGER_SELECTORS = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_16_9'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_9_16'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_square'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_portrait'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_landscape'))",
    "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_original'))",
)
VIDEO_TAB_IN_MENU_SELECTORS = (
    "[role='menu'] [role='tab'][aria-controls*='VIDEO']",
    "[role='menu'] [role='tab']:has(i:text('play_circle'))",
    "[role='tab'][aria-controls*='VIDEO']",
    "[role='menu'] [role='tab']:has-text('Vídeo')",
    "[role='menu'] [role='tab']:has-text('Video')",
)
# Output-count tabs in the same dropdown. Flow defaults output count to x2
# (two videos = double credits — spec §10.5); generate_video forces count=1.
COUNT_ONE_SELECTORS = (
    "[role='menu'] [role='tab'][aria-controls*='-content-1']",
    "[role='menu'] [role='tab'][id*='-trigger-1']",
    "[role='menu'] [role='tab']:text-is('1x')",
)
# Aspect tabs inside the open menu. §6 best-effort — the Phase 0 spike confirmed
# video offers 9:16 / 16:9 only but did not lock an exact aspect-set selector;
# Phase B e2e hardens these. A miss is non-fatal (Flow's default applies).
VIDEO_ASPECT_TAB_SELECTORS: dict[Aspect, tuple[str, ...]] = {
    Aspect.PORTRAIT: (
        "[role='menu'] [role='tab'][aria-controls*='9_16']",
        "[role='menu'] [role='tab']:text-is('9:16')",
        "[role='tab']:has-text('9:16')",
    ),
    Aspect.LANDSCAPE: (
        "[role='menu'] [role='tab'][aria-controls*='16_9']",
        "[role='menu'] [role='tab']:text-is('16:9')",
        "[role='tab']:has-text('16:9')",
    ),
}

# The editor SPA's ready anchor — the Slate prompt textbox. The /project/ URL
# nav fires before the UI mounts; this is the readiness gate (used by
# _wait_video_editor_ready and asserted in its test).
_EDITOR_READY_ANCHOR = "div[role='textbox'][data-slate-editor='true'], div[contenteditable='true']"


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
        async def _dismiss_blocking_overlays(
            self, page: Page, out_dir: Path | None = None
        ) -> bool: ...

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

    @staticmethod
    async def _probe_selector_cascade(
        page: Page,
        label: str,
        candidates: tuple[str, ...],
        *,
        timeout_ms: int = 4000,
    ) -> Locator | None:
        """Try each selector in order; return the first visible match or None.
        Logs every attempt so a failed probe is diagnosable from the structured
        log alone."""
        for selector in candidates:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=timeout_ms)
                log.info("ui_automation_video.selector_matched", probe=label, selector=selector)
                return loc
            except Exception:  # noqa: BLE001 — selector miss; try the next
                log.debug("ui_automation_video.selector_miss", probe=label, selector=selector)
        log.warning("ui_automation_video.selector_probe_failed", probe=label)
        return None

    @staticmethod
    async def _switch_to_video_mode(page: Page, *, out_dir: Path | None) -> None:
        """Open the 2-step mode dropdown and switch to Video mode. The menu
        stays open afterward so the caller can also set aspect + count."""
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page, "mode_switch_trigger", MODE_SWITCH_TRIGGER_SELECTORS
        )
        if trigger is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_mode_trigger.png")
            raise RuntimeError(
                f"mode-switch dropdown trigger not found on the Flow editor. Screenshot: {shot}"
            )
        await trigger.click()
        await page.wait_for_timeout(800)
        video_tab = await VideoGenerationMixin._probe_selector_cascade(
            page, "video_mode_tab", VIDEO_TAB_IN_MENU_SELECTORS
        )
        if video_tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_video_tab.png")
            raise RuntimeError(f"Video tab not found in the mode dropdown. Screenshot: {shot}")
        await video_tab.click()
        await page.wait_for_timeout(1200)
        log.info("ui_automation_video.video_mode_entered")

    @staticmethod
    async def _wait_video_editor_ready(page: Page) -> None:
        """Wait for the editor SPA to mount before probing video controls. The
        /project/ URL nav fires before the UI renders — the Phase 0 spike found
        probes taken right after it see only the page shell. The prompt textbox
        is the ready anchor. Non-fatal on timeout (the cascade probes still
        have their own per-selector waits)."""
        try:
            await page.locator(_EDITOR_READY_ANCHOR).first.wait_for(state="visible", timeout=20_000)
            await page.wait_for_timeout(1000)
            log.info("ui_automation_video.editor_ready")
        except Exception as e:  # noqa: BLE001 — non-fatal readiness gate
            log.warning("ui_automation_video.editor_ready_timeout", error=str(e))

    @staticmethod
    async def _set_output_count_one(page: Page) -> None:
        """Force the output count to 1. Flow defaults to x2 (two videos =
        double credits — spec §10.5). Non-fatal on miss."""
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page, "count_one_tab", COUNT_ONE_SELECTORS
        )
        if tab is None:
            log.warning("ui_automation_video.count_not_set", note="Flow default (x2) applies")
            return
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.output_count_set", count=1)

    @staticmethod
    async def _select_video_aspect(page: Page, aspect: Aspect) -> None:
        """Click the aspect-ratio tab for `aspect` in the open mode dropdown.
        Non-fatal on miss — generation proceeds with Flow's default ratio."""
        candidates = VIDEO_ASPECT_TAB_SELECTORS.get(aspect)
        if candidates is None:
            log.warning("ui_automation_video.aspect_unsupported", aspect=aspect.value)
            return
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page, "video_aspect_tab", candidates
        )
        if tab is None:
            log.warning("ui_automation_video.aspect_not_set", aspect=aspect.value)
            return
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.aspect_set", aspect=aspect.value)

    @staticmethod
    async def _await_generate_response(
        captured: list[dict[str, Any]],
        *,
        timeout_s: float = 180.0,
        poll_interval_s: float = 0.5,
    ) -> dict[str, Any]:
        """Wait for the first captured batchAsyncGenerateVideo* response."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not captured:
            await asyncio.sleep(poll_interval_s)
        if not captured:
            raise TimeoutError(
                f"no batchAsyncGenerateVideo* response within {timeout_s:.0f}s — "
                "did the submit fire? did reCAPTCHA fail silently?"
            )
        return captured[0]

    async def generate_video(
        self,
        *,
        request: GenerateVideoRequest,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
    ) -> VideoStatus:
        """Generate ONE video by driving the Flow editor UI (Phase A: T2V only).

        Returns a `VideoStatus` for both SUCCESSFUL and FAILED terminal states;
        the caller maps a FAILED status to a typed error (spec §7). Raises
        `RuntimeError` (no setup / editor control missing), `NotImplementedError`
        (non-T2V), `ValueError` (SQUARE aspect), `AuthExpiredError` (401),
        `WafRejectionError` (403), `WireFormatError` (other non-200 / no media),
        or `TimeoutError`.
        """
        if not self._setup_done or self._page is None:
            raise RuntimeError(
                "UiAutomationTransport.setup() must be called before generate_video()"
            )
        if request.mode is not Mode.T2V:
            raise NotImplementedError("Phase A supports T2V only; I2V and R2V land in Phase B")
        if request.aspect is Aspect.SQUARE:
            raise ValueError(
                "video generation does not support the SQUARE aspect; "
                "use PORTRAIT (9:16) or LANDSCAPE (16:9)"
            )
        async with self._generate_lock:
            return await self._generate_video_locked(request, out_dir, poll_timeout_s)

    async def _generate_video_locked(
        self,
        request: GenerateVideoRequest,
        out_dir: Path | None,
        poll_timeout_s: float,
    ) -> VideoStatus:
        """Serialized body of `generate_video` — runs under `self._generate_lock`
        (shared with `generate_images`: one Page, one DOM)."""
        page: Page = self._page  # type: ignore[assignment]  # guarded in generate_video

        await self._enter_editor(page, out_dir)
        await VideoGenerationMixin._wait_video_editor_ready(page)
        # Dismiss any Flow changelog / "What's new" overlay that may be on top
        # of the editor before we click into mode-switch / settings / submit (#26).
        await self._dismiss_blocking_overlays(page, out_dir)
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=out_dir)
        await VideoGenerationMixin._select_video_aspect(page, request.aspect)
        await VideoGenerationMixin._set_output_count_one(page)
        await page.keyboard.press("Escape")  # close the mode dropdown
        await page.wait_for_timeout(400)

        # Attach BOTH listeners synchronously BEFORE the prompt is submitted so
        # neither the generate response nor an early status poll is missed.
        generate_captured, generate_handler = VideoGenerationMixin._attach_video_response_listener(
            page
        )
        status_captured, status_handler = VideoGenerationMixin._attach_status_response_listener(
            page
        )
        try:
            await self._send_prompt(page, request.prompt, out_dir)

            generate_resp = await VideoGenerationMixin._await_generate_response(generate_captured)
            http_status = generate_resp.get("status")
            url = str(generate_resp.get("url", ""))
            # errors.py documents `route` as a sanitized route NAME, not a URL.
            route = next((r for r in VIDEO_GENERATE_ROUTES if r in url), "video:generate")
            if http_status == 401:
                raise AuthExpiredError(
                    detail="batchAsyncGenerateVideo* returned HTTP 401 — session expired",
                    status=401,
                    route=route,
                )
            if http_status == 403:
                raise WafRejectionError(
                    detail=(
                        "batchAsyncGenerateVideo* returned HTTP 403 — WAF / reCAPTCHA rejection"
                    ),
                    status=403,
                    route=route,
                )
            if http_status != 200:
                raise WireFormatError(
                    detail=f"batchAsyncGenerateVideo* returned HTTP {http_status}",
                    status=http_status if isinstance(http_status, int) else None,
                    route=route,
                )
            # A video 200 ALWAYS carries media[0] (the asset slot — capture 02);
            # content rejection surfaces later as a FAILED *status*, not empty
            # media. So a missing media[0] here is a genuine wire anomaly —
            # WireFormatError, NOT ContentPolicyError (the image-flow pattern).
            try:
                media_name = media_name_from_generate_response(generate_resp.get("body") or {})
            except ValueError as e:
                # discovery carries only the route + the body's top-level KEY
                # NAMES (not values) — enough to diagnose the anomaly without
                # logging `remainingCredits`, media UUIDs, or any token.
                anomaly_body = cast("dict[str, Any]", generate_resp.get("body") or {})
                raise WireFormatError(
                    detail=f"video generate response carries no media id: {e}",
                    route=route,
                    discovery={"route": route, "top_level_keys": sorted(anomaly_body)},
                ) from e

            return await VideoGenerationMixin._poll_video_status(
                page, status_captured, media_name, timeout_s=poll_timeout_s
            )
        finally:
            # The Page is pooled and persistent — remove both listeners so they
            # never leak across calls.
            page.remove_listener("response", generate_handler)
            page.remove_listener("response", status_handler)
