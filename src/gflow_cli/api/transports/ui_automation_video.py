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
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api import routes
from gflow_cli.api.transports._common import extract_project_id
from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    VideoResult,
    VideoStarted,
    VideoStartedCallback,
    VideoStatus,
    media_name_from_generate_response,
    operation_name_from_generate_response,
    parse_video_status,
)
from gflow_cli.errors import AuthExpiredError, WafRejectionError, WireFormatError
from gflow_cli.storage import AnyPath, storage_path, write_asset_async

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

log = structlog.get_logger(__name__)

# The three mode-specific generate routes (spec §2.1). The listener filters on
# these substrings only — video generate URLs carry no /projects/{id}/ path
# segment, so a project-id URL filter is impossible (deviation from §5.4).
VIDEO_GENERATE_ROUTES = (
    "batchAsyncGenerateVideoText",
    "batchAsyncGenerateVideoStartImage",
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
# SOT (flow-editor-map.json): the VIDEO mode tab id ends with '-trigger-VIDEO'
# (radix prefix is dynamic — match the suffix). aria-controls ends with
# '-content-VIDEO'. Both are EXACT (ends-with), so they do NOT match the
# sub-mode tabs '-trigger-VIDEO_FRAMES' / '-trigger-VIDEO_REFERENCES'. Icon +
# id-suffix are locale-independent; the localized-text fallbacks come last.
VIDEO_TAB_IN_MENU_SELECTORS = (
    "[role='tab'][id$='-trigger-VIDEO']",
    "[role='tab'][aria-controls$='-content-VIDEO']",
    "[role='menu'] [role='tab']:has(i:text('play_circle'))",
    "[role='menu'] [role='tab']:has-text('Vídeo')",
    "[role='menu'] [role='tab']:has-text('Video')",
)
# Output-count + duration tabs are selected by aria-label text in
# `_set_output_count` / `_select_video_duration` — NOT by id-suffix: the count
# tab '-trigger-4' and the duration tab '-trigger-4' (4s) share a suffix, so an
# id match is ambiguous (this was the prior '[id*=-trigger-1]' bug that also
# caught '-trigger-10'). Labels '1x'/'x2'.. and '4s'/'6s'.. are unambiguous and
# locale-independent.

# Aspect tabs inside the open menu. SOT (flow-editor-map.json): video aspect
# tab ids end with '-trigger-PORTRAIT' (9:16, icon crop_9_16) and
# '-trigger-LANDSCAPE' (16:9, icon crop_16_9). The prior selector matched
# aria-controls*='9_16', but the real aria-controls is '-content-PORTRAIT' /
# '-content-LANDSCAPE' (NO '9_16'/'16_9' substring) — it never matched and
# always fell through to text. id-suffix + icon are locale-independent and
# exact (ends-with '-trigger-PORTRAIT' does not match the image-only
# '-trigger-PORTRAIT_3_4'). A miss is non-fatal (Flow's default applies).
VIDEO_ASPECT_TAB_SELECTORS: dict[Aspect, tuple[str, ...]] = {
    Aspect.PORTRAIT: (
        "[role='tab'][id$='-trigger-PORTRAIT']",
        "[role='menu'] [role='tab']:has(i.google-symbols:text-is('crop_9_16'))",
        "[role='tab']:has-text('9:16')",
    ),
    Aspect.LANDSCAPE: (
        "[role='tab'][id$='-trigger-LANDSCAPE']",
        "[role='menu'] [role='tab']:has(i.google-symbols:text-is('crop_16_9'))",
        "[role='tab']:has-text('16:9')",
    ),
}

# Model picker (SOT flow-editor-map.json). The trigger is the only
# button[aria-haspopup='menu'] carrying an 'arrow_drop_down' icon; its label is
# the currently-selected model. Options are role='menuitem' matched by product
# name (NOT localized). 'Veo 3.1 - Lite' is a prefix of 'Veo 3.1 - Lite [Lower
# Priority]', so it needs an EXACT text match (the menuitem text is the model
# name prefixed by the 'volume_up' icon ligature); the others match by has-text.
MODEL_PICKER_TRIGGER = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text-is('arrow_drop_down'))"
)
VIDEO_MODEL_OPTION_SELECTORS: dict[VideoModel, str] = {
    VideoModel.OMNI_FLASH: "[role='menuitem']:has-text('Omni Flash')",
    VideoModel.VEO_3_1_FAST: "[role='menuitem']:has-text('Veo 3.1 - Fast')",
    VideoModel.VEO_3_1_QUALITY: "[role='menuitem']:has-text('Veo 3.1 - Quality')",
    VideoModel.VEO_3_1_LITE: "[role='menuitem']:text-is('volume_upVeo 3.1 - Lite')",
    VideoModel.VEO_3_1_LITE_LOWER_PRIORITY: "[role='menuitem']:has-text('[Lower Priority]')",
}

# Image-attach for I2V (SOT flow-editor-map.json + live verification).
# CRITICAL: set_input_files() on the generic hidden input only adds the image to
# the LIBRARY — it does NOT associate it with the start/end frame slot, so Flow
# then fires the plain `batchAsyncGenerateVideoText` route (image ignored). The
# frame slot MUST be filled through its own dialog: click the slot
# (div[aria-haspopup='dialog'] labelled Start/End) -> 'Upload media' (opens a
# file chooser) -> wait uploadImage -> 'Add to Prompt' to commit it into the
# slot. Only then does the DOM Generate click fire StartImage/StartAndEndImage.
UPLOAD_IMAGE_ROUTE = "uploadImage"
# Frame slots are `<div type="button" aria-haspopup="dialog">` — Flow uses a
# div-with-button-semantics custom component for the Start/End slots in I2V mode.
# Their label text is localized (EN 'Start'/'End', PT-BR 'Inicial'/'Final',
# DE 'Anfang'/'Ende', JA '開始'/'終了', etc.).
#
# Tier 1 (PRIMARY, locale-free): `FRAME_SLOTS_STRUCT` matches the exact pair via
# the `type='button'` + `aria-haspopup='dialog'` composite — a unique pattern in
# Flow's editor (regular elements don't carry a `type` attr on divs). Order is
# DOM order: `.nth(0)` = Start, `.nth(1)` = End.
#
# Tier 2 (FALLBACK, EN-only): `FRAME_SLOT_BY_LABEL` matches by visible English
# text. Kept for defense-in-depth in case Flow drops the `type` attribute on
# the slots; the fail-loud `RuntimeError` in `_attach_frame` covers the case
# where both tiers miss on a non-EN profile.
#
# Caller labels are always the hardcoded constants 'Start' / 'End', so no
# CSS-escaping is needed.
#
# Earlier PR #70 used a structural anchor
#   `div:has(> button:has(i.google-symbols:text-is('swap_horiz')))`
# as the parent of the dialog slots. That anchor was broken on real Flow DOMs because
# (1) the slots are `<div type="button">` not children of any `div > button`
# wrapper, and (2) the `swap_horiz` icon uses class `material-icons` (NOT
# `google-symbols`). PR #70's structural tier therefore matched ZERO elements
# on every profile, silently falling through to the text-tier which only
# matched on EN. This was discovered 2026-05-26 via DOM probe on pt-BR — see
# scripts/dev/capture_i2v_frame_slots_dom.py.
FRAME_SLOTS_STRUCT = "div[type='button'][aria-haspopup='dialog']"
FRAME_SLOT_BY_LABEL = "div[aria-haspopup='dialog']:has-text('{label}')"
# Media-dialog action buttons. These MUST be locale-agnostic: Flow renders the
# dialog in the CHROME PROFILE's language (NOT the Google account language, and
# the `--lang=en-US` launch arg does NOT override an existing profile's stored
# language), so a text match like has-text('Upload media') silently misses on a
# pt-BR / th / ... profile -> the file chooser never opens -> 34s hang (#56).
# Anchor on the Material Symbols icon ligature (locale-free) instead:
#   - 'Upload media' carries the `upload` icon. Use :text-is('upload') (EXACT) so
#     it doesn't also grab the 'Uploads' tab (icon `drive_folder_upload`).
#   - 'Add to Prompt' has NO icon, so it can't be matched by ligature; it's the
#     only iconless button in the open dialog -> selected structurally at the
#     call site via .filter(has_not=<icon>).
# Both are scoped to the open Radix popover ([role='dialog'][data-state='open']).
# FALLBACK / OPERATOR NOTE: if Google ever restructures this dialog so even these
# anchors break, `_upload_via_open_dialog` raises a clear error + screenshot
# instead of hanging. The operator workaround is to set the CHROME PROFILE
# language to English -- the Google ACCOUNT language alone is NOT enough.
UPLOAD_MEDIA_BUTTON = (
    "[role='dialog'][data-state='open'] button:has(i.google-symbols:text-is('upload'))"
)
# Tier-2 fallback: the original localized-text selector (#50). Only matches an
# ENGLISH-rendering profile — kept as a graceful fallback for the narrow case
# where Google changes the `upload` icon ligature but the English label survives.
# This is exactly why the failure message tells the operator to set the Chrome
# profile to English: it makes this fallback tier viable.
UPLOAD_MEDIA_BUTTON_TEXT = "[role='dialog'][data-state='open'] button:has-text('Upload media')"
# 'Add to Prompt' has no stable string anchor; selected structurally (the lone
# iconless button) at the call site. This scope is the open media dialog.
ADD_TO_PROMPT_DIALOG = "[role='dialog'][data-state='open']"
# R2V references mode has NO Start/End slots — references are added via the
# only button[aria-haspopup='dialog'] in the editor: a 'Create' button carrying
# the 'add_2' icon (its visible text 'Create' / 'Add Media' is unreliable — a
# has-text('Add Media') match grabbed a nav-header button instead). The icon +
# dialog-popup combo is locale-free and unambiguous in the editor. Repeat up to
# MAX_REFERENCE_IMAGES — the button persists to add the next reference.
ADD_MEDIA_BUTTON = "button[aria-haspopup='dialog']:has(i.google-symbols:text-is('add_2'))"
VIDEO_SUBMODE_SELECTORS: dict[str, tuple[str, ...]] = {
    # I2V — "frames" (start + optional end frame). Icon: crop_free.
    "frames": (
        "[role='tab'][id$='-trigger-VIDEO_FRAMES']",
        "[role='menu'] [role='tab']:has(i.google-symbols:text('crop_free'))",
    ),
    # R2V — "references"/ingredients/Elementos. Icon: chrome_extension.
    "references": (
        "[role='tab'][id$='-trigger-VIDEO_REFERENCES']",
        "[role='menu'] [role='tab']:has(i.google-symbols:text('chrome_extension'))",
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
    except Exception as e:
        log.debug("ui_automation_video.screenshot_capture_failed", error=str(e))
    return shot_path


def _summarize_request_image_inputs(request: Any) -> dict[str, Any]:
    """Privacy-safe summary of the image inputs in a generate request body:
    presence of startImage/endImage + referenceImages count, each as an 8-char
    mediaId PREFIX (UUIDs are asset ids, not secrets). Proves the attached
    images are bound into the request. Never returns the reCAPTCHA token or
    credit balance. Non-fatal — returns ``{"parsed": False}`` on any error."""
    try:
        raw = request.post_data
        if not raw:
            return {"parsed": False}
        data = cast("dict[str, Any]", json.loads(raw))
        reqs = cast("list[dict[str, Any]]", data.get("requests") or [])
        first: dict[str, Any] = reqs[0] if reqs else {}

        def _mid(obj: Any) -> str | None:
            if not isinstance(obj, dict):
                return None
            mid = cast("dict[str, Any]", obj).get("mediaId")
            return mid[:8] if isinstance(mid, str) else None

        refs = cast("list[dict[str, Any]]", first.get("referenceImages") or [])
        return {
            "parsed": True,
            "startImage": _mid(first.get("startImage")),
            "endImage": _mid(first.get("endImage")),
            "referenceCount": len(refs),
            "referenceIds": [_mid(r) for r in refs],
        }
    except Exception as e:
        return {"parsed": False, "error": str(e)[:60]}


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
    _out_dir: Path | None

    if TYPE_CHECKING:

        async def _enter_editor(self, page: Page, out_dir: Path | None = None) -> None: ...
        async def _send_prompt(
            self, page: Page, prompt_text: str, out_dir: Path | None = None,
        ) -> None: ...
        async def _dismiss_blocking_overlays(
            self, page: Page, out_dir: Path | None = None,
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
            except Exception as e:
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
                except Exception as e:
                    log.debug("ui_automation_video.bring_to_front_failed", error=str(e))
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
        raise TimeoutError(
            msg,
        )

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
        url = routes.media_download_url(media_id)
        resp = await page.request.get(url, max_redirects=5, timeout=180_000)
        if resp.status >= 400:
            raise WireFormatError(
                detail=(
                    f"video download returned HTTP {resp.status} for {media_id!r} "
                    f"via media.getMediaUrlRedirect"
                ),
                status=resp.status,
                route="media.getMediaUrlRedirect",
            )
        body = await resp.body()
        storage_uri: str | None = getattr(self, "_storage_uri", None)
        if storage_uri:
            from datetime import date

            from gflow_cli import paths as _paths

            key = f"videos/{date.today().isoformat()}/{_paths.validate_job_id(media_id)}.mp4"
            # output_dir fallback only used for key computation when cloud is active
            output_dir = getattr(self, "_output_dir", None) or Path("tmp")
            target: AnyPath = storage_path(storage_uri, output_dir, key)
        else:
            effective_dir = out_dir or self._out_dir or Path("tmp")
            target = effective_dir / f"{media_id}.mp4"
        await write_asset_async(target, body)
        log.info(
            "ui_automation_video.video_saved",
            path=str(target),
            bytes=len(body),
            media_id=media_id,
        )
        return target

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
            except Exception:
                log.debug("ui_automation_video.selector_miss", probe=label, selector=selector)
        log.warning("ui_automation_video.selector_probe_failed", probe=label)
        return None

    @staticmethod
    async def _switch_to_video_mode(page: Page, *, out_dir: Path | None) -> None:
        """Open the 2-step mode dropdown and switch to Video mode. The menu
        stays open afterward so the caller can also set aspect + count."""
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page, "mode_switch_trigger", MODE_SWITCH_TRIGGER_SELECTORS,
        )
        if trigger is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_mode_trigger.png")
            msg = f"mode-switch dropdown trigger not found on the Flow editor. Screenshot: {shot}"
            raise RuntimeError(
                msg,
            )
        await trigger.click()
        await page.wait_for_timeout(800)
        video_tab = await VideoGenerationMixin._probe_selector_cascade(
            page, "video_mode_tab", VIDEO_TAB_IN_MENU_SELECTORS,
        )
        if video_tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_video_tab.png")
            msg = f"Video tab not found in the mode dropdown. Screenshot: {shot}"
            raise RuntimeError(msg)
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
        except Exception as e:
            log.warning("ui_automation_video.editor_ready_timeout", error=str(e))

    @staticmethod
    async def _set_output_count(page: Page, n: int) -> None:
        """Set the output count to `n` (1-4). Flow defaults to x2 (two videos =
        double credits — spec §10.5). Disambiguated by aria-label text
        ('1x'/'x2'/'x3'/'x4'), NOT id-suffix — '-trigger-4' collides with the
        DURATION 4s tab. Non-fatal on miss."""
        label = "1x" if n == 1 else f"x{n}"
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "count_tab",
            (f"[role='tab']:text-is('{label}')", f"[role='tab']:has-text('{label}')"),
        )
        if tab is None:
            log.warning(
                "ui_automation_video.count_not_set", count=n, note="Flow default (x2) applies",
            )
            return
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.output_count_set", count=n)

    @staticmethod
    async def _set_output_count_one(page: Page) -> None:
        """Back-compat shim: force the output count to 1."""
        await VideoGenerationMixin._set_output_count(page, 1)

    @staticmethod
    async def _select_video_model(page: Page, model: VideoModel, *, out_dir: Path | None) -> None:
        """Open the model picker and select `model`. Non-fatal on miss (Flow's
        default model applies) but logged at WARNING — picking the wrong model
        changes credit cost, so a miss is a real signal, not noise."""
        option_sel = VIDEO_MODEL_OPTION_SELECTORS.get(model)
        if option_sel is None:
            log.warning("ui_automation_video.model_unknown", model=model.value)
            return
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page, "model_picker_trigger", (MODEL_PICKER_TRIGGER,),
        )
        if trigger is None:
            log.warning(
                "ui_automation_video.model_picker_not_found",
                model=model.value,
                note="Flow default model applies",
            )
            return
        await trigger.click()
        await page.wait_for_timeout(600)
        option = await VideoGenerationMixin._probe_selector_cascade(
            page, "model_option", (option_sel,),
        )
        if option is None:
            log.warning(
                "ui_automation_video.model_option_not_found",
                model=model.value,
                note="Flow default model applies",
            )
            # The model dropdown is the TOP layer here — Escape closes only it,
            # leaving the settings popover open (Escape on the popover itself
            # would close everything). Safe to recover.
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
            return
        await option.click()
        await page.wait_for_timeout(800)
        log.info("ui_automation_video.model_selected", model=model.value)

    @staticmethod
    async def _select_video_duration(page: Page, seconds: int) -> None:
        """Click the duration tab for `seconds` (4/6/8, or 10 for omni_flash).
        Disambiguated by aria-label text ('4s'..'10s'), NOT id-suffix
        (collides with count). Must run AFTER model select — the 10s tab only
        exists once omni_flash is chosen. Non-fatal on miss."""
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page,
            "duration_tab",
            (f"[role='tab']:text-is('{seconds}s')", f"[role='tab']:has-text('{seconds}s')"),
        )
        if tab is None:
            log.warning("ui_automation_video.duration_not_set", seconds=seconds)
            return
        await tab.click()
        await page.wait_for_timeout(400)
        log.info("ui_automation_video.duration_set", seconds=seconds)

    @staticmethod
    async def _switch_video_sub_mode(page: Page, sub: str, *, out_dir: Path | None) -> None:
        """Switch the video sub-mode tab: 'frames' (I2V) or 'references' (R2V).
        Must run while the settings panel is open (after _switch_to_video_mode)."""
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page, f"video_submode_{sub}", VIDEO_SUBMODE_SELECTORS[sub],
        )
        if tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, f"debug_no_submode_{sub}.png")
            msg = f"video sub-mode tab {sub!r} not found on the Flow editor. Screenshot: {shot}"
            raise RuntimeError(
                msg,
            )
        await tab.click()
        await page.wait_for_timeout(900)
        log.info("ui_automation_video.video_submode_entered", sub=sub)

    @staticmethod
    async def _attach_frame(
        page: Page,
        slot_index: int,
        label: str,
        image: Path,
        *,
        out_dir: Path | None,
        timeout_s: float = 120.0,
    ) -> None:
        """Fill the I2V first/last frame slot (`slot_index` 0=first, 1=last) with
        `image` through Flow's media dialog (the ONLY way that binds the image to
        the slot — set_input_files on the generic input only adds it to the
        library, see the UPLOAD_IMAGE_ROUTE note). Sequence: click slot ->
        'Upload media' (file chooser) -> wait uploadImage -> commit. Must run with
        the settings panel CLOSED (the slots live in the main editor). `label` is
        for logging only. Path existence is validated here (the boundary)."""
        if not image.exists():
            msg = f"frame image not found: {image}"
            raise FileNotFoundError(msg)

        # Locate the slot structural-first (locale-free): the frame slots are the
        # dialog-divs inside the swap_horiz container, indexed by position
        # (0=start, 1=end).  FRAME_SLOT_BY_LABEL (has-text 'Start'/'End') is
        # tried only as a fallback when the structural count is insufficient —
        # it requires --lang=en-US / English Chrome profile to work.
        #
        # wait_for is short (1500 ms) because _wait_video_editor_ready already
        # guaranteed the editor SPA is mounted; the frame panel resolves in
        # <10 ms (one CDP round-trip) on a pre-rendered page.  A shorter probe
        # means a future swap_horiz rename surfaces as a fast, clear error
        # instead of an 8-second dead wait on every I2V/R2V call.
        structs = page.locator(FRAME_SLOTS_STRUCT)
        try:
            await structs.first.wait_for(state="visible", timeout=1500)
        except Exception as e:
            shot = await _capture_debug_screenshot(
                page, out_dir, f"debug_no_{label.lower()}_slot.png",
            )
            msg = f"frame slot {label!r} not found on the Flow editor. Screenshot: {shot}"
            raise RuntimeError(
                msg,
            ) from e

        struct_count = await structs.count()
        if struct_count > slot_index:
            # Both slots unfilled — pick by DOM order.
            slot = structs.nth(slot_index)
        elif struct_count > 0:
            # Some slots already attached (typical: Start filled, End remaining).
            # The DOM only keeps `div[type='button'][aria-haspopup='dialog']` on
            # the unfilled slot(s) — once an image binds, the slot transitions
            # away from that pattern. The next unfilled slot is therefore
            # `.first` of the remaining matches, regardless of its original
            # positional index. This case is hit on the End-frame call after
            # Start was just attached.
            slot = structs.first
        else:
            # Structural count was insufficient; fall back to text-label match.
            slot = page.locator(FRAME_SLOT_BY_LABEL.format(label=label)).first
            try:
                await slot.wait_for(state="visible", timeout=3000)
            except Exception as e:
                msg = (
                    f"frame slot index {slot_index} ({label!r}) not present "
                    f"(found {struct_count} structural slot(s), "
                    f"text-label fallback also missed)"
                )
                raise RuntimeError(
                    msg,
                ) from e
        await slot.click()
        await page.wait_for_timeout(1000)  # media dialog opens
        await VideoGenerationMixin._upload_via_open_dialog(
            page, image, log_label=label, out_dir=out_dir, timeout_s=timeout_s,
        )
        log.info("ui_automation_video.frame_attached", slot=label)

    @staticmethod
    async def _upload_via_open_dialog(
        page: Page,
        image: Path,
        *,
        log_label: str,
        out_dir: Path | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        """With a media dialog ALREADY open, upload `image` and commit it into
        the active slot/carousel: 'Upload media' (file chooser) -> wait the
        uploadImage XHR 200 (bytes stored) -> 'Add to Prompt' -> wait the dialog
        to CLOSE (commit registered) before returning, so the caller never
        submits before the image binds. Shared by I2V slots and R2V references."""
        uploaded: list[str] = []

        async def on_response(response: Any) -> None:
            if UPLOAD_IMAGE_ROUTE in response.url:
                uploaded.append(response.url)
                log.info(
                    "ui_automation_video.image_uploaded", target=log_label, status=response.status,
                )

        page.on("response", on_response)
        try:
            # Tier 1 = locale-agnostic icon selector (primary, every locale).
            # Tier 2 = the original English-text selector (#50) — catches an
            # icon-ligature change on an English profile. If BOTH miss, fail loud
            # with a screenshot. Short per-tier timeouts so two misses can't add
            # up to the silent ~34s hang that #56 was about.
            chooser = None
            last_err: Exception | None = None
            for sel in (UPLOAD_MEDIA_BUTTON, UPLOAD_MEDIA_BUTTON_TEXT):
                try:
                    async with page.expect_file_chooser(timeout=8000) as fc_info:
                        await page.locator(sel).first.click(timeout=4000)
                    chooser = await fc_info.value
                    break
                except Exception as e:
                    last_err = e
            if chooser is None:
                shot = await _capture_debug_screenshot(
                    page, out_dir, f"debug_upload_no_chooser_{log_label}.png",
                )
                msg = (
                    f"Neither the icon nor the text 'Upload media' selector opened a file "
                    f"chooser for {log_label!r} — Google likely changed the media dialog "
                    f"(issue #56). Screenshot: {shot}. Workaround: set the CHROME BROWSER "
                    f"PROFILE's language to English (chrome://settings/languages). NOTE: "
                    f"this is the Chrome PROFILE language, NOT the Google ACCOUNT language "
                    f"— changing only the Google account language does NOT work, because "
                    f"Flow follows the Chrome profile locale (and the --lang=en-US launch "
                    f"arg cannot override an already-configured profile)."
                )
                raise RuntimeError(
                    msg,
                ) from last_err
            await chooser.set_files(str(image))
            deadline = time.monotonic() + timeout_s
            while not uploaded and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
            if not uploaded:
                log.warning("ui_automation_video.upload_incomplete", target=log_label)
        finally:
            page.remove_listener("response", on_response)

        # 'Add to Prompt' = the only iconless button in the open dialog (its text
        # is localized; see the UPLOAD_MEDIA_BUTTON note). Select it structurally.
        add_btn = (
            page.locator(ADD_TO_PROMPT_DIALOG)
            .last.locator("button")
            .filter(has_not=page.locator("i.google-symbols"))
            .last
        )
        if await add_btn.count():
            await add_btn.click()
        try:
            await page.locator("[role='dialog']").last.wait_for(state="hidden", timeout=15_000)
        except Exception:
            log.warning("ui_automation_video.dialog_close_timeout", target=log_label)
        await page.wait_for_timeout(1500)

    @staticmethod
    async def _attach_references(
        page: Page, images: list[Path], *, out_dir: Path | None, timeout_s: float = 120.0,
    ) -> None:
        """R2V: attach up to MAX_REFERENCE_IMAGES reference images. References
        have no Start/End slots — each is added via the 'Add Media' button, which
        opens the same media dialog. Must run with the settings panel closed."""
        missing = [str(p) for p in images if not p.exists()]
        if missing:
            msg = f"reference image(s) not found: {missing}"
            raise FileNotFoundError(msg)
        attached = 0
        for i, img in enumerate(images):
            add_media = page.locator(ADD_MEDIA_BUTTON).first
            try:
                await add_media.wait_for(state="visible", timeout=8000)
            except Exception as e:
                if i == 0:
                    shot = await _capture_debug_screenshot(page, out_dir, "debug_no_add_media.png")
                    msg = f"'Add Media' button not found for the first reference. Screenshot: {shot}"
                    raise RuntimeError(
                        msg,
                    ) from e
                # Flow removes the Add-Media button once the per-model reference
                # cap is hit (omni_flash=7, veo_3_1_*=3). The DTO enforces this
                # when the model is known; for an unknown (None) model we only
                # learn the cap here. Proceed with what's attached + warn loudly.
                log.warning(
                    "ui_automation_video.reference_cap_reached",
                    attached=attached,
                    requested=len(images),
                    note="Flow hid 'Add Media' — per-model reference cap reached",
                )
                break
            await add_media.click()
            await page.wait_for_timeout(1000)
            await VideoGenerationMixin._upload_via_open_dialog(
                page, img, log_label=f"ref{i}", out_dir=out_dir, timeout_s=timeout_s,
            )
            attached += 1
            log.info("ui_automation_video.reference_attached", index=i)

    @staticmethod
    async def _select_video_aspect(page: Page, aspect: Aspect) -> None:
        """Click the aspect-ratio tab for `aspect` in the open mode dropdown.
        Non-fatal on miss — generation proceeds with Flow's default ratio."""
        candidates = VIDEO_ASPECT_TAB_SELECTORS.get(aspect)
        if candidates is None:
            log.warning("ui_automation_video.aspect_unsupported", aspect=aspect.value)
            return
        tab = await VideoGenerationMixin._probe_selector_cascade(
            page, "video_aspect_tab", candidates,
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
            msg = (
                f"no batchAsyncGenerateVideo* response within {timeout_s:.0f}s — "
                "did the submit fire? did reCAPTCHA fail silently?"
            )
            raise TimeoutError(
                msg,
            )
        return captured[0]

    async def generate_video(
        self,
        *,
        request: GenerateVideoRequest,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
        download: bool = True,
        on_started: VideoStartedCallback | None = None,
    ) -> VideoResult:
        """Generate ONE video by driving the Flow editor UI (T2V / I2V / R2V).

        Returns a `VideoResult` carrying both the terminal `VideoStatus` and the
        on-disk `local_path` (``None`` when ``download=False`` or the generation
        failed — callers should check ``result.status.succeeded`` first). Raises
        `RuntimeError` (no setup / editor control missing), `ValueError` (SQUARE
        aspect), `FileNotFoundError` (I2V/R2V image path missing),
        `AuthExpiredError` (401), `WafRejectionError` (403), `WireFormatError`
        (other non-200 / no media), or `TimeoutError`.

        ``on_started`` is called with a :class:`VideoStarted` as soon as the
        media_id is known (before polling completes) so the recorder can insert
        a STARTED row even if the long poll later fails.
        """
        if not self._setup_done or self._page is None:
            msg = "UiAutomationTransport.setup() must be called before generate_video()"
            raise RuntimeError(
                msg,
            )
        if request.aspect is Aspect.SQUARE:
            msg = (
                "video generation does not support the SQUARE aspect; "
                "use PORTRAIT (9:16) or LANDSCAPE (16:9)"
            )
            raise ValueError(
                msg,
            )
        async with self._generate_lock:
            return await self._generate_video_locked(
                request, out_dir, poll_timeout_s, download, on_started,
            )

    @staticmethod
    def _parse_generate_response(
        generate_resp: dict[str, Any],
    ) -> tuple[str, str | None]:
        """Validate HTTP status, extract media_name and flow_operation_id.

        Raises AuthExpiredError, WafRejectionError, or WireFormatError on bad
        status codes or missing media id. Returns (media_name, flow_operation_id).
        """
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
                detail="batchAsyncGenerateVideo* returned HTTP 403 — WAF / reCAPTCHA rejection",
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
        # content rejection surfaces later as a FAILED *status*, not empty media.
        # So a missing media[0] here is a genuine wire anomaly — WireFormatError.
        body: dict[str, Any] = cast("dict[str, Any]", generate_resp.get("body") or {})
        try:
            media_name = media_name_from_generate_response(body)
        except ValueError as e:
            # discovery carries only route + top-level KEY NAMES (not values).
            raise WireFormatError(
                detail=f"video generate response carries no media id: {e}",
                route=route,
                discovery={"route": route, "top_level_keys": sorted(body)},
            ) from e
        # Stored SEPARATELY from media_name even when they currently match —
        # spec explicitly keeps them distinct for future divergence.
        flow_operation_id: str | None = operation_name_from_generate_response(body)
        return media_name, flow_operation_id

    @staticmethod
    async def _fire_on_started(
        on_started: VideoStartedCallback,
        started: VideoStarted,
    ) -> None:
        """Invoke the on_started callback, awaiting it if it returns a coroutine."""
        import inspect

        result_or_coro = on_started(started)
        if inspect.isawaitable(result_or_coro):
            await result_or_coro

    async def _generate_video_locked(
        self,
        request: GenerateVideoRequest,
        out_dir: Path | None,
        poll_timeout_s: float,
        download: bool,
        on_started: VideoStartedCallback | None,
    ) -> VideoResult:
        """Serialized body of `generate_video` — runs under `self._generate_lock`
        (shared with `generate_images`: one Page, one DOM)."""
        page: Page = self._page  # type: ignore[assignment]  # guarded in generate_video

        await self._enter_editor(page, out_dir)
        await VideoGenerationMixin._wait_video_editor_ready(page)
        # Dismiss any Flow changelog / "What's new" overlay that may be on top
        # of the editor before we click into mode-switch / settings / submit (#26).
        await self._dismiss_blocking_overlays(page, out_dir)
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=out_dir)

        # Capture project_id from the editor URL as soon as we have it —
        # needed for VideoStarted provenance and recorded before the generate request.
        project_id: str | None = extract_project_id(page.url)

        # All settings-panel selections happen while the panel is open: model
        # (gates the 10s duration), sub-mode tab, aspect, count, duration.
        if request.model is not None:
            await VideoGenerationMixin._select_video_model(page, request.model, out_dir=out_dir)
        if request.mode is Mode.I2V:
            await VideoGenerationMixin._switch_video_sub_mode(page, "frames", out_dir=out_dir)
        elif request.mode is Mode.R2V:
            await VideoGenerationMixin._switch_video_sub_mode(page, "references", out_dir=out_dir)
        await VideoGenerationMixin._select_video_aspect(page, request.aspect)
        await VideoGenerationMixin._set_output_count(page, request.count)
        if request.duration is not None:
            await VideoGenerationMixin._select_video_duration(page, request.duration)
        await page.keyboard.press("Escape")  # close the settings panel
        await page.wait_for_timeout(600)

        # Attach images AFTER the panel is closed — the slots / 'Add Media' button
        # live in the main editor. This is what makes Flow fire StartImage /
        # StartAndEndImage / ReferenceImages instead of the plain Text route.
        if request.mode is Mode.I2V and request.start_image is not None:
            await VideoGenerationMixin._attach_frame(
                page, 0, "Start", request.start_image, out_dir=out_dir,
            )
            if request.end_image is not None:
                await VideoGenerationMixin._attach_frame(
                    page, 1, "End", request.end_image, out_dir=out_dir,
                )
        elif request.mode is Mode.R2V:
            await VideoGenerationMixin._attach_references(
                page, list(request.reference_images), out_dir=out_dir,
            )

        # Attach BOTH listeners synchronously BEFORE the prompt is submitted so
        # neither the generate response nor an early status poll is missed.
        generate_captured, generate_handler = VideoGenerationMixin._attach_video_response_listener(
            page,
        )
        status_captured, status_handler = VideoGenerationMixin._attach_status_response_listener(
            page,
        )
        try:
            await self._send_prompt(page, request.prompt, out_dir)

            generate_resp = await VideoGenerationMixin._await_generate_response(generate_captured)
            media_name, flow_operation_id = VideoGenerationMixin._parse_generate_response(
                generate_resp,
            )

            # Fire on_started BEFORE polling so the recorder can insert a STARTED
            # row even if the long poll later fails.
            if on_started is not None:
                started = VideoStarted(
                    media_id=media_name,
                    project_id=project_id,
                    flow_operation_id=flow_operation_id,
                )
                await VideoGenerationMixin._fire_on_started(on_started, started)

            status = await VideoGenerationMixin._poll_video_status(
                page, status_captured, media_name, timeout_s=poll_timeout_s,
            )
            local_path = (
                await self._download_video(status.media_id, out_dir, page)
                if download and status.succeeded
                else None
            )
            return VideoResult(
                status=status,
                local_path=local_path,
                project_id=project_id,
                flow_operation_id=flow_operation_id,
            )
        finally:
            # The Page is pooled and persistent — remove both listeners so they
            # never leak across calls.
            page.remove_listener("response", generate_handler)
            page.remove_listener("response", status_handler)
