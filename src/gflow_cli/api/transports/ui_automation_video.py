"""Video-generation methods for UiAutomationTransport.

`VideoGenerationMixin` keeps the orchestration (`generate_video`, `_submit_and_poll`,
`_generate_video_locked`). Its other parts are a linear chain of mixins in the
`_video_*` modules, and the shared selectors and helpers are in `_video_common` (#1249
wave 2). Every name this module exported before is re-exported here.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api._retry import parse_retry_after
from gflow_cli.api.transports import (
    _video_editor_mode,
    _video_frames,
    _video_network,
    _video_picker_project,
    _video_references,
    _video_settings,
)
from gflow_cli.api.transports._common import (
    extract_project_id,
    generation_error,
    migrated_route,
    raise_if_migrated,
)
from gflow_cli.api.transports._video_common import (
    _EDITOR_READY_ANCHOR as _EDITOR_READY_ANCHOR,
)
from gflow_cli.api.transports._video_common import (
    _GENERATION_ROUTE_RE as _GENERATION_ROUTE_RE,
)
from gflow_cli.api.transports._video_common import (
    _PICKER_PROJECT_OPTION_MATCH_JS as _PICKER_PROJECT_OPTION_MATCH_JS,
)
from gflow_cli.api.transports._video_common import (
    _PICKER_PROJECT_TRIGGER_ACTIVE_JS as _PICKER_PROJECT_TRIGGER_ACTIVE_JS,
)
from gflow_cli.api.transports._video_common import (
    _PROBE_TIMEOUT_MS as _PROBE_TIMEOUT_MS,
)
from gflow_cli.api.transports._video_common import (
    _START_AND_END_GENERATE_ROUTE,
    _T2V_GENERATE_ROUTE,
    PINNED_PLAYWRIGHT,
    STAGE_TIMEOUT_SHOT_S,
    SUBMIT_STAGE_TIMEOUT_S,
    SUPPORTED_PLAYWRIGHT_RANGE,
    VIDEO_GENERATE_ROUTES,
    _capture_debug_screenshot,
    _JsonObj,
    _JsonObjList,
    _playwright_version,
    screenshot_clause,
    zip_entity_refs,
)
from gflow_cli.api.transports._video_common import (  # noqa: F401
    ADD_MEDIA_BUTTON as ADD_MEDIA_BUTTON,
)
from gflow_cli.api.transports._video_common import (
    ADD_TO_PROMPT_DIALOG as ADD_TO_PROMPT_DIALOG,
)
from gflow_cli.api.transports._video_common import (
    AGENT_CHAT_PANEL_CLOSE_SELECTOR as AGENT_CHAT_PANEL_CLOSE_SELECTOR,
)
from gflow_cli.api.transports._video_common import (
    AGENTIC_UI_INDICATORS as AGENTIC_UI_INDICATORS,
)
from gflow_cli.api.transports._video_common import (
    COMPOSER_AGENT_TOGGLE_SELECTOR as COMPOSER_AGENT_TOGGLE_SELECTOR,
)
from gflow_cli.api.transports._video_common import (
    DIALOG_ANY as DIALOG_ANY,
)
from gflow_cli.api.transports._video_common import (
    ENTITY_ATTACH_DRIFT_HINT as ENTITY_ATTACH_DRIFT_HINT,
)
from gflow_cli.api.transports._video_common import (
    FRAME_SLOTS_STRUCT as FRAME_SLOTS_STRUCT,
)
from gflow_cli.api.transports._video_common import (
    LIBRARY_UI_INDICATORS as LIBRARY_UI_INDICATORS,
)
from gflow_cli.api.transports._video_common import (
    MODE_SWITCH_TRIGGER_SELECTORS as MODE_SWITCH_TRIGGER_SELECTORS,
)
from gflow_cli.api.transports._video_common import (
    MODEL_PICKER_TRIGGER as MODEL_PICKER_TRIGGER,
)
from gflow_cli.api.transports._video_common import (
    NON_CLASSIC_COHORT_INDICATORS as NON_CLASSIC_COHORT_INDICATORS,
)
from gflow_cli.api.transports._video_common import (
    PICKER_CONTEXT_INCLUDE as PICKER_CONTEXT_INCLUDE,
)
from gflow_cli.api.transports._video_common import (
    PICKER_GRID_SCROLL_ATTEMPTS as PICKER_GRID_SCROLL_ATTEMPTS,
)
from gflow_cli.api.transports._video_common import (
    PICKER_GRID_SCROLL_DELTA_PX as PICKER_GRID_SCROLL_DELTA_PX,
)
from gflow_cli.api.transports._video_common import (
    PICKER_GRID_SCROLL_MAX_ATTEMPTS as PICKER_GRID_SCROLL_MAX_ATTEMPTS,
)
from gflow_cli.api.transports._video_common import (
    PICKER_GRID_SCROLL_STALL_LIMIT as PICKER_GRID_SCROLL_STALL_LIMIT,
)
from gflow_cli.api.transports._video_common import (
    PICKER_INCLUDE_BUTTON as PICKER_INCLUDE_BUTTON,
)
from gflow_cli.api.transports._video_common import (
    PICKER_PERSONAGENS_TAB as PICKER_PERSONAGENS_TAB,
)
from gflow_cli.api.transports._video_common import (
    PICKER_PROJECT_MENU_OPEN as PICKER_PROJECT_MENU_OPEN,
)
from gflow_cli.api.transports._video_common import (
    PICKER_PROJECT_MENU_POLL_MS as PICKER_PROJECT_MENU_POLL_MS,
)
from gflow_cli.api.transports._video_common import (
    PICKER_PROJECT_MENU_POLLS as PICKER_PROJECT_MENU_POLLS,
)
from gflow_cli.api.transports._video_common import (
    PICKER_PROJECT_MENU_SCROLL_DELTA_PX as PICKER_PROJECT_MENU_SCROLL_DELTA_PX,
)
from gflow_cli.api.transports._video_common import (
    PICKER_PROJECT_MENU_SCROLL_MAX as PICKER_PROJECT_MENU_SCROLL_MAX,
)
from gflow_cli.api.transports._video_common import (
    PICKER_PROJECT_MENU_SCROLL_SETTLE_MS as PICKER_PROJECT_MENU_SCROLL_SETTLE_MS,
)
from gflow_cli.api.transports._video_common import (
    PICKER_PROJECT_SELECTOR_TRIGGERS as PICKER_PROJECT_SELECTOR_TRIGGERS,
)
from gflow_cli.api.transports._video_common import (
    PICKER_SEARCH_INPUT as PICKER_SEARCH_INPUT,
)
from gflow_cli.api.transports._video_common import (
    PICKER_VOZES_TAB as PICKER_VOZES_TAB,
)
from gflow_cli.api.transports._video_common import (
    REMOTE_PICKER_CLOSE_TIMEOUT_S as REMOTE_PICKER_CLOSE_TIMEOUT_S,
)
from gflow_cli.api.transports._video_common import (
    UPLOAD_IMAGE_ROUTE as UPLOAD_IMAGE_ROUTE,
)
from gflow_cli.api.transports._video_common import (
    UPLOAD_MEDIA_BUTTON as UPLOAD_MEDIA_BUTTON,
)
from gflow_cli.api.transports._video_common import (
    VIDEO_ASPECT_TAB_SELECTORS as VIDEO_ASPECT_TAB_SELECTORS,
)
from gflow_cli.api.transports._video_common import (
    VIDEO_MODEL_OPTION_SELECTORS as VIDEO_MODEL_OPTION_SELECTORS,
)
from gflow_cli.api.transports._video_common import (
    VIDEO_STATUS_ROUTE as VIDEO_STATUS_ROUTE,
)
from gflow_cli.api.transports._video_common import (
    VIDEO_SUBMODE_SELECTORS as VIDEO_SUBMODE_SELECTORS,
)
from gflow_cli.api.transports._video_common import (
    VIDEO_TAB_IN_MENU_SELECTORS as VIDEO_TAB_IN_MENU_SELECTORS,
)
from gflow_cli.api.transports._video_common import (
    _summarize_request_image_inputs as _summarize_request_image_inputs,
)
from gflow_cli.api.transports._video_common import (
    _upload_rejection_message as _upload_rejection_message,
)
from gflow_cli.api.transports._video_common import (
    capture_ui_diagnostics as capture_ui_diagnostics,
)
from gflow_cli.api.transports._video_common import (
    selector_drift_detail as selector_drift_detail,
)
from gflow_cli.api.transports._video_frames import FrameAttachMixin
from gflow_cli.api.video import (
    I2V_DEFAULT_MODEL,
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    VideoResult,
    VideoStarted,
    VideoStartedCallback,
    media_name_from_generate_response,
    operation_name_from_generate_response,
    workflow_id_from_generate_response,
)
from gflow_cli.errors import (
    AuthExpiredError,
    ConfigurationError,
    RateLimitError,
    TransportTimeoutError,
    WafRejectionError,
    WireFormatError,
)

# Re-exported: other code reads these names from this module.
from gflow_cli.storage import write_asset_async as write_asset_async  # noqa: F401

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)


class VideoGenerationMixin(FrameAttachMixin):
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

    @staticmethod
    async def _await_generate_response(
        captured: _JsonObjList,
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
        project_id: str | None = None,
        out_dir: Path | None = None,
        poll_timeout_s: float = 600.0,
        download: bool = True,
        on_started: VideoStartedCallback | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> VideoResult:
        """Generate ONE video by driving the Flow editor UI (T2V / I2V / R2V).

        If ``project_id`` is provided, navigates to that project. Otherwise
        creates a new one.

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
                request,
                project_id=project_id,
                out_dir=out_dir,
                poll_timeout_s=poll_timeout_s,
                download=download,
                on_started=on_started,
                name_resolver=name_resolver,
            )

    @staticmethod
    def _parse_generate_response(
        generate_resp: dict[str, Any],
    ) -> tuple[str, str | None, str | None]:
        """Validate HTTP status, extract media_name, flow_operation_id and workflow_id.

        Raises AuthExpiredError, WafRejectionError, or WireFormatError on bad
        status codes or missing media id. Returns (media_name, flow_operation_id,
        workflow_id).
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
        if http_status == 429:
            # #379 gave the image path a 429 branch; video never got one, so a
            # quota hit surfaced as "unexpected response shape" (#528).
            retry_after = parse_retry_after(generate_resp)
            raise RateLimitError(
                detail=(
                    "batchAsyncGenerateVideo* returned HTTP 429 — rate limit hit."
                    + (f" Retry after {retry_after:.0f}s." if retry_after is not None else "")
                ),
                status=429,
                route=route,
                retry_after=retry_after,
            )
        if http_status != 200:
            # #528: same misclassification as the image path — a 400 here is a
            # content-policy refusal, not a malformed request.
            raise generation_error(
                status=http_status if isinstance(http_status, int) else -1,
                route=route,
                body=generate_resp.get("body") or {},
            )
        # A video 200 ALWAYS carries media[0] (the asset slot — capture 02);
        # content rejection surfaces later as a FAILED *status*, not empty media.
        # So a missing media[0] here is a genuine wire anomaly — WireFormatError.
        body: dict[str, Any] = cast(_JsonObj, generate_resp.get("body") or {})
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
        return media_name, flow_operation_id, workflow_id_from_generate_response(body)

    @staticmethod
    async def _run_stage(
        coro: Any,
        *,
        stage: str,
        page: Page,
        out_dir: Path | None,
        timeout_s: float,
    ) -> Any:
        """Await *coro* under a named wall-clock deadline.

        Every UI probe in this transport carries its own per-selector timeout,
        so a stage that blows a whole-stage budget is not "slow Flow" — it is a
        Playwright call that stopped honouring its deadline, so the per-probe
        timer never fires. Left alone that presents as an unbounded SILENT
        hang: browser alive, no error, no further log line, no indication of
        which stage owns the wait.

        Known cause (2026-08-03): an **unpinned playwright** — an install that
        resolved 1.62.0 against a project locked to 1.59.0 wedged every
        ``video i2v`` run immediately after the frame upload. ``uv tool
        install <path>`` ignores ``uv.lock``, so a local/tool install silently
        picks the newest version the pyproject range allows; the range is now
        upper-bounded. The version check lives in the error message rather
        than in a preflight assert because a future driver regression may
        present the same way on an in-range version — the watchdog is the
        general net, the pin is the specific fix.

        On expiry this raises :class:`TransportTimeoutError` naming the stage,
        after a best-effort screenshot taken under its own short deadline (the
        page is by definition not answering, so an unbounded capture here would
        re-hang the code path meant to end the hang).
        """
        log.info("ui_automation_video.stage_started", stage=stage, timeout_s=timeout_s)
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(coro, timeout=timeout_s)
        except TimeoutError as e:
            shot: Path | None = None
            with contextlib.suppress(Exception):
                shot = await asyncio.wait_for(
                    _capture_debug_screenshot(page, out_dir, f"debug_stage_stalled_{stage}.png"),
                    timeout=STAGE_TIMEOUT_SHOT_S,
                )
            pw_version = _playwright_version()
            log.error(
                "ui_automation_video.stage_stalled",
                stage=stage,
                timeout_s=timeout_s,
                playwright_version=pw_version,
                screenshot=str(shot) if shot else None,
            )
            msg = (
                f"the {stage!r} stage did not complete within {timeout_s:.0f}s. "
                f"Every probe inside it is individually bounded, so this means "
                f"the browser stopped responding to automation — the run was "
                f"aborted instead of hanging silently. Nothing was submitted, "
                f"so no credit was spent. FIRST THING TO CHECK: your installed "
                f"playwright is {pw_version}; gflow is tested against "
                f"{SUPPORTED_PLAYWRIGHT_RANGE}. An out-of-range playwright is "
                f"the known cause of this exact stall (a 1.62.0 install against "
                f"a 1.59.0-locked project wedged every i2v run right after the "
                f"frame upload). `uv tool install <path>` IGNORES uv.lock, so "
                f"reinstall pinned: "
                f"`uv tool install --force --with playwright=={PINNED_PLAYWRIGHT} .`"
                f"{screenshot_clause(shot)}"
            )
            raise TransportTimeoutError(detail=msg) from e
        log.info(
            "ui_automation_video.stage_completed",
            stage=stage,
            elapsed_s=round(time.monotonic() - started, 2),
        )
        return result

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

    @staticmethod
    def _assert_i2v_route(
        captured_url: str,
        request: GenerateVideoRequest,
        effective_model: VideoModel | None,
    ) -> None:
        """Fail an i2v run whose submit did not route to the endpoint its
        frames required (issues #125, #626).

        Two mis-billing shapes, one check:

        * **All frames dropped** — Flow routes to ``batchAsyncGenerateVideoText``
          and bills a text-only clip. Observed live 2026-05-30 on omni-flash,
          which is why omni-flash was excluded from i2v entirely at the time.
        * **End frame dropped** — Flow keeps the start frame but degrades
          ``StartAndEndImage`` to ``StartImage``, billing a clip that starts
          right but was never interpolated toward the end frame. Not previously
          detectable: until #626 no model could legally carry an end frame that
          Flow might drop, so the T2V check above was sufficient.

        Both spend a credit before we can see them — the point is to refuse to
        report success, not to prevent the spend. This is what replaces the
        static capability table: it validates the route Flow actually used, so
        a staged or partial rollout surfaces as a loud failure on any account
        rather than a silently wrong video.
        """
        model_value = effective_model.value if effective_model else None
        if _T2V_GENERATE_ROUTE in captured_url:
            log.error(
                "ui_automation_video.i2v_routed_to_t2v",
                url=captured_url,
                model=model_value,
                issue_ref="#125",
            )
            raise WireFormatError(
                detail=(
                    "i2v request routed to the T2V endpoint "
                    f"({_T2V_GENERATE_ROUTE}); Flow dropped the start/end "
                    "frames and produced a text-only video (issue #125). "
                    "The credit was spent but the output is not an "
                    "interpolation — refusing to report success."
                ),
                route=_T2V_GENERATE_ROUTE,
            )

        has_end_ref = (
            request.end_image is not None
            or request.end_image_ref_id is not None
            or request.end_image_ref_name is not None
        )
        if has_end_ref and _START_AND_END_GENERATE_ROUTE not in captured_url:
            log.error(
                "ui_automation_video.i2v_end_frame_dropped",
                url=captured_url,
                model=model_value,
                issue_ref="#626",
            )
            raise WireFormatError(
                detail=(
                    "i2v request carried an END frame but Flow routed it to "
                    f"{captured_url!r} instead of "
                    f"{_START_AND_END_GENERATE_ROUTE}; the end frame was "
                    "dropped at submit, so the clip is not a first+last "
                    "interpolation (issue #626). The credit was spent — "
                    "refusing to report success. If this persists, Flow may "
                    "have rolled first+last back for this model or account: "
                    "re-run without --end-frame, or use a Veo 3.1 model."
                ),
                route=_START_AND_END_GENERATE_ROUTE,
            )

    @staticmethod
    def _resolve_i2v_model(
        request: GenerateVideoRequest,
        is_i2v_with_frames: bool,
    ) -> VideoModel | None:
        """Resolve the effective model for an I2V request.

        No model/frame combination is rejected any more: every current model
        carries both start-only and start+end i2v. omni-flash's START frame was
        wire-verified 2026-08-03 and its END frame on 2026-09-02 (#626) — two
        accounts, route-aborted at zero credits, both firing
        ``batchAsyncGenerateVideoStartAndEndImage`` with a non-null
        ``endImage``. What replaces the pre-submit guard is
        :meth:`_assert_i2v_route`, which checks the route Flow *actually* used
        rather than the one a static capability table predicted.

        Returns the effective model to use — defaults to ``I2V_DEFAULT_MODEL``
        when ``is_i2v_with_frames`` and no model is set.
        """
        effective_model = request.model
        if is_i2v_with_frames and effective_model is None:
            effective_model = I2V_DEFAULT_MODEL
            log.info(
                "ui_automation_video.i2v_model_defaulted",
                model=effective_model.value,
                issue_ref="#125",
            )
        return effective_model

    @staticmethod
    async def _attach_i2v_frames(
        page: Page,
        request: GenerateVideoRequest,
        *,
        out_dir: Path | None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        """Attach the Start (and optional End) I2V frame: local path, in-project
        asset UUID (#287), or remote display-name ref."""
        if request.start_image is not None:
            await VideoGenerationMixin._attach_frame(
                page, 0, "Start", request.start_image, out_dir=out_dir
            )
        elif request.start_image_ref_id is not None:
            await VideoGenerationMixin._attach_frame_by_media_id(
                page,
                0,
                "Start",
                request.start_image_ref_id,
                request.start_image_ref_display_name,
                out_dir=out_dir,
                project_name=request.project_name,
                local_path=request.start_image_ref_local_path,
                local_sha256=request.start_image_ref_local_sha256,
                name_resolver=name_resolver,
            )
        elif request.start_image_ref_name is not None:
            await VideoGenerationMixin._attach_remote_frame(
                page, 0, "Start", request.start_image_ref_name, out_dir=out_dir
            )
        if request.end_image is not None:
            await VideoGenerationMixin._attach_frame(
                page, 1, "End", request.end_image, out_dir=out_dir
            )
        elif request.end_image_ref_id is not None:
            await VideoGenerationMixin._attach_frame_by_media_id(
                page,
                1,
                "End",
                request.end_image_ref_id,
                request.end_image_ref_display_name,
                out_dir=out_dir,
                project_name=request.project_name,
                local_path=request.end_image_ref_local_path,
                local_sha256=request.end_image_ref_local_sha256,
                name_resolver=name_resolver,
            )
        elif request.end_image_ref_name is not None:
            await VideoGenerationMixin._attach_remote_frame(
                page, 1, "End", request.end_image_ref_name, out_dir=out_dir
            )

    @staticmethod
    async def _attach_r2v_references(
        page: Page, request: GenerateVideoRequest, *, out_dir: Path | None
    ) -> None:
        """Attach R2V character entities, local reference images, remote refs, and audio."""
        if request.reference_entities:
            await VideoGenerationMixin._attach_character_entities(
                page,
                zip_entity_refs(request.reference_entities, request.reference_entity_names),
                out_dir=out_dir,
            )
        if request.reference_images:
            await VideoGenerationMixin._attach_references(
                page, list(request.reference_images), out_dir=out_dir
            )
        if request.ref_names:
            await VideoGenerationMixin._attach_remote_references(
                page, list(request.ref_names), out_dir=out_dir
            )
        if request.reference_audio:
            await VideoGenerationMixin._attach_reference_audio(
                page, request.reference_audio, out_dir=out_dir
            )

    @staticmethod
    async def _attach_media_inputs(
        page: Page,
        request: GenerateVideoRequest,
        *,
        out_dir: Path | None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        """Attach I2V frames or R2V references/entities to the editor before submit."""
        if request.mode is Mode.I2V:
            await VideoGenerationMixin._attach_i2v_frames(
                page, request, out_dir=out_dir, name_resolver=name_resolver
            )
        elif request.mode is Mode.R2V:
            await VideoGenerationMixin._attach_r2v_references(page, request, out_dir=out_dir)

    async def _submit_and_poll(
        self,
        page: Page,
        request: GenerateVideoRequest,
        is_i2v_with_frames: bool,
        effective_model: VideoModel | None,
        project_id: str | None,
        out_dir: Path | None,
        poll_timeout_s: float,
        download: bool,
        on_started: VideoStartedCallback | None,
        ui_driver: Any,
    ) -> VideoResult:
        """Submit the prompt, await and validate the generate response, then poll."""
        generate_captured, generate_handler = VideoGenerationMixin._attach_video_response_listener(
            page
        )
        status_captured, status_handler = VideoGenerationMixin._attach_status_response_listener(
            page
        )
        generate_resp: dict[str, Any] = {}
        expected_ents = set(request.reference_entities)
        try:
            async with self._intercept_reference_entities(page, expected_ents):
                # Watchdog: this is the window that hung silently after
                # `frame_attached` — overlay dismissal + prompt-box lookup +
                # typing + submit, all of which are individually bounded.
                await VideoGenerationMixin._run_stage(
                    ui_driver.send_prompt(page, request.prompt, out_dir=out_dir),
                    stage="send_prompt",
                    page=page,
                    out_dir=out_dir,
                    timeout_s=SUBMIT_STAGE_TIMEOUT_S,
                )
                generate_resp = await VideoGenerationMixin._await_generate_response(
                    generate_captured
                )

            # Layer-2 backstop (issues #125, #626): for i2v, the request MUST
            # have routed to the endpoint matching the frames it carried.
            if is_i2v_with_frames:
                VideoGenerationMixin._assert_i2v_route(
                    str(generate_resp.get("url") or ""), request, effective_model
                )

            if request.reference_entities:
                VideoGenerationMixin._assert_entities_attached(
                    generate_resp, expected=list(request.reference_entities)
                )

            media_name, flow_operation_id, workflow_id = (
                VideoGenerationMixin._parse_generate_response(generate_resp)
            )

            if on_started is not None:
                started = VideoStarted(
                    media_id=media_name,
                    project_id=project_id,
                    flow_operation_id=flow_operation_id,
                    workflow_id=workflow_id,
                )
                await VideoGenerationMixin._fire_on_started(on_started, started)

            status = await VideoGenerationMixin._poll_video_status(
                page,
                status_captured,
                media_name,
                timeout_s=poll_timeout_s,
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
                workflow_id=workflow_id,
            )
        finally:
            # The Page is pooled and persistent — remove both listeners so they
            # never leak across calls.
            page.remove_listener("response", generate_handler)
            page.remove_listener("response", status_handler)

    async def _generate_video_locked(
        self,
        request: GenerateVideoRequest,
        *,
        project_id: str | None = None,
        out_dir: Path | None,
        poll_timeout_s: float,
        download: bool,
        on_started: VideoStartedCallback | None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> VideoResult:
        """Serialized body of `generate_video` — runs under `self._generate_lock`
        (shared with `generate_images`: one Page, one DOM)."""
        is_i2v_with_frames = request.mode is Mode.I2V and any(
            (
                request.start_image,
                request.start_image_ref_name,
                request.start_image_ref_id,
                request.end_image,
                request.end_image_ref_name,
                request.end_image_ref_id,
            )
        )
        # Defense-in-depth model/mode guard (issue #125). Pure DTO check — runs
        # BEFORE any browser interaction so a bad combination fails instantly
        # with no DOM state mutated and no credit risk.
        effective_model = VideoGenerationMixin._resolve_i2v_model(request, is_i2v_with_frames)

        page: Page = self._page  # type: ignore[assignment]  # guarded in generate_video

        # #639: an account Google moved to flow.google.com (or GFLOW_CLI_FLOW_HOST
        # forcing that host) is driven by the migrated composer. Decided twice —
        # here, when the bootstrap page already hopped or the host is forced, and
        # again after entering the project, because the hop is a client-side
        # navigation the labs app performs AFTER the project page has loaded.
        from gflow_cli.api.transports.migrated_composer import (  # noqa: PLC0415
            migrated_can_serve,
            run_video,
        )
        from gflow_cli.config import get_settings  # noqa: PLC0415

        # #792: a FAILED migrated run leaves this page on the project URL on
        # purpose, so the incident bundle is captured from a live page instead of
        # about:blank. Drain that deferred park HERE — before the route decision
        # reads page.url, and before anything re-enters a still-mounted composer.
        # It also covers a client that still retries this call: since #1010 the
        # page-owned image call runs once, but a caller re-entering after a failure
        # must not resume on a dirty composer.
        await self.park_deferred_page()
        flow_host = get_settings().flow_host
        prefer = migrated_can_serve(request, project_id)
        route = migrated_route(page.url, flow_host, prefer_migrated=prefer)
        if route == "labs":
            await self._enter_editor(page, out_dir, project_id=project_id)
            await VideoGenerationMixin._wait_video_editor_ready(page)
            # Dismiss any Flow changelog / "What's new" overlay that may be on top
            # of the editor before we click into mode-switch / settings / submit (#26).
            await self._dismiss_blocking_overlays(page, out_dir)
            route = migrated_route(page.url, flow_host, prefer_migrated=prefer)
        if route == "blocked":
            raise_if_migrated(page, at="flow_host_kill_switch")
        if route == "migrated":
            try:
                result = await run_video(
                    page,
                    request,
                    project_id=project_id,
                    save=partial(self._save_clip, out_dir=out_dir),
                    poll_timeout_s=poll_timeout_s,
                    download=download,
                    on_started=on_started,
                )
            except Exception:
                # #792: do NOT park here. The bundle is staged from THIS page by
                # FlowApiClient._capture_incident ("while the page is still
                # alive"); parking first handed the triager an about:blank DOM and
                # a white screenshot. Deferred to park_deferred_page().
                self._deferred_park_pending = True
                raise
            except BaseException:
                # Cancellation/KeyboardInterrupt: no bundle is staged for these, so
                # nothing is waiting on the page. Attempt the park inline -- but a
                # re-delivered cancel can pre-empt it at the `goto` await, and
                # `except Exception` cannot catch that. The next run's drain is the
                # guarantee; this is the courtesy.
                self._deferred_park_pending = True
                await self._park_composer_page(page, event="migrated.page_park_failed")
                raise
            # The pooled page would otherwise stay on flow.google.com/project/<id>,
            # and the NEXT request on this client would be routed by that URL
            # instead of by its own shape (an unmoved account's i2v → exit 36,
            # or a silently reused project). Park it; the next run navigates.
            await self._park_composer_page(page, event="migrated.page_park_failed")
            return result

        if request.resolution is not None:
            # #787 drives the resolution radio on flow.google.com only. This driver has no
            # resolution step, so honouring the flag here would mean silently billing
            # Flow's default for a user who asked for something else. Refused here, on the
            # labs route only (the migrated one returned above), before any submit.
            raise ConfigurationError(
                detail=(
                    f"--resolution {request.resolution} is not driven on the labs Flow "
                    "editor yet; only the flow.google.com composer selects a resolution."
                ),
                remediation_hint="Drop --resolution to accept Flow's default on this account.",
            )

        # #299: the video path binds through the mode policy like images do —
        # get_ui_driver switches to the required arm, VERIFIES via a DOM
        # re-probe, and fails fast pre-submit (UiModeUnavailableError, exit 28)
        # when the arm is unreachable, so an agentic cohort flip costs $0
        # instead of a mid-flow selector-drift failure. The video pipeline only
        # has a classic driver, so every request clamps to classic-required:
        # ``auto`` ≡ classic until an agentic video driver exists, and an
        # env-sourced ``agentic`` (set for image workflows) degrades to classic
        # with a warning — only the explicit ``--ui-mode agentic`` flag
        # rejects, at the CLI edge (exit 2). The bind runs AFTER the editor
        # mounts + overlays clear: the policy probes the live DOM.
        from gflow_cli.api.transports.drivers.factory import (  # noqa: PLC0415
            get_ui_driver,
        )
        from gflow_cli.config import UiMode, resolve_ui_mode  # noqa: PLC0415

        base_mode = request.ui_mode if request.ui_mode is not None else resolve_ui_mode(None)
        if base_mode is UiMode.AGENTIC:
            log.warning(
                "ui_automation_video.ui_mode_agentic_clamped",
                requested=base_mode.value,
                bound="classic",
                reason="no agentic video driver exists; classic required",
            )
        ui_driver = await get_ui_driver(page, ui_mode=UiMode.CLASSIC, transport=self)
        # The CLASSIC recovery inside the bind may have RELOADED the page (the
        # sanctioned rescue re-rolls the cohort) — a fresh load can re-mount
        # the #26 overlay and drop editor hydration, so re-run both guards
        # before the driver starts clicking. Cheap no-ops when nothing
        # reloaded.
        await VideoGenerationMixin._wait_video_editor_ready(page)
        await self._dismiss_blocking_overlays(page, out_dir)
        await ui_driver.switch_to_video_mode(page, out_dir=out_dir)

        # Capture project_id from the editor URL as soon as we have it —
        # needed for VideoStarted provenance and recorded before the generate request.
        # Falls back to the caller-supplied id when the URL carries none.
        project_id = extract_project_id(page.url) or project_id

        # All settings-panel selections happen while the panel is open: model
        # (gates the 10s duration), sub-mode tab, aspect, count, duration.
        # For i2v, model selection is REQUIRED (required=True): a silent miss
        # would run i2v on whatever model Flow last had selected, breaking the
        # request contract (cost tier, duration cap, end-frame capability —
        # refs #125), so _select_video_model raises here — before any frame
        # attach or submit, spending no credit.
        await ui_driver.configure_video_settings(page, request, out_dir=out_dir)

        # Attach images AFTER the panel is closed — the slots / 'Add Media' button
        # live in the main editor. This is what makes Flow fire StartImage /
        # StartAndEndImage / ReferenceImages instead of the plain Text route.
        await VideoGenerationMixin._attach_media_inputs(
            page, request, out_dir=out_dir, name_resolver=name_resolver
        )

        # Submit prompt, await generate response, validate, and poll for status.
        return await self._submit_and_poll(
            page,
            request,
            is_i2v_with_frames,
            effective_model,
            project_id,
            out_dir,
            poll_timeout_s,
            download,
            on_started,
            ui_driver,
        )


# The moved methods still call their siblings as ``VideoGenerationMixin._x`` (tests patch
# them there), so each part's module needs the name bound now that the class exists.
_video_network.VideoGenerationMixin = VideoGenerationMixin
_video_editor_mode.VideoGenerationMixin = VideoGenerationMixin
_video_settings.VideoGenerationMixin = VideoGenerationMixin
_video_picker_project.VideoGenerationMixin = VideoGenerationMixin
_video_references.VideoGenerationMixin = VideoGenerationMixin
_video_frames.VideoGenerationMixin = VideoGenerationMixin
