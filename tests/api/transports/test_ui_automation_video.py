"""Unit tests for the video-generation mixin (ui_automation_video.py)."""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import (
    _PICKER_PROJECT_OPTION_MATCH_JS,
    _PICKER_PROJECT_TRIGGER_ACTIVE_JS,
    ADD_MEDIA_BUTTON,
    DIALOG_ANY,
    FRAME_SLOT_BY_LABEL,
    FRAME_SLOTS_STRUCT,
    PICKER_CONTEXT_INCLUDE,
    PICKER_GRID_SCROLL_ATTEMPTS,
    PICKER_GRID_SCROLL_STALL_LIMIT,
    PICKER_INCLUDE_BUTTON,
    PICKER_PROJECT_MENU_OPEN,
    PICKER_PROJECT_MENU_POLL_MS,
    PICKER_PROJECT_MENU_POLLS,
    PICKER_PROJECT_SELECTOR_TRIGGERS,
    PICKER_SEARCH_INPUT,
    VideoGenerationMixin,
    _upload_rejection_message,
)
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel, VideoStatus
from gflow_cli.errors import (
    AuthExpiredError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)


def _make_listener_page() -> tuple[MagicMock, list]:
    """A fake page that records the handlers registered via page.on()."""
    page = MagicMock()
    handlers: list = []
    page.on = MagicMock(side_effect=lambda event, cb: handlers.append((event, cb)))
    page.remove_listener = MagicMock()
    return page, handlers


def _make_response(*, url: str, status: int = 200, body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.url = url
    resp.status = status
    resp.json = AsyncMock(return_value=body if body is not None else {"media": []})
    return resp


class TestUploadRejectionMessage:
    """`_upload_rejection_message` decides whether the uploadImage response
    status means the frame upload was rejected. A silent 4xx here previously
    committed an empty slot and fell back to T2V (#125)."""

    def test_ok_status_no_message(self) -> None:
        assert _upload_rejection_message(200, "Start") is None

    def test_none_status_no_message(self) -> None:
        # No uploadImage response seen at all — handled separately (incomplete).
        assert _upload_rejection_message(None, "Start") is None

    def test_400_is_rejected(self) -> None:
        msg = _upload_rejection_message(400, "Start")
        assert msg is not None
        assert "400" in msg
        assert "Start" in msg

    def test_500_is_rejected(self) -> None:
        assert _upload_rejection_message(500, "End") is not None


_T2V_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText"
_I2V_START_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoStartImage"
_I2V_START_END_URL = (
    "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoStartAndEndImage"
)


class TestAttachVideoResponseListener:
    @pytest.mark.asyncio
    async def test_captures_a_generate_route_response(self) -> None:
        page, handlers = _make_listener_page()
        captured, _handler = VideoGenerationMixin._attach_video_response_listener(page)
        assert handlers and handlers[0][0] == "response"
        await handlers[0][1](_make_response(url=_T2V_URL, body={"media": [{"name": "m"}]}))
        assert len(captured) == 1
        assert captured[0]["status"] == 200
        assert captured[0]["body"]["media"][0]["name"] == "m"

    @pytest.mark.asyncio
    async def test_ignores_unrelated_routes(self) -> None:
        page, handlers = _make_listener_page()
        captured, _handler = VideoGenerationMixin._attach_video_response_listener(page)
        await handlers[0][1](_make_response(url="https://example.com/other"))
        assert captured == []

    @pytest.mark.asyncio
    async def test_parse_failure_is_non_fatal(self) -> None:
        page, handlers = _make_listener_page()
        captured, _handler = VideoGenerationMixin._attach_video_response_listener(page)
        resp = _make_response(url=_T2V_URL)
        resp.json = AsyncMock(side_effect=ValueError("bad json"))
        await handlers[0][1](resp)  # must not raise
        assert captured == []


_STATUS_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus"


def _status_resp(media_id: str, status: str, *, failure_reasons: list | None = None) -> dict:
    """Build a captured-status dict shaped like Flow's check-status response."""
    media_status: dict = {"mediaGenerationStatus": status}
    if failure_reasons:
        media_status["failureReasons"] = failure_reasons
        media_status["error"] = {"message": "PUBLIC_ERROR_IP_INPUT_IMAGE"}
    body = {"media": [{"name": media_id, "mediaMetadata": {"mediaStatus": media_status}}]}
    return {"status": 200, "url": _STATUS_URL, "body": body}


class TestAttachStatusResponseListener:
    @pytest.mark.asyncio
    async def test_captures_status_route_only(self) -> None:
        page, handlers = _make_listener_page()
        captured, _handler = VideoGenerationMixin._attach_status_response_listener(page)
        await handlers[0][1](_make_response(url=_STATUS_URL, body={"media": []}))
        await handlers[0][1](_make_response(url=_T2V_URL, body={"media": []}))
        assert len(captured) == 1


class TestPollVideoStatus:
    @pytest.mark.asyncio
    async def test_returns_on_successful(self) -> None:
        page = MagicMock()
        captured = [
            _status_resp("m", "MEDIA_GENERATION_STATUS_SCHEDULED"),
            _status_resp("m", "MEDIA_GENERATION_STATUS_ACTIVE"),
            _status_resp("m", "MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        ]
        result = await VideoGenerationMixin._poll_video_status(
            page, captured, "m", timeout_s=2.0, poll_interval_s=0.05
        )
        assert result.succeeded is True

    @pytest.mark.asyncio
    async def test_returns_failed_status(self) -> None:
        page = MagicMock()
        captured = [
            _status_resp("m", "MEDIA_GENERATION_STATUS_FAILED", failure_reasons=["IP_PROHIBITED"])
        ]
        result = await VideoGenerationMixin._poll_video_status(
            page, captured, "m", timeout_s=2.0, poll_interval_s=0.05
        )
        assert result.is_terminal is True
        assert result.succeeded is False
        assert result.failure_reasons == ("IP_PROHIBITED",)

    @pytest.mark.asyncio
    async def test_waits_for_a_late_terminal_status(self) -> None:
        page = MagicMock()
        captured: list[dict] = [_status_resp("m", "MEDIA_GENERATION_STATUS_SCHEDULED")]

        async def _append_later() -> None:
            await asyncio.sleep(0.1)
            captured.append(_status_resp("m", "MEDIA_GENERATION_STATUS_SUCCESSFUL"))

        asyncio.create_task(_append_later())
        result = await VideoGenerationMixin._poll_video_status(
            page, captured, "m", timeout_s=2.0, poll_interval_s=0.05
        )
        assert result.succeeded is True

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        page = MagicMock()
        with pytest.raises(TimeoutError, match="no terminal status"):
            await VideoGenerationMixin._poll_video_status(
                page, [], "m", timeout_s=0.2, poll_interval_s=0.05
            )

    @pytest.mark.asyncio
    async def test_401_response_raises_auth_expired_error_immediately(self) -> None:
        page = MagicMock()
        captured = [
            {"status": 401, "url": _STATUS_URL, "body": {}},
        ]
        with pytest.raises(AuthExpiredError, match="session expired mid-poll"):
            await VideoGenerationMixin._poll_video_status(
                page, captured, "m", timeout_s=60.0, poll_interval_s=0.05
            )

    @pytest.mark.asyncio
    async def test_401_after_in_progress_raises_auth_expired_error(self) -> None:
        page = MagicMock()
        # Simulates session expiry mid-poll: some ACTIVE responses arrive first, then a 401
        captured: list[dict[str, Any]] = [
            _status_resp("m", "MEDIA_GENERATION_STATUS_SCHEDULED"),
            _status_resp("m", "MEDIA_GENERATION_STATUS_ACTIVE"),
            {"status": 401, "url": _STATUS_URL, "body": {}},
        ]
        with pytest.raises(AuthExpiredError, match="session expired mid-poll"):
            await VideoGenerationMixin._poll_video_status(
                page, captured, "m", timeout_s=60.0, poll_interval_s=0.05
            )


def _cascade_page(visible: set[str]) -> MagicMock:
    """A fake page whose locator(sel) is 'visible' only for sel in `visible`."""
    page = MagicMock()

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        loc.first = loc
        if sel in visible:
            loc.wait_for = AsyncMock()
        else:
            loc.wait_for = AsyncMock(side_effect=Exception("not visible"))
        loc.click = AsyncMock()
        # `_select_video_model` counts VISIBLE matches rather than resolving
        # `.first`, so the fake has to model presence as well as visibility.
        hit = sel in visible
        loc.count = AsyncMock(return_value=1 if hit else 0)
        loc.nth = MagicMock(return_value=loc)
        loc.is_visible = AsyncMock(return_value=hit)
        return loc

    page.locator = MagicMock(side_effect=_locator)
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.evaluate = AsyncMock(return_value=[])
    return page


class TestProbeSelectorCascade:
    @pytest.mark.asyncio
    async def test_returns_first_visible_match(self) -> None:
        page = _cascade_page({"b"})
        loc = await VideoGenerationMixin._probe_selector_cascade(
            page, "x", ("a", "b", "c"), timeout_ms=10
        )
        assert loc is not None

    @pytest.mark.asyncio
    async def test_returns_none_when_all_miss(self) -> None:
        page = _cascade_page(set())
        loc = await VideoGenerationMixin._probe_selector_cascade(
            page, "x", ("a", "b"), timeout_ms=10
        )
        assert loc is None


class TestSwitchToVideoMode:
    @pytest.mark.asyncio
    async def test_opens_dropdown_then_clicks_video_tab(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod

        trigger = mod.MODE_SWITCH_TRIGGER_SELECTORS[0]
        video_tab = mod.VIDEO_TAB_IN_MENU_SELECTORS[0]
        page = _cascade_page({trigger, video_tab})
        await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)
        # both the trigger and the in-menu video tab were located
        assert page.locator.call_count >= 2

    @pytest.mark.asyncio
    async def test_raises_when_trigger_missing(self) -> None:
        page = _cascade_page(set())
        with pytest.raises(UiSelectorDriftError, match="mode_switch_trigger"):
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)

    @pytest.mark.asyncio
    async def test_raises_when_video_tab_missing(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod

        page = _cascade_page({mod.MODE_SWITCH_TRIGGER_SELECTORS[0]})
        with pytest.raises(UiSelectorDriftError, match="Video tab"):
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)

    @pytest.mark.asyncio
    async def test_submode_miss_raises_drift_error(self) -> None:
        # The sub-mode probe is the same selector-cascade pattern as the
        # mode-switch trigger and must carry the same typed-error contract.
        page = _cascade_page(set())
        with pytest.raises(UiSelectorDriftError, match="video_submode_references"):
            await VideoGenerationMixin._switch_video_sub_mode(page, "references", out_dir=None)


class TestWaitVideoEditorReady:
    @pytest.mark.asyncio
    async def test_returns_when_anchor_visible(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod

        page = _cascade_page({mod._EDITOR_READY_ANCHOR})
        await VideoGenerationMixin._wait_video_editor_ready(page)  # must not raise

    @pytest.mark.asyncio
    async def test_timeout_is_non_fatal(self) -> None:
        page = _cascade_page(set())
        await VideoGenerationMixin._wait_video_editor_ready(page)  # logs, must not raise


class TestSetOutputCountOne:
    """Count=1 path of `_set_output_count` (the count most sensitive to #404)."""

    @pytest.mark.asyncio
    async def test_clicks_the_legacy_count_one_tab(self) -> None:
        # Legacy cohort: the pre-#404 '1x' label is still probed as a fallback.
        sel = "[role='tab']:text-is('1x')"
        page = _cascade_page({sel})
        await VideoGenerationMixin._set_output_count(page, 1)
        page.locator.assert_any_call(sel)

    @pytest.mark.asyncio
    async def test_clicks_the_renamed_x1_tab(self) -> None:
        """#404: Flow renamed the count-1 label from '1x' to 'x1' — the setter
        must probe BOTH label cohorts before declaring a miss."""
        sel = "[role='tab']:text-is('x1')"
        page = _cascade_page({sel})
        await VideoGenerationMixin._set_output_count(page, 1)
        page.locator.assert_any_call(sel)

    @pytest.mark.asyncio
    async def test_missing_count_tab_raises_drift_error(self) -> None:
        # Count is a credit multiplier: every request carries a definite 1-4
        # value, and Flow's sticky default (x2) silently doubles spend for
        # count=1 — so a probe miss must refuse pre-submit, exactly like the
        # duration probe (#288), not proceed on the default.
        page = _cascade_page(set())
        with pytest.raises(UiSelectorDriftError) as exc_info:
            await VideoGenerationMixin._set_output_count(page, 1)
        msg = str(exc_info.value)
        assert "count=1" in msg
        assert "bills" in msg  # the refusal explains the billing consequence
        assert "Screenshot:" not in msg  # no out_dir -> no screenshot clause

    @pytest.mark.asyncio
    async def test_missing_count_tab_captures_screenshot(self, tmp_path: Path) -> None:
        page = _cascade_page(set())
        with pytest.raises(UiSelectorDriftError) as exc_info:
            await VideoGenerationMixin._set_output_count(page, 1, out_dir=tmp_path)
        assert "Screenshot:" in str(exc_info.value)
        page.screenshot.assert_awaited()


class TestSelectVideoModel:
    @pytest.mark.asyncio
    async def test_clicks_trigger_then_option(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod
        from gflow_cli.api.video import VideoModel

        trig = mod.MODEL_PICKER_TRIGGER
        opt = mod.VIDEO_MODEL_OPTION_SELECTORS[VideoModel.VEO_3_1_FAST]
        page = _cascade_page({trig, opt})
        await VideoGenerationMixin._select_video_model(page, VideoModel.VEO_3_1_FAST, out_dir=None)
        page.locator.assert_any_call(trig)
        page.locator.assert_any_call(opt)

    @pytest.mark.asyncio
    async def test_missing_trigger_is_now_fatal(self) -> None:
        """Was `test_missing_trigger_is_non_fatal` — it encoded the bug.

        A missing picker meant the run proceeded on Flow's current selection and
        CHARGED CREDITS for that tier. We only reach here when a model was
        explicitly requested, so there is nothing to legitimately fall back to.
        """
        from gflow_cli.api.video import VideoModel
        from gflow_cli.errors import VideoModelSelectionError

        page = _cascade_page(set())
        with pytest.raises(VideoModelSelectionError):
            await VideoGenerationMixin._select_video_model(
                page, VideoModel.OMNI_FLASH, out_dir=None
            )

    @pytest.mark.asyncio
    async def test_missing_option_escapes_to_recover_then_refuses(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod
        from gflow_cli.api.video import VideoModel
        from gflow_cli.errors import VideoModelSelectionError

        # trigger visible but the option is not -> Escape closes the stray menu,
        # and the run is refused rather than generating on the wrong tier.
        page = _cascade_page({mod.MODEL_PICKER_TRIGGER})
        with pytest.raises(VideoModelSelectionError):
            await VideoGenerationMixin._select_video_model(
                page, VideoModel.OMNI_FLASH, out_dir=None
            )
        page.keyboard.press.assert_any_call("Escape")


class TestSelectVideoDuration:
    @pytest.mark.asyncio
    async def test_clicks_the_duration_tab(self) -> None:
        sel = "[role='tab']:text-is('6s')"
        page = _cascade_page({sel})
        await VideoGenerationMixin._select_video_duration(page, 6, out_dir=None)
        page.locator.assert_any_call(sel)

    @pytest.mark.asyncio
    async def test_missing_duration_tab_raises_drift_error(self) -> None:
        # #288: --duration is always explicit at this point (the classic driver
        # guards on request.duration is not None) — a silent 4->8 substitution
        # corrupts downstream timeline math, so a probe miss must fail fast.
        from gflow_cli.errors import UiSelectorDriftError

        page = _cascade_page(set())
        with pytest.raises(UiSelectorDriftError) as exc_info:
            await VideoGenerationMixin._select_video_duration(page, 4, out_dir=None)
        msg = str(exc_info.value)
        assert "4s" in msg
        assert "--duration" in msg  # remediation hint: omit --duration for Flow's default
        assert "Screenshot:" not in msg  # no out_dir -> no screenshot clause

    @pytest.mark.asyncio
    async def test_missing_duration_tab_captures_screenshot(self, tmp_path: Path) -> None:
        from gflow_cli.errors import UiSelectorDriftError

        page = _cascade_page(set())
        with pytest.raises(UiSelectorDriftError) as exc_info:
            await VideoGenerationMixin._select_video_duration(page, 6, out_dir=tmp_path)
        assert "Screenshot:" in str(exc_info.value)
        page.screenshot.assert_awaited()


class TestRunStage:
    """`_run_stage` converts a wedged UI stage into a fast, named error.

    The bug: `gflow video i2v` hung SILENTLY after `frame_attached` — browser
    alive, no error, no further log line — because a Playwright call stopped
    honouring its own per-probe deadline, so no inner timeout ever fired.
    """

    @pytest.mark.asyncio
    async def test_passes_through_result_when_fast(self) -> None:
        page = _cascade_page(set())

        async def _work() -> str:
            return "ok"

        got = await VideoGenerationMixin._run_stage(
            _work(), stage="send_prompt", page=page, out_dir=None, timeout_s=5.0
        )
        assert got == "ok"

    @pytest.mark.asyncio
    async def test_stalled_stage_raises_named_transport_timeout(self) -> None:
        page = _cascade_page(set())

        async def _hang() -> None:
            await asyncio.sleep(10)

        with pytest.raises(TransportTimeoutError) as exc_info:
            await VideoGenerationMixin._run_stage(
                _hang(), stage="send_prompt", page=page, out_dir=None, timeout_s=0.05
            )
        msg = str(exc_info.value)
        assert "send_prompt" in msg  # names the stage that owns the wait
        assert "no credit was spent" in msg  # nothing was submitted

    @pytest.mark.asyncio
    async def test_stall_screenshot_hang_does_not_re_hang(self, tmp_path: Path) -> None:
        """The page is wedged by definition, so an unbounded capture would
        re-hang the very path meant to end the hang — it must stay bounded."""
        page = _cascade_page(set())

        async def _never_returns(**_k: object) -> None:
            await asyncio.sleep(30)

        page.screenshot = AsyncMock(side_effect=_never_returns)

        async def _hang() -> None:
            await asyncio.sleep(10)

        from gflow_cli.api.transports import ui_automation_video as mod

        with patch.object(mod, "STAGE_TIMEOUT_SHOT_S", 0.05):
            with pytest.raises(TransportTimeoutError):
                await VideoGenerationMixin._run_stage(
                    _hang(), stage="send_prompt", page=page, out_dir=tmp_path, timeout_s=0.05
                )

    @pytest.mark.asyncio
    async def test_stall_emits_diagnosable_event(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        page = _cascade_page(set())

        async def _hang() -> None:
            await asyncio.sleep(10)

        with pytest.raises(TransportTimeoutError):
            await VideoGenerationMixin._run_stage(
                _hang(), stage="send_prompt", page=page, out_dir=None, timeout_s=0.05
            )
        events = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.stage_stalled"
        ]
        assert len(events) == 1
        assert events[0]["stage"] == "send_prompt"


class TestSetOutputCount:
    @pytest.mark.asyncio
    async def test_clicks_the_count_n_tab(self) -> None:
        sel = "[role='tab']:text-is('x3')"
        page = _cascade_page({sel})
        await VideoGenerationMixin._set_output_count(page, 3)
        page.locator.assert_any_call(sel)

    @pytest.mark.asyncio
    async def test_legacy_affix_fallback_for_count_n(self) -> None:
        # Affix-agnostic matching: if Flow flips the label back to '3x' (the
        # #404 rename class), the digit-keyed fallback finds it with no code
        # change instead of refusing.
        sel = "[role='tab']:text-is('3x')"
        page = _cascade_page({sel})
        await VideoGenerationMixin._set_output_count(page, 3)
        page.locator.assert_any_call(sel)


class TestSelectVideoAspect:
    @pytest.mark.asyncio
    async def test_clicks_the_landscape_tab(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod
        from gflow_cli.api.video import Aspect

        sel = mod.VIDEO_ASPECT_TAB_SELECTORS[Aspect.LANDSCAPE][0]
        page = _cascade_page({sel})
        await VideoGenerationMixin._select_video_aspect(page, Aspect.LANDSCAPE)
        page.locator.assert_any_call(sel)

    @pytest.mark.asyncio
    async def test_missing_aspect_tab_is_non_fatal(self) -> None:
        from gflow_cli.api.video import Aspect

        page = _cascade_page(set())
        await VideoGenerationMixin._select_video_aspect(page, Aspect.PORTRAIT)  # must not raise


def _mock_async_page() -> MagicMock:
    """A MagicMock page whose AWAITED methods are AsyncMock (so `await page.x()`
    works) and whose `remove_listener` is a plain MagicMock."""
    page = MagicMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.bring_to_front = AsyncMock()
    page.remove_listener = MagicMock()
    return page


def _stub_video_helpers(monkeypatch: pytest.MonkeyPatch, *, generate_resp: dict) -> None:
    """Stub every VideoGenerationMixin helper `generate_video` drives, so the
    orchestration is testable without a browser. The listener stubs return
    `(captured, handler)` tuples to match the real signatures."""
    monkeypatch.setattr(VideoGenerationMixin, "_wait_video_editor_ready", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_switch_to_video_mode", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_set_output_count", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_select_video_model", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_select_video_duration", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_select_video_aspect", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_switch_video_sub_mode", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_attach_frame", AsyncMock())
    monkeypatch.setattr(VideoGenerationMixin, "_attach_references", AsyncMock())
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_attach_video_response_listener",
        staticmethod(lambda page: ([generate_resp], object())),
    )
    monkeypatch.setattr(
        VideoGenerationMixin,
        "_attach_status_response_listener",
        staticmethod(lambda page: ([], object())),
    )
    # #299: generate_video binds its driver via the mode-policy factory; stub
    # the bind to a real classic driver so no DOM probing hits the mock page.
    from gflow_cli.api.transports.drivers import factory
    from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver

    async def _bind_classic(page, *, ui_mode, transport):  # type: ignore[no-untyped-def]  # noqa: ARG001
        return ClassicFlowUiDriver(transport=transport)

    monkeypatch.setattr(factory, "get_ui_driver", _bind_classic)


class TestGenerateVideoGuards:
    @pytest.mark.asyncio
    async def test_requires_setup(self) -> None:
        transport = UiAutomationTransport()
        with pytest.raises(RuntimeError, match="setup"):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))

    def test_i2v_with_omni_flash_end_frame_resolves_to_omni_flash(self) -> None:
        """omni-flash + an END frame is a supported combination (#626).

        Flow shipped first+last for Omni 1.1 Flash and a route-aborted capture
        on 2026-09-02 proved the wire route
        (``batchAsyncGenerateVideoStartAndEndImage``, both images non-null), so
        the pre-submit rejection this replaces is gone. Reinstating it would
        make this raise instead of returning the requested model.
        """
        from gflow_cli.api.video import VideoModel

        req = GenerateVideoRequest(
            prompt="rise up",
            mode=Mode.I2V,
            model=VideoModel.OMNI_FLASH,
            start_image=Path("a.png"),
            end_image=Path("b.png"),
        )
        resolved = VideoGenerationMixin._resolve_i2v_model(req, True)
        assert resolved is VideoModel.OMNI_FLASH

    @pytest.mark.asyncio
    async def test_i2v_start_only_with_omni_flash_proceeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Start-only i2v with omni-flash is ACCEPTED: the 2026-08-03
        route-aborted re-capture (refs #125) proved Flow now routes it to the
        StartImage endpoint with the frame bound."""
        from gflow_cli.api.video import VideoModel

        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={
                "status": 200,
                "url": _I2V_START_URL,
                "body": {"media": [{"name": "v"}]},
            },
        )
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_poll_video_status",
            AsyncMock(
                return_value=VideoStatus(media_id="v", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
            ),
        )
        monkeypatch.setattr(transport, "_download_video", AsyncMock(return_value=Path("v.mp4")))
        req = GenerateVideoRequest(
            prompt="rise up",
            mode=Mode.I2V,
            model=VideoModel.OMNI_FLASH,
            start_image=Path("a.png"),
        )
        # Must NOT raise — and the frame attach must be reached.
        await transport.generate_video(request=req, download=False)
        VideoGenerationMixin._attach_frame.assert_awaited()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_t2v_with_omni_flash_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guard is i2v-only: t2v + omni-flash is a valid combination."""
        from gflow_cli.api.video import VideoModel

        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={"status": 200, "url": _T2V_URL, "body": {"media": [{"name": "v"}]}},
        )
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_poll_video_status",
            AsyncMock(
                return_value=VideoStatus(media_id="v", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
            ),
        )
        monkeypatch.setattr(transport, "_download_video", AsyncMock(return_value=Path("v.mp4")))
        req = GenerateVideoRequest(prompt="x", mode=Mode.T2V, model=VideoModel.OMNI_FLASH)
        # Must NOT raise — the guard is scoped to i2v.
        await transport.generate_video(request=req, download=False)

    @pytest.mark.asyncio
    async def test_i2v_routes_to_frames_and_attach(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        # i2v with a single start frame must route to the StartImage endpoint;
        # feeding the T2V url here would (correctly) trip the Layer-2 backstop.
        _stub_video_helpers(
            monkeypatch,
            generate_resp={
                "status": 200,
                "url": _I2V_START_URL,
                "body": {"media": [{"name": "v"}]},
            },
        )
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_poll_video_status",
            AsyncMock(
                return_value=VideoStatus(media_id="v", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
            ),
        )
        monkeypatch.setattr(transport, "_download_video", AsyncMock(return_value=Path("v.mp4")))
        req = GenerateVideoRequest(prompt="x", mode=Mode.I2V, start_image=Path("a.png"))
        await transport.generate_video(request=req, download=False)
        VideoGenerationMixin._switch_video_sub_mode.assert_awaited()  # type: ignore[attr-defined]
        VideoGenerationMixin._attach_frame.assert_awaited()  # type: ignore[attr-defined]
        # model=None i2v must default to the interpolation-capable model and
        # call _select_video_model with required=True (issue #125).
        from gflow_cli.api.video import I2V_DEFAULT_MODEL

        select_call = cast("Any", VideoGenerationMixin._select_video_model)
        select_call.assert_awaited()
        # `required` is gone: every miss is fatal now, on i2v and t2v alike.
        assert "required" not in select_call.await_args.kwargs
        assert select_call.await_args.args[1] is I2V_DEFAULT_MODEL

    @pytest.mark.asyncio
    async def test_r2v_routes_to_references_and_attach(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R2V must switch the editor to the 'references' sub-mode and attach the
        reference image(s) via _attach_references — NOT the I2V frame slots."""
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={"status": 200, "url": _T2V_URL, "body": {"media": [{"name": "v"}]}},
        )
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_poll_video_status",
            AsyncMock(
                return_value=VideoStatus(media_id="v", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
            ),
        )
        monkeypatch.setattr(transport, "_download_video", AsyncMock(return_value=Path("v.mp4")))
        req = GenerateVideoRequest(prompt="x", mode=Mode.R2V, reference_images=(Path("r.png"),))
        await transport.generate_video(request=req, download=False)
        # References sub-mode selected (not frames) + references attached, frames not.
        sub_args = [
            c.args
            for c in VideoGenerationMixin._switch_video_sub_mode.await_args_list  # type: ignore[attr-defined]
        ]
        assert any("references" in a for a in sub_args), sub_args
        VideoGenerationMixin._attach_references.assert_awaited()  # type: ignore[attr-defined]
        VideoGenerationMixin._attach_frame.assert_not_awaited()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_rejects_square_aspect(self) -> None:
        transport = UiAutomationTransport()
        transport._page = MagicMock()
        transport._setup_done = True
        req = GenerateVideoRequest(prompt="x", aspect=Aspect.SQUARE)
        with pytest.raises(ValueError, match="SQUARE"):
            await transport.generate_video(request=req)


class TestGenerateVideoOrchestration:
    @pytest.mark.asyncio
    async def test_t2v_happy_path_returns_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={
                "status": 200,
                "url": _T2V_URL,
                "body": {"media": [{"name": "vid-1"}]},
            },
        )

        async def _fake_poll(page, captured, media_name, **_k):  # type: ignore[no-untyped-def]
            assert media_name == "vid-1"
            return VideoStatus(media_id="vid-1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")

        monkeypatch.setattr(VideoGenerationMixin, "_poll_video_status", staticmethod(_fake_poll))
        fake_path = Path("/tmp/vid-1.mp4")
        monkeypatch.setattr(transport, "_download_video", AsyncMock(return_value=fake_path))
        result = await transport.generate_video(request=GenerateVideoRequest(prompt="a forest"))
        assert result.status.succeeded is True
        assert result.local_path == fake_path
        # both response listeners were detached in the finally block
        assert transport._page.remove_listener.call_count == 2

    @pytest.mark.asyncio
    async def test_t2v_401_raises_auth_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(monkeypatch, generate_resp={"status": 401, "url": _T2V_URL, "body": {}})
        from gflow_cli.errors import AuthExpiredError

        with pytest.raises(AuthExpiredError):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))
        assert transport._page.remove_listener.call_count == 2  # detached on the error path too

    @pytest.mark.asyncio
    async def test_t2v_403_raises_waf_rejection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(monkeypatch, generate_resp={"status": 403, "url": _T2V_URL, "body": {}})
        from gflow_cli.errors import WafRejectionError

        with pytest.raises(WafRejectionError):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))

    @pytest.mark.asyncio
    async def test_t2v_500_raises_wire_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(monkeypatch, generate_resp={"status": 500, "url": _T2V_URL, "body": {}})
        from gflow_cli.errors import WireFormatError

        with pytest.raises(WireFormatError):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))

    @pytest.mark.asyncio
    async def test_t2v_200_empty_media_raises_wire_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={"status": 200, "url": _T2V_URL, "body": {"media": []}},
        )
        from gflow_cli.errors import WireFormatError

        with pytest.raises(WireFormatError):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))


class TestDownloadVideo:
    @pytest.mark.asyncio
    async def test_download_video_saves_mp4(self, tmp_path: Path) -> None:
        """_download_video writes response bytes to <out_dir>/<media_id>.mp4."""
        transport = UiAutomationTransport()

        fake_page = MagicMock()
        fake_resp = AsyncMock()
        fake_resp.status = 200
        fake_resp.body = AsyncMock(return_value=b"fake-mp4-content")
        fake_page.request.get = AsyncMock(return_value=fake_resp)

        out_path = await transport._download_video("test-uuid-123", tmp_path, fake_page)

        assert out_path == tmp_path / "test-uuid-123.mp4"
        assert out_path.read_bytes() == b"fake-mp4-content"
        fake_page.request.get.assert_awaited_once()
        call_url = fake_page.request.get.call_args[0][0]
        assert "test-uuid-123" in call_url
        assert "getMediaUrlRedirect" in call_url

    @pytest.mark.asyncio
    async def test_download_video_raises_on_http_error(self, tmp_path: Path) -> None:
        """_download_video raises WireFormatError on non-2xx response."""
        from gflow_cli.errors import WireFormatError

        transport = UiAutomationTransport()

        fake_page = MagicMock()
        fake_resp = AsyncMock()
        fake_resp.status = 403
        fake_page.request.get = AsyncMock(return_value=fake_resp)

        with pytest.raises(WireFormatError):
            await transport._download_video("test-uuid-456", tmp_path, fake_page)


class TestGenerateVideoReturnType:
    @pytest.mark.asyncio
    async def test_generate_video_returns_video_result_type(self) -> None:
        """generate_video must declare VideoResult as return type."""
        import typing

        from gflow_cli.api.video import VideoResult

        transport = UiAutomationTransport()
        try:
            hints = typing.get_type_hints(transport.generate_video)
        except Exception:
            hints = {}

        ret = hints.get("return")
        assert ret is VideoResult or str(ret) == "VideoResult", (
            f"generate_video must return VideoResult, got {ret!r}"
        )


# ---------------------------------------------------------------------------
# Unit — _attach_frame: structural-first slot selection (issue #24 Phase 2)
# ---------------------------------------------------------------------------


def _make_frame_slot_page(
    *,
    structural_count: int,
    text_label_visible: bool = False,
    upload_dialog_raises: bool = False,
) -> MagicMock:
    """Build a fake page for _attach_frame locale-selection tests.

    structural_count  — how many results FRAME_SLOTS_STRUCT returns
    text_label_visible — whether FRAME_SLOT_BY_LABEL.first is visible
    """
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.on = MagicMock()
    page.remove_listener = MagicMock()

    struct_slots: list[MagicMock] = []
    for _ in range(structural_count):
        s = MagicMock()
        s.click = AsyncMock()
        struct_slots.append(s)

    struct_locator = MagicMock()
    first_struct = MagicMock()
    first_struct.wait_for = AsyncMock()
    struct_locator.first = first_struct
    struct_locator.count = AsyncMock(return_value=structural_count)
    struct_locator.nth = MagicMock(side_effect=lambda i: struct_slots[i])

    text_locator_inner = MagicMock()
    text_locator_inner.click = AsyncMock()
    # Production code uses wait_for(state="visible") not is_visible()
    if text_label_visible:
        text_locator_inner.wait_for = AsyncMock()
    else:
        text_locator_inner.wait_for = AsyncMock(side_effect=Exception("not visible"))
    text_locator_wrapper = MagicMock()
    text_locator_wrapper.first = text_locator_inner

    def _locator(sel: str) -> MagicMock:
        if FRAME_SLOTS_STRUCT in sel or sel == FRAME_SLOTS_STRUCT:
            return struct_locator
        # FRAME_SLOT_BY_LABEL is a format string; any has-text variant
        if "has-text" in sel:
            return text_locator_wrapper
        return MagicMock()

    page.locator = MagicMock(side_effect=_locator)
    return page


class TestAttachFrameSlotSelection:
    """_attach_frame selects frame slots structural-first (issue #24 Phase 2).

    Validates that locale-free structural selection (FRAME_SLOTS_STRUCT) is
    used when the slots are present, and that FRAME_SLOT_BY_LABEL text-match
    is only consulted as a fallback.
    """

    @pytest.mark.asyncio
    async def test_structural_slot_used_when_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When structural slots are found, _attach_frame clicks the indexed
        one without consulting the text-label fallback."""
        image = tmp_path / "start.png"
        image.write_bytes(b"\x89PNG")

        page = _make_frame_slot_page(structural_count=2)
        monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", AsyncMock())

        await VideoGenerationMixin._attach_frame(
            page, slot_index=0, label="Start", image=image, out_dir=None
        )

        # structural slot nth(0) was clicked
        struct_locator = page.locator(FRAME_SLOTS_STRUCT)
        struct_locator.nth(0).click.assert_awaited_once()
        # text locator was never probed via wait_for
        text_wrapper = page.locator(FRAME_SLOT_BY_LABEL.format(label="Start"))
        text_wrapper.first.wait_for.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_text_label_fallback_used_when_structural_count_insufficient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When structural count < slot_index + 1, _attach_frame falls back to
        the text-label selector (requires English Chrome profile)."""
        image = tmp_path / "start.png"
        image.write_bytes(b"\x89PNG")

        # Only 0 structural slots — fallback must be used
        page = _make_frame_slot_page(structural_count=0, text_label_visible=True)
        monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", AsyncMock())

        await VideoGenerationMixin._attach_frame(
            page, slot_index=0, label="Start", image=image, out_dir=None
        )

        # text locator was probed via wait_for and then clicked
        text_wrapper = page.locator(FRAME_SLOT_BY_LABEL.format(label="Start"))
        text_wrapper.first.wait_for.assert_awaited_once()
        text_wrapper.first.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_structural_and_text_both_miss(self, tmp_path: Path) -> None:
        """RuntimeError is raised when neither structural nor text-label finds
        the slot — gives a clear error instead of a silent hang."""
        image = tmp_path / "start.png"
        image.write_bytes(b"\x89PNG")

        page = _make_frame_slot_page(structural_count=0, text_label_visible=False)

        with pytest.raises(RuntimeError, match="frame slot index 0"):
            await VideoGenerationMixin._attach_frame(
                page, slot_index=0, label="Start", image=image, out_dir=None
            )

    @pytest.mark.asyncio
    async def test_raises_when_image_missing(self, tmp_path: Path) -> None:
        """FileNotFoundError is raised before any DOM interaction when the
        source image does not exist."""
        page = _make_frame_slot_page(structural_count=2)

        with pytest.raises(FileNotFoundError, match="frame image not found"):
            await VideoGenerationMixin._attach_frame(
                page,
                slot_index=0,
                label="Start",
                image=tmp_path / "nonexistent.png",
                out_dir=None,
            )


# ---------------------------------------------------------------------------
# Issue #125 Layer 1 (model-select fatal for i2v) + Layer 2 (post-submit
# T2V-routing backstop) + model-select reliability retry.
# ---------------------------------------------------------------------------


def _select_model_page(*, option_visible_on_attempt: int | None) -> MagicMock:
    """A page for exercising _select_video_model. The model-picker trigger is
    always visible; the model OPTION becomes visible only on
    `option_visible_on_attempt` (1-based trigger click). None => never visible.
    """
    from gflow_cli.api.transports import ui_automation_video as mod

    trigger_sel = mod.MODEL_PICKER_TRIGGER
    option_sel = mod.VIDEO_MODEL_OPTION_SELECTORS[mod.VideoModel.VEO_3_1_LITE]
    state = {"clicks": 0}
    page = MagicMock()

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        loc.first = loc

        async def _wait_for(*_a: object, **_k: object) -> None:
            if sel == trigger_sel:
                return
            if sel == option_sel and option_visible_on_attempt is not None:
                if state["clicks"] >= option_visible_on_attempt:
                    return
            raise Exception("not visible")

        async def _click(*_a: object, **_k: object) -> None:
            if sel == trigger_sel:
                state["clicks"] += 1

        async def _count() -> int:
            if sel == trigger_sel:
                return 1
            if sel == option_sel and option_visible_on_attempt is not None:
                return 1 if state["clicks"] >= option_visible_on_attempt else 0
            return 0

        async def _is_visible(*_a: object, **_k: object) -> bool:
            return await _count() > 0

        loc.wait_for = _wait_for
        loc.click = _click
        loc.count = _count
        loc.nth = MagicMock(return_value=loc)
        loc.is_visible = _is_visible
        return loc

    page.locator = MagicMock(side_effect=_locator)
    page.wait_for_timeout = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.screenshot = AsyncMock()
    page.evaluate = AsyncMock(return_value=[])
    return page


class TestSelectVideoModelRequired:
    """Named for the old ``required=`` flag, which no longer exists: as of
    2026-08-26 EVERY model miss is fatal, on i2v and t2v alike."""

    @pytest.mark.asyncio
    async def test_required_raises_when_option_never_found(self) -> None:
        """Issue #125 Layer 1: a model-option miss is FATAL — raise rather than
        let Flow fall back to omni-flash -> T2V."""
        from gflow_cli.errors import VideoModelSelectionError

        page = _select_model_page(option_visible_on_attempt=None)
        with pytest.raises(VideoModelSelectionError, match="#125"):
            await VideoGenerationMixin._select_video_model(
                page, VideoModel.VEO_3_1_LITE, out_dir=None
            )

    @pytest.mark.asyncio
    async def test_t2v_miss_is_fatal_too(self) -> None:
        """Was `test_not_required_warns_and_returns_on_miss`.

        The old contract let t2v/r2v proceed on Flow's current model. Video is
        the credit-bearing arm (veo-quality 100 against veo-lite's 10), and this
        function is only reached when a model was explicitly requested, so a miss
        has no defensible fallback. The `required` flag is gone entirely.
        """
        from gflow_cli.errors import VideoModelSelectionError

        page = _select_model_page(option_visible_on_attempt=None)
        with pytest.raises(VideoModelSelectionError):
            await VideoGenerationMixin._select_video_model(
                page, VideoModel.VEO_3_1_LITE, out_dir=None
            )

    @pytest.mark.asyncio
    async def test_retries_trigger_click_and_succeeds_second_attempt(self) -> None:
        """Reliability: the first trigger click may not open the menu; the
        second attempt finds the option and selects it (no raise)."""
        page = _select_model_page(option_visible_on_attempt=2)
        await VideoGenerationMixin._select_video_model(page, VideoModel.VEO_3_1_LITE, out_dir=None)


class TestI2vT2vRoutingBackstop:
    @pytest.mark.asyncio
    async def test_i2v_routed_to_t2v_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Issue #125 Layer 2: if an i2v request's captured generate URL is the
        T2V endpoint, raise WireFormatError instead of returning a fake-success
        VideoResult (the frames were silently dropped)."""
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={"status": 200, "url": _T2V_URL, "body": {"media": [{"name": "v"}]}},
        )
        # veo-lite is a VALID i2v model — the DTO guard passes; only the
        # post-submit URL backstop should fire.
        req = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            model=VideoModel.VEO_3_1_LITE,
            start_image=Path("a.png"),
            end_image=Path("b.png"),
        )
        with pytest.raises(WireFormatError, match="#125"):
            await transport.generate_video(request=req, download=False)

    def test_end_frame_routed_to_start_only_raises(self) -> None:
        """A request carrying an END frame that Flow routed to the START-only
        endpoint must raise, not be reported as a success (#626).

        The T2V check above only catches Flow dropping *both* frames. Now that
        omni-flash may carry an end frame, the narrower regression — Flow keeps
        the start frame and silently drops the end one, so ``StartAndEndImage``
        degrades to ``StartImage`` — spends the credit and yields a
        non-interpolated clip that the T2V check waves straight through. Same
        mis-billing class the removed pre-submit guard covered.
        """
        req = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            model=VideoModel.OMNI_FLASH,
            start_image=Path("a.png"),
            end_image=Path("b.png"),
        )
        with pytest.raises(WireFormatError, match="end frame"):
            VideoGenerationMixin._assert_i2v_route(_I2V_START_URL, req, VideoModel.OMNI_FLASH)

    @pytest.mark.asyncio
    async def test_end_frame_dropped_raises_through_generate_video(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The end-frame check must fire through its real call site, not just
        when called directly.

        The direct-call tests above cover the helper's logic; this covers the
        wiring in ``_submit_and_poll``. Without it, a positional-argument slip
        at the call site (swapping ``request`` and ``effective_model``) stays
        invisible: the T2V branch returns before touching either argument, so
        every other ``generate_video`` test would still pass while the #626
        branch blew up with an AttributeError on the one path that matters.
        """
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={
                "status": 200,
                "url": _I2V_START_URL,
                "body": {"media": [{"name": "v"}]},
            },
        )
        req = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            model=VideoModel.OMNI_FLASH,
            start_image=Path("a.png"),
            end_image=Path("b.png"),
        )
        with pytest.raises(WireFormatError, match="#626"):
            await transport.generate_video(request=req, download=False)

    def test_end_frame_routed_to_start_and_end_is_accepted(self) -> None:
        """Control for the test above: the correct route passes the backstop.

        Without this, a backstop that rejected *every* end-frame request would
        still look green.
        """
        req = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            model=VideoModel.OMNI_FLASH,
            start_image=Path("a.png"),
            end_image=Path("b.png"),
        )
        VideoGenerationMixin._assert_i2v_route(_I2V_START_END_URL, req, VideoModel.OMNI_FLASH)

    def test_start_only_i2v_is_not_held_to_the_end_frame_route(self) -> None:
        """A start-only request must still be accepted on ``StartImage``.

        Guards the new check against over-reach: it may fire only when the
        request actually carries an end frame.
        """
        req = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            model=VideoModel.OMNI_FLASH,
            start_image=Path("a.png"),
        )
        VideoGenerationMixin._assert_i2v_route(_I2V_START_URL, req, VideoModel.OMNI_FLASH)

    def test_uuid_end_frame_ref_also_requires_the_end_route(self) -> None:
        """A UUID-backed end frame gets the same protection as a local path.

        `end_image_ref_id` and `end_image_ref_name` are the other two ways an
        end frame reaches the request; keying the check off `end_image` alone
        would leave both unguarded.
        """
        req = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            model=VideoModel.OMNI_FLASH,
            start_image_ref_id=_FRAME_REF_UUID,
            end_image_ref_id="6a3d0c11-2b8e-4f6a-9c77-1d5f0e2a4b93",
        )
        with pytest.raises(WireFormatError, match="end frame"):
            VideoGenerationMixin._assert_i2v_route(_I2V_START_URL, req, VideoModel.OMNI_FLASH)

    @pytest.mark.asyncio
    async def test_uuid_i2v_routed_to_t2v_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """UUID-backed frames receive the same post-submit credit-safety guard."""
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={"status": 200, "url": _T2V_URL, "body": {"media": [{"name": "v"}]}},
        )
        monkeypatch.setattr(VideoGenerationMixin, "_attach_i2v_frames", AsyncMock())
        req = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            start_image_ref_id=_FRAME_REF_UUID,
        )

        with pytest.raises(WireFormatError, match="#125"):
            await transport.generate_video(request=req, download=False)

    @pytest.mark.asyncio
    async def test_named_i2v_routed_to_t2v_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={"status": 200, "url": _T2V_URL, "body": {"media": [{"name": "v"}]}},
        )
        monkeypatch.setattr(VideoGenerationMixin, "_attach_i2v_frames", AsyncMock())
        req = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            start_image_ref_name="Brass key",
        )

        with pytest.raises(WireFormatError, match="#125"):
            await transport.generate_video(request=req, download=False)

    @pytest.mark.asyncio
    async def test_i2v_routed_to_start_end_image_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The backstop must NOT fire when the i2v request routes correctly."""
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={
                "status": 200,
                "url": _I2V_START_END_URL,
                "body": {"media": [{"name": "v"}]},
            },
        )
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_poll_video_status",
            AsyncMock(
                return_value=VideoStatus(media_id="v", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
            ),
        )
        monkeypatch.setattr(transport, "_download_video", AsyncMock(return_value=Path("v.mp4")))
        req = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            model=VideoModel.VEO_3_1_LITE,
            start_image=Path("a.png"),
            end_image=Path("b.png"),
        )
        result = await transport.generate_video(request=req, download=False)
        assert result.status.succeeded is True

    @pytest.mark.asyncio
    async def test_t2v_routed_to_t2v_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A genuine t2v request landing on the T2V route is correct — the
        backstop is i2v-only."""
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={"status": 200, "url": _T2V_URL, "body": {"media": [{"name": "v"}]}},
        )
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_poll_video_status",
            AsyncMock(
                return_value=VideoStatus(media_id="v", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
            ),
        )
        monkeypatch.setattr(transport, "_download_video", AsyncMock(return_value=Path("v.mp4")))
        req = GenerateVideoRequest(prompt="x", mode=Mode.T2V)
        result = await transport.generate_video(request=req, download=False)
        assert result.status.succeeded is True


class TestPickerIncludeSelectorLocaleInvariance:
    """Issue #170: the picker include selectors must be tiered cascades —
    locale-free anchor first where one exists, localized text as fallback.

    Recon (denon82 pt-BR 2026-06-11 + ru report on #170): the right-click
    context menu is `div[role='menu'][data-state='open']` and its include item
    carries the `add` ligature (unique within the menu: content_cut /
    content_copy / delete). The Vozes include button has NO ligature — it is
    the lone iconless button in the open picker dialog.
    """

    def test_context_include_is_a_two_tier_cascade(self) -> None:
        assert isinstance(PICKER_CONTEXT_INCLUDE, tuple)
        assert len(PICKER_CONTEXT_INCLUDE) == 2

    def test_context_include_tier1_is_menu_scoped_add_icon(self) -> None:
        """Tier 1 locked verbatim so a drift can't silently slip past."""
        assert PICKER_CONTEXT_INCLUDE[0] == (
            "[role='menu'][data-state='open'] "
            "[role='menuitem']:has(i.google-symbols:text-is('add'))"
        ), f"PICKER_CONTEXT_INCLUDE[0] drifted: {PICKER_CONTEXT_INCLUDE[0]!r}"

    def test_context_include_tier1_has_no_localized_text(self) -> None:
        for word in ("Incluir", "Добавить", "Add to prompt"):
            assert word not in PICKER_CONTEXT_INCLUDE[0]

    def test_context_include_text_tier_covers_pt_ru_en_menu_scoped(self) -> None:
        text_tier = PICKER_CONTEXT_INCLUDE[1]
        for caption in ("Incluir no comando", "Добавить в запрос", "Add to prompt"):
            assert caption in text_tier, f"missing caption {caption!r}"
        # Every comma-segment must be scoped to the open menu so a user-named
        # tile (e.g. a character called 'Add to prompt') can never match.
        for segment in text_tier.split(","):
            assert segment.strip().startswith("[role='menu']"), (
                f"unscoped text segment: {segment.strip()!r}"
            )

    def test_include_button_is_a_two_tier_cascade(self) -> None:
        assert isinstance(PICKER_INCLUDE_BUTTON, tuple)
        assert len(PICKER_INCLUDE_BUTTON) == 2

    def test_include_button_text_tier_covers_pt_ru_en(self) -> None:
        text_tier = PICKER_INCLUDE_BUTTON[0]
        for caption in ("Incluir no comando", "Добавить в запрос", "Add to prompt"):
            assert caption in text_tier, f"missing caption {caption!r}"

    def test_include_button_structural_tier_is_lone_iconless_dialog_button(self) -> None:
        structural = PICKER_INCLUDE_BUTTON[1]
        assert structural.startswith("[role='dialog'][data-state='open']")
        assert ":not(:has(i.google-symbols))" in structural


class TestAttachCharacterEntities:
    @staticmethod
    def _picker_page() -> MagicMock:
        """A page whose every locator is selected + already rendered (count=1)."""
        page = MagicMock()
        loc = MagicMock()
        loc.first = loc
        loc.last = loc
        loc.click = AsyncMock()
        loc.hover = AsyncMock()
        loc.fill = AsyncMock()
        loc.wait_for = AsyncMock()
        loc.scroll_into_view_if_needed = AsyncMock()
        loc.count = AsyncMock(return_value=1)
        loc.or_ = MagicMock(return_value=loc)
        page.locator.return_value = loc
        page.get_by_role.return_value = loc
        page.wait_for_timeout = AsyncMock()
        page.mouse = MagicMock()
        page.mouse.wheel = AsyncMock()
        page.keyboard = MagicMock()
        page.keyboard.press = AsyncMock()
        page.screenshot = AsyncMock()
        return page

    @pytest.mark.asyncio
    async def test_attach_right_clicks_personagens_entity_tile(self) -> None:
        """A character is staged as a referenceEntity via: Personagens tab ->
        RIGHT-CLICK the `data-tile-id=fe_id_<entityId>` tile -> the context-menu
        include action, matched by its locale-free `add` ligature (Tier 1). The
        tile is addressed by entity id (not name), and the click MUST be a
        right-click (a left-click navigates to the editor)."""
        page = self._picker_page()

        await VideoGenerationMixin._attach_character_entities(
            page, [("ent-123", "Stickman")], out_dir=None
        )

        selectors = " ".join(str(c.args[0]) for c in page.locator.call_args_list)
        assert "accessibility_new" in selectors  # Personagens tab
        assert "fe_id_ent-123" in selectors  # tile keyed by entity id
        assert "text-is('add')" in selectors  # icon-tier context-menu action
        assert "add-menu-input" not in selectors  # NOT the prompt box
        # The selection click is a right-click (button='right').
        right_clicks = [
            c
            for c in page.locator.return_value.click.call_args_list
            if c.kwargs.get("button") == "right"
        ]
        assert right_clicks, "expected a right-click on the entity tile"

    @pytest.mark.asyncio
    async def test_attach_logs_which_selector_tier_matched(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """Drift telemetry: a successful attach reports the matched tier so the
        icon tier dying (text tier silently carrying the load) is observable."""
        page = self._picker_page()

        await VideoGenerationMixin._attach_character_entities(
            page, [("ent-123", "Stickman")], out_dir=None
        )

        tier_events = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.include_selector_tier"
        ]
        assert tier_events, "expected an include_selector_tier event"
        assert tier_events[0]["tier"] == "icon"
        assert tier_events[0]["surface"] == "context_menu"

    @pytest.mark.asyncio
    async def test_attach_raises_when_context_menu_absent(self) -> None:
        """If the right-click context menu never shows the include action (any
        tier), the attach fails loudly (with a screenshot) instead of silently
        dropping the entity — and the error is TYPED with a locale-neutral
        message (issue #170: a RuntimeError embedding the pt-BR caption reached
        the user only as a privacy-hashed 'Unexpected error.', burying the
        remediation hint)."""
        page = self._picker_page()
        # wait_for succeeds for add-media / Personagens tab / tile, then raises
        # for every include tier probe (the menu item never appeared).
        page.locator.return_value.wait_for = AsyncMock(
            side_effect=[None, None, None] + [TimeoutError("boom")] * 4
        )

        with pytest.raises(TransportTimeoutError, match="include action") as excinfo:
            await VideoGenerationMixin._attach_character_entities(
                page, [("ent-123", "Stickman")], out_dir=None
            )
        message = str(excinfo.value)
        assert "Incluir" not in message, "error message must be locale-neutral"
        assert "ent-123" in message
        assert excinfo.value.remediation_hint, "expected a remediation hint"
        assert "Incluir" not in excinfo.value.remediation_hint

    @pytest.mark.asyncio
    async def test_attach_failure_closes_picker_before_raising(self) -> None:
        """The failure path must not return a Page to the pool with the picker
        dialog / context menu still open (state contamination for the next
        checkout) — Escape is pressed before the error propagates."""
        page = self._picker_page()
        page.locator.return_value.wait_for = AsyncMock(
            side_effect=[None, None, None] + [TimeoutError("boom")] * 4
        )

        with pytest.raises(TransportTimeoutError, match="include action"):
            await VideoGenerationMixin._attach_character_entities(
                page, [("ent-123", "Stickman")], out_dir=None
            )
        escapes = [
            c for c in page.keyboard.press.call_args_list if c.args and c.args[0] == "Escape"
        ]
        assert escapes, "expected Escape cleanup before raising"


class TestAttachReferenceAudio:
    @pytest.mark.asyncio
    async def test_attach_audio_logs_selector_tier(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """The Vozes include button has no ligature (recon 2026-06-11): the
        text tier is primary and its match must be reported for telemetry."""
        page = TestAttachCharacterEntities._picker_page()

        await VideoGenerationMixin._attach_reference_audio(page, "Alnilam", out_dir=None)

        tier_events = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.include_selector_tier"
        ]
        assert tier_events, "expected an include_selector_tier event"
        assert tier_events[0]["tier"] == "text"
        assert tier_events[0]["surface"] == "vozes_button"

    @pytest.mark.asyncio
    async def test_attach_audio_raises_locale_neutral_when_button_absent(self) -> None:
        """When no include-button tier matches, the failure is loud, typed,
        locale-neutral, and leaves no open dialog behind."""
        page = TestAttachCharacterEntities._picker_page()
        # add-media wait succeeds; both include-button tier probes time out.
        page.locator.return_value.wait_for = AsyncMock(
            side_effect=[None] + [TimeoutError("boom")] * 4
        )

        with pytest.raises(TransportTimeoutError, match="include action") as excinfo:
            await VideoGenerationMixin._attach_reference_audio(page, "Alnilam", out_dir=None)
        message = str(excinfo.value)
        assert "Incluir" not in message, "error message must be locale-neutral"
        escapes = [
            c for c in page.keyboard.press.call_args_list if c.args and c.args[0] == "Escape"
        ]
        assert escapes, "expected Escape cleanup before raising"


class TestAssertEntitiesAttached:
    @staticmethod
    def _live_response(entity_id: str) -> dict[str, object]:
        """The real SUBMIT response shape — the entity is echoed under
        media[].mediaMetadata.requestData.videoGenerationRequestData
        .videoGenerationEntityInputs (NOT requests[].referenceEntities)."""
        return {
            "url": "video:batchAsyncGenerateVideoReferenceImages",
            "status": 200,
            "body": {
                "media": [
                    {
                        "name": "vid-1",
                        "mediaMetadata": {
                            "requestData": {
                                "videoGenerationRequestData": {
                                    "videoGenerationEntityInputs": [{"entityId": entity_id}],
                                }
                            }
                        },
                    }
                ]
            },
        }

    def test_backstop_raises_when_entity_missing_from_payload(self) -> None:
        from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
        from gflow_cli.errors import WireFormatError

        captured = {
            "url": "video:batchAsyncGenerateVideoReferenceImages",
            "status": 200,
            # a real response with NO entity inputs (text/image-only generation).
            "body": {"media": [{"mediaMetadata": {"requestData": {}}}]},
        }
        with pytest.raises(WireFormatError, match="entity attach failed"):
            VideoGenerationMixin._assert_entities_attached(captured, expected=["ent-1"])

    def test_backstop_passes_on_live_response_shape(self) -> None:
        from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

        # should NOT raise — entity echoed at the real response path.
        VideoGenerationMixin._assert_entities_attached(
            self._live_response("ent-1"), expected=["ent-1"]
        )

    def test_backstop_accepts_request_shape_fallback(self) -> None:
        from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

        # request-body shape (referenceEntities) is also accepted.
        captured = {"body": {"requests": [{"referenceEntities": [{"entityId": "ent-1"}]}]}}
        VideoGenerationMixin._assert_entities_attached(captured, expected=["ent-1"])

    def test_backstop_error_carries_issue_174_hint_and_discovery(self) -> None:
        """Issue #174: an attach miss on the new library UI must point the
        user at the tracking issue (typed-error remediation hint) and tag
        the surface in the discovery payload."""
        from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
        from gflow_cli.errors import WireFormatError

        captured = {"body": {"media": [{"mediaMetadata": {"requestData": {}}}]}}
        with pytest.raises(WireFormatError) as exc_info:
            VideoGenerationMixin._assert_entities_attached(captured, expected=["ent-1"])
        err = exc_info.value
        assert "github.com/ffroliva/gflow-cli/issues/174" in err.remediation_hint
        assert err.to_problem_details().get("remediation_hint") == err.remediation_hint
        assert err.discovery == {"entity_attach_context": "video"}


class TestRemoteRefTileLocator:
    """PR #237: option tiles are matched by display_name / prompt text, which
    commonly contains an apostrophe. The old `:has-text('{name}')` CSS selector
    broke on those; `_remote_option_tile` must match by role name instead."""

    def test_apostrophe_name_does_not_go_into_a_quoted_css_selector(self) -> None:
        page = MagicMock()
        VideoGenerationMixin._remote_option_tile(page, "Wren's cabin")
        # #529 live recon: the picker exposes no accessible tree, so the tile
        # is matched by text via has_text — the name is escaped into an
        # anchored regex, never interpolated into a `:has-text('...')` CSS
        # string.
        page.locator.assert_called_once()
        args, kwargs = page.locator.call_args
        assert args[0] == "[role='option']"
        assert kwargs["has_text"].match("Wren's cabin")
        page.get_by_role.assert_not_called()

    def test_anchored_so_a_substring_name_cannot_attach_the_wrong_tile(self) -> None:
        # PR #245 review #4: a substring match makes 'cabin' also select
        # 'cabin at night' → .first attaches the wrong image silently.
        # #529 live e2e: the option's text carries the picker's localized
        # media-type badge ('…\nImagem' on a pt profile) appended to the
        # display name — that suffix, and only that suffix, must be tolerated.
        page = MagicMock()
        VideoGenerationMixin._remote_option_tile(page, "cabin")
        pattern = page.locator.call_args.kwargs["has_text"]
        assert pattern.match("cabin")
        assert pattern.match("cabinImagem")
        assert pattern.match("cabin\nImagem")
        assert not pattern.match("cabin at night")
        assert not pattern.match("cabin at nightImagem")
        assert not pattern.match("cabinsImagem")


class TestRemoteReferencesDialogGuard:
    """PR #237 review #4: _attach_remote_references logged success even when the
    include action never fired (locale mismatch). It must verify the picker
    dialog closed and raise TransportTimeoutError otherwise."""

    @staticmethod
    def _locator_mock() -> MagicMock:
        loc = MagicMock()
        loc.wait_for = AsyncMock()
        loc.click = AsyncMock()
        loc.press_sequentially = AsyncMock()
        loc.first = loc
        loc.last = loc
        return loc

    @pytest.mark.asyncio
    async def test_raises_when_picker_dialog_stays_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = _mock_async_page()
        dialog = self._locator_mock()
        dialog.wait_for = AsyncMock(side_effect=Exception("dialog still open"))

        def _locator(selector: str) -> MagicMock:
            return dialog if selector == "[role='dialog']" else self._locator_mock()

        page.locator = MagicMock(side_effect=_locator)
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_remote_option_tile",
            staticmethod(lambda p, n: self._locator_mock()),
        )
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_resolve_include_action",
            AsyncMock(return_value=self._locator_mock()),
        )

        with pytest.raises(TransportTimeoutError, match="did not close"):
            await VideoGenerationMixin._attach_remote_references(
                page, ["Wren's cabin"], out_dir=None
            )


class TestSelectExistingAssetPickerScroll:
    """Picker fakes shared by name-search and exact-UUID selection tests."""

    @staticmethod
    def _tile_mock(
        *,
        wait_for_side_effect: object,
        count_side_effect: object = 0,
    ) -> MagicMock:
        tile = MagicMock()
        tile.first = tile
        tile.click = AsyncMock()
        tile.wait_for = AsyncMock(side_effect=wait_for_side_effect)
        tile.evaluate = AsyncMock(return_value=True)
        if isinstance(count_side_effect, list):
            tile.count = AsyncMock(side_effect=count_side_effect)
        else:
            tile.count = AsyncMock(return_value=count_side_effect)
        return tile

    @staticmethod
    def _page_with_tile(tile: MagicMock) -> MagicMock:
        page = _mock_async_page()
        dialog = MagicMock()
        dialog.last = dialog
        dialog.hover = AsyncMock()
        dialog.wait_for = AsyncMock()  # closes immediately -> one-step image attach
        search = MagicMock()
        search.first = search
        search.press_sequentially = AsyncMock()
        search.fill = AsyncMock()
        search.wait_for = AsyncMock()
        search.count = AsyncMock(return_value=1)

        def _locator(selector: str) -> MagicMock:
            if selector == DIALOG_ANY:
                return dialog
            if selector == PICKER_SEARCH_INPUT:
                return search
            return tile

        page.locator = MagicMock(side_effect=_locator)
        page.mouse = MagicMock()
        page.mouse.wheel = AsyncMock()
        return page

    @pytest.mark.asyncio
    async def test_named_tile_is_searched_and_selected_by_exact_uuid(self) -> None:
        tile = self._tile_mock(
            wait_for_side_effect=None,
            count_side_effect=1,
        )
        page = self._page_with_tile(tile)
        search = page.locator(PICKER_SEARCH_INPUT)

        result = await VideoGenerationMixin._select_existing_asset(
            page, "uuid-1", "Brass key on marble surface", out_dir=None
        )

        assert result is True
        search.press_sequentially.assert_awaited_once()
        assert search.press_sequentially.await_args.args[0] == "Brass key on marble surface"
        tile.click.assert_awaited_once()
        page.mouse.wheel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_waits_for_delayed_search_input_before_name_lookup(self) -> None:
        tile = self._tile_mock(wait_for_side_effect=None, count_side_effect=1)
        page = self._page_with_tile(tile)
        search = page.locator(PICKER_SEARCH_INPUT)
        search.count = AsyncMock(return_value=0)

        result = await VideoGenerationMixin._select_existing_asset(
            page, "uuid-1", "Brass key", out_dir=None
        )

        assert result is True
        search.wait_for.assert_awaited_once_with(state="visible", timeout=4000)
        assert search.press_sequentially.await_args.args[0] == "Brass key"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("probe", [False, RuntimeError("stale tile")])
    async def test_filtered_tile_must_be_fully_visible_before_click(self, probe: object) -> None:
        tile = self._tile_mock(wait_for_side_effect=None, count_side_effect=1)
        tile.evaluate = (
            AsyncMock(side_effect=probe)
            if isinstance(probe, Exception)
            else AsyncMock(return_value=probe)
        )
        page = self._page_with_tile(tile)

        result = await VideoGenerationMixin._select_existing_asset(
            page, "uuid-1", "Brass key", out_dir=None
        )

        assert result is False
        tile.click.assert_not_awaited()
        page.mouse.wheel.assert_not_awaited()


class TestSelectExistingAssetLargeGrid:
    """Name-addressed UUID selection never scans a crowded unfiltered grid."""

    _FULL_UUID = "d6f1927a-3eae-4626-bc90-9a6ea7637bab"

    @pytest.mark.asyncio
    async def test_display_name_miss_does_not_scroll_or_search_uuid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The browser picker is name-addressed; a name miss is terminal.

        Scrolling the unfiltered catalog and typing UUID fragments are not
        alternate resolution strategies.  The exact UUID remains the tile
        assertion after the name search surfaces candidates.
        """
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=TimeoutError("never visible"),
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        search = page.locator(PICKER_SEARCH_INPUT)
        scroll = AsyncMock(return_value=False)
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_scroll_picker_grid_until_rendered",
            scroll,
        )

        result = await VideoGenerationMixin._select_existing_asset(
            page,
            self._FULL_UUID,
            "Brass key on wooden bench",
            out_dir=None,
        )

        assert result is False
        assert [call.args[0] for call in search.press_sequentially.await_args_list] == [
            "Brass key on wooden bench"
        ]
        assert search.fill.await_count == 2
        assert all(call.args == ("",) for call in search.fill.call_args_list)
        scroll.assert_not_awaited()
        page.mouse.wheel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_display_name_does_not_scroll_or_search(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A legacy bare UUID only checks the already-rendered viewport."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=[
                TimeoutError("not visible in initial viewport"),
                None,  # visible after scrolling
            ],
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        search = page.locator(PICKER_SEARCH_INPUT)
        scroll = AsyncMock(return_value=True)
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_scroll_picker_grid_until_rendered",
            scroll,
        )

        result = await VideoGenerationMixin._select_existing_asset(
            page, self._FULL_UUID, "", out_dir=None
        )

        assert result is False
        tile.click.assert_not_awaited()
        scroll.assert_not_awaited()
        search.press_sequentially.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tile_match_stays_uuid_in_src(self) -> None:
        """A non-unique name can surface extra tiles but cannot select one."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=None,
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        display_name = "common caption words"

        result = await VideoGenerationMixin._select_existing_asset(
            page,
            self._FULL_UUID,
            display_name,
            out_dir=None,
        )

        assert result is not None
        exact_selector = f"[role='option']:has(img[src*='{self._FULL_UUID}'])"
        assert page.locator.call_args_list[0].args == (exact_selector,)
        tile.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_grid_scroll_uses_js_scroller_and_logs_evidence(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """#287 round 6 audit: react-virtuoso scrolls its own container, not
        the dialog — a wheel over the wrong node is a silent no-op. The grid
        scroll now drives the actual scrollable element via JS and the probe
        event reports WHICH node moved and its scrollTop before/after, so a
        no-op scroll (scrollTop never moves) is visible in telemetry."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=[
                TimeoutError("not visible in initial viewport"),
                TimeoutError("not surfaced by the UUID search"),
                TimeoutError("not surfaced by the UUID-stem search"),
                None,
            ],
            count_side_effect=[0, 0, 1],
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        counter = itertools.count()
        scroll_top = {"value": 0}

        def _eval(js: str, *args: object) -> object:
            if "scrollTop" in js:
                before = scroll_top["value"]
                scroll_top["value"] = before + 500
                return {
                    "tag": "div",
                    "cls": "virtuoso-scroller",
                    "before": before,
                    "after": scroll_top["value"],
                }
            return [f"tile-{next(counter)}"]

        page.evaluate = AsyncMock(side_effect=_eval)

        result = await VideoGenerationMixin._scroll_picker_grid_until_rendered(page, tile)

        assert result is True
        # JS scroll must replace the blind wheel when the scroller is found.
        page.mouse.wheel.assert_not_awaited()
        probes = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_scroll_probe"
        ]
        assert probes, "expected picker_scroll_probe events"
        assert probes[0]["scrolled_tag"] == "div"
        assert probes[0]["scrolled_class"] == "virtuoso-scroller"
        assert probes[0]["scroll_top_before"] == 0
        assert probes[0]["scroll_top_after"] == 500
        assert probes[1]["scroll_top_before"] == 500


_PROJECT_ID_287 = "f6caf027-0000-4000-8000-000000000287"
_PROJECT_URL_287 = f"https://labs.google/fx/tools/flow/project/{_PROJECT_ID_287}"


class TestPickerProjectSync:
    """#287 CONFIRMED (live round 2): the media picker's library component has
    its OWN project selection — the dump showed the library on an old test
    project (`gflow-cli t2i`, 16 tiles, `target_project_in_dialog: false`)
    while `--project` had only navigated the EDITOR. The trigger is a Radix
    `ProjectDropdownSubTrigger` (aria-haspopup='menu', submenu semantics,
    portal-rendered options), the menu options render project NAMES (not
    ids), and the UI locale is not English (pt-BR observed) — so the switch
    resolves the target project's NAME from the live page and matches menu
    options by id first, then by name, never by locale-dependent labels."""

    @staticmethod
    def _trigger_mock(*, references_target: bool) -> MagicMock:
        trigger = MagicMock()
        trigger.first = trigger
        trigger.count = AsyncMock(return_value=1)
        trigger.click = AsyncMock()
        trigger.hover = AsyncMock()
        trigger.focus = AsyncMock()
        # Active-project probe: does the trigger's markup/text reference the
        # target project (by id, or by resolved name)?
        trigger.evaluate = AsyncMock(return_value=references_target)
        return trigger

    @staticmethod
    def _menu_mock(*, wait_for: object = None) -> MagicMock:
        menu = MagicMock()
        menu.last = menu
        if isinstance(wait_for, list):
            menu.wait_for = AsyncMock(side_effect=wait_for)
        else:
            menu.wait_for = AsyncMock(side_effect=wait_for) if wait_for else AsyncMock()
        return menu

    @staticmethod
    def _selector_page(trigger: MagicMock | None, *, menu: MagicMock | None = None) -> MagicMock:
        page = _mock_async_page()
        absent = MagicMock()
        absent.first = absent
        absent.last = absent
        absent.count = AsyncMock(return_value=0)
        absent.wait_for = AsyncMock(side_effect=TimeoutError("absent"))

        def _locator(selector: str) -> MagicMock:
            if trigger is not None and selector == PICKER_PROJECT_SELECTOR_TRIGGERS[0]:
                return trigger
            if menu is not None and selector == PICKER_PROJECT_MENU_OPEN:
                return menu
            return absent

        page.locator = MagicMock(side_effect=_locator)

        # page.evaluate router keyed on JS markers (#287 round 4): the portal
        # dump JS reads inner_html, the option-match JS returns `clicked`,
        # anything else is the menu-population poll (element count).
        def _default_eval(js: str, *args: object) -> object:
            if "inner_html" in js:
                return None
            if "clicked" in js:
                return {"clicked": True, "matched_by": "id", "candidates": 3}
            return 5

        page.evaluate = AsyncMock(side_effect=_default_eval)
        return page

    def test_trigger_cascade_prefers_project_dropdown_and_excludes_sort(self) -> None:
        """Live round 2: the trigger is `.ProjectDropdownSubTrigger`, and a
        sibling `.SortDropdownSubTrigger` ('Recentes' — pt-BR) also matches a
        generic aria-haspopup='menu' probe. The cascade must try the stable
        class first and must never match the sort trigger."""
        assert "ProjectDropdownSubTrigger" in PICKER_PROJECT_SELECTOR_TRIGGERS[0]
        menu_tiers = [s for s in PICKER_PROJECT_SELECTOR_TRIGGERS if "aria-haspopup='menu'" in s]
        assert menu_tiers, "expected a generic menu-haspopup fallback tier"
        assert all("SortDropdownSubTrigger" in s and ":not(" in s for s in menu_tiers), (
            "the generic menu tier must exclude the SortDropdownSubTrigger"
        )

    @pytest.mark.asyncio
    async def test_switches_picker_to_target_project_when_it_differs(self) -> None:
        trigger = self._trigger_mock(references_target=False)
        menu = self._menu_mock()  # opens on the first click
        page = self._selector_page(trigger, menu=menu)

        result = await VideoGenerationMixin._ensure_picker_project(
            page, _PROJECT_ID_287, project_name="Chalkboard Spike", out_dir=None
        )

        assert result is True
        trigger.click.assert_awaited_once()
        # The option-match JS receives BOTH the id and the resolved name —
        # menu options render project NAMES, not ids (live round 2).
        match_args = page.evaluate.await_args.args[-1]
        assert match_args["projectId"] == _PROJECT_ID_287
        assert match_args["projectName"] == "Chalkboard Spike"

    @pytest.mark.asyncio
    async def test_noop_when_picker_already_on_target_project(self) -> None:
        trigger = self._trigger_mock(references_target=True)
        page = self._selector_page(trigger)

        result = await VideoGenerationMixin._ensure_picker_project(page, _PROJECT_ID_287)

        assert result is True
        trigger.click.assert_not_awaited()
        page.evaluate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_noop_when_picker_has_no_project_selector(self) -> None:
        """Older cohort: no project selector in the picker — the sync must be
        a pure no-op (nothing clicked, nothing evaluated)."""
        page = self._selector_page(None)

        result = await VideoGenerationMixin._ensure_picker_project(page, _PROJECT_ID_287)

        assert result is None
        page.evaluate.assert_not_awaited()
        page.keyboard.press.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_menu_open_falls_back_to_hover_for_radix_subtrigger(self) -> None:
        """Radix SubTrigger submenus may not open on plain click — the open
        sequence is click -> hover -> focus+ArrowRight, each verified against
        the portal-rendered `[role='menu'][data-state='open']`."""
        trigger = self._trigger_mock(references_target=False)
        # click does NOT open the menu; hover does.
        menu = self._menu_mock(wait_for=[TimeoutError("closed after click"), None])
        page = self._selector_page(trigger, menu=menu)

        result = await VideoGenerationMixin._ensure_picker_project(page, _PROJECT_ID_287)

        assert result is True
        trigger.hover.assert_awaited_once()
        arrow_presses = [
            c for c in page.keyboard.press.call_args_list if c.args and c.args[0] == "ArrowRight"
        ]
        assert not arrow_presses, "keyboard tier must not fire once hover opened the menu"

    @pytest.mark.asyncio
    async def test_switch_miss_escapes_dropdown_and_returns_false(self) -> None:
        """A selector exists but no portal candidate matches by href, id, OR
        name: close the dropdown (never leave an open overlay) and report
        False — the asset lookup proceeds and its telemetry captures state."""
        trigger = self._trigger_mock(references_target=False)
        page = self._selector_page(trigger, menu=self._menu_mock())

        def _eval(js: str, *args: object) -> object:
            if "inner_html" in js:
                return None
            if "clicked" in js:
                return {"clicked": False, "matched_by": None, "candidates": 0}
            return 5

        page.evaluate = AsyncMock(side_effect=_eval)

        result = await VideoGenerationMixin._ensure_picker_project(page, _PROJECT_ID_287)

        assert result is False
        page.keyboard.press.assert_awaited_with("Escape")

    def test_option_match_js_covers_non_aria_clickables(self) -> None:
        """Round-3 live miss: the OPEN portal contained ZERO elements with the
        classic menu-item ARIA roles (`menu_items: 0`) — the project list
        renders as something else. The matcher must sweep generic clickables
        (anchors FIRST: `href*=<project-id>` is the jackpot case that makes
        name resolution unnecessary) and never rely on ARIA roles alone."""
        assert "a, button, li, div[role]" in _PICKER_PROJECT_OPTION_MATCH_JS
        assert "getAttribute('href')" in _PICKER_PROJECT_OPTION_MATCH_JS
        # Name tier retained for markup without ids.
        assert "projectName" in _PICKER_PROJECT_OPTION_MATCH_JS

    @pytest.mark.asyncio
    async def test_menu_population_is_polled_before_matching(self) -> None:
        """Round-3 live miss: the open-state flips before the project list
        populates — matching (or dumping) an empty portal is a guaranteed
        miss. After the menu opens, poll for element children (300ms steps,
        bounded) and only then match."""
        trigger = self._trigger_mock(references_target=False)
        page = self._selector_page(trigger, menu=self._menu_mock())
        poll_calls: list[str] = []

        def _eval(js: str, *args: object) -> object:
            if "inner_html" in js:
                return None
            if "clicked" in js:
                return {"clicked": True, "matched_by": "href", "candidates": 12}
            poll_calls.append(js)
            return 0 if len(poll_calls) < 3 else 7  # populates on the 3rd poll

        page.evaluate = AsyncMock(side_effect=_eval)

        result = await VideoGenerationMixin._ensure_picker_project(page, _PROJECT_ID_287)

        assert result is True
        assert len(poll_calls) == 3, "polling must stop as soon as the portal has children"

    @pytest.mark.asyncio
    async def test_menu_never_populates_still_matches_and_dumps(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """A portal that never populates must not spin: the poll is bounded,
        the match is still attempted, and the miss telemetry reports zero
        elements."""
        trigger = self._trigger_mock(references_target=False)
        page = self._selector_page(trigger, menu=self._menu_mock())
        poll_calls: list[str] = []

        def _eval(js: str, *args: object) -> object:
            if "inner_html" in js:
                return None
            if "clicked" in js:
                return {"clicked": False, "matched_by": None, "candidates": 0}
            if "scrollTop" in js:
                return None  # menu-scroll probe: nothing scrollable to advance
            poll_calls.append(js)
            return 0  # never populates

        page.evaluate = AsyncMock(side_effect=_eval)

        result = await VideoGenerationMixin._ensure_picker_project(page, _PROJECT_ID_287)

        assert result is False
        assert len(poll_calls) == PICKER_PROJECT_MENU_POLLS
        waits = [c for c in page.wait_for_timeout.call_args_list if c.args]
        assert any(c.args[0] == PICKER_PROJECT_MENU_POLL_MS for c in waits)
        misses = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_project_switch_miss"
        ]
        assert misses[0]["menu_elements"] == 0

    @pytest.mark.asyncio
    async def test_switched_event_reports_href_match(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """An anchor with `href*=<project-id>` is the strongest match signal —
        the switched event must say which tier landed the click."""
        trigger = self._trigger_mock(references_target=False)
        page = self._selector_page(trigger, menu=self._menu_mock())

        def _eval(js: str, *args: object) -> object:
            if "inner_html" in js:
                return None
            if "clicked" in js:
                return {"clicked": True, "matched_by": "href", "candidates": 8}
            return 5

        page.evaluate = AsyncMock(side_effect=_eval)

        result = await VideoGenerationMixin._ensure_picker_project(page, _PROJECT_ID_287)

        assert result is True
        switched = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_project_switched"
        ]
        assert switched and switched[0]["matched_by"] == "href"

    @pytest.mark.asyncio
    async def test_switch_miss_writes_portal_dump(
        self, tmp_path: Path, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """Round-3 gap: the role-filtered items list came back empty and left
        us blind. The miss dump now captures the OPEN portal's raw innerHTML
        (bounded) plus a child count and tag histogram — raw markup can't
        lie about how the project list is really structured."""
        trigger = self._trigger_mock(references_target=False)
        page = self._selector_page(trigger, menu=self._menu_mock())
        portal_state = {
            "child_elements": 42,
            "tag_histogram": {"div": 30, "a": 6, "span": 6},
            "inner_html": "<div class='ScrollArea'><a href='/project/other'>gflow-cli t2i</a>",
        }

        def _eval(js: str, *args: object) -> object:
            if "inner_html" in js:
                return portal_state
            if "clicked" in js:
                return {"clicked": False, "matched_by": None, "candidates": 2}
            return 42

        page.evaluate = AsyncMock(side_effect=_eval)

        result = await VideoGenerationMixin._ensure_picker_project(
            page, _PROJECT_ID_287, project_name="Chalkboard Spike", out_dir=tmp_path
        )

        assert result is False
        dump_path = tmp_path / f"debug_picker_project_menu_{_PROJECT_ID_287[:8]}.json"
        assert dump_path.exists(), "switch miss must leave the portal dump in the out-dir"
        data = json.loads(dump_path.read_text(encoding="utf-8"))
        assert data["project_id"] == _PROJECT_ID_287
        assert data["project_name"] == "Chalkboard Spike"
        assert data["candidates"] == 2
        assert data["menu_elements"] == 42
        assert data["portal"] == portal_state
        misses = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_project_switch_miss"
        ]
        assert misses, "expected a picker_project_switch_miss event"
        assert misses[0]["menu_elements"] == 42
        assert misses[0]["candidates"] == 2
        assert misses[0]["menu_dump"] == str(dump_path)

    @pytest.mark.asyncio
    async def test_resolve_project_name_from_live_page(self) -> None:
        """The picker menu shows project NAMES; the CLI only knows the UUID.
        The name is resolved from the live page's raw signals (an element
        referencing the project id, e.g. an href — reflects renames, works
        for projects not created by gflow)."""
        page = _mock_async_page()
        page.evaluate = AsyncMock(
            return_value={"title": "", "href_text": "Chalkboard Spike", "class_text": ""}
        )

        name = await VideoGenerationMixin._resolve_project_name(page, _PROJECT_ID_287)

        assert name == "Chalkboard Spike"
        assert page.evaluate.await_args.args[-1] == _PROJECT_ID_287

    @pytest.mark.asyncio
    async def test_resolve_project_name_returns_none_when_page_has_no_hint(self) -> None:
        page = _mock_async_page()
        page.evaluate = AsyncMock(return_value=None)

        name = await VideoGenerationMixin._resolve_project_name(page, _PROJECT_ID_287)

        assert name is None

    def test_strip_flow_branding_variants(self) -> None:
        """Round-5 tier 0: the editor tab title carries the project name with
        Flow branding attached. The strip must be tolerant across separator
        variants and fall back to the raw title when no pattern matches."""
        strip = VideoGenerationMixin._strip_flow_branding
        assert strip("Chalkboard Spike - Flow") == "Chalkboard Spike"
        assert strip("Chalkboard Spike – Flow") == "Chalkboard Spike"  # en dash
        assert strip("Chalkboard Spike — Flow") == "Chalkboard Spike"  # em dash
        assert strip("Chalkboard Spike | Flow") == "Chalkboard Spike"
        assert strip("Flow - Chalkboard Spike") == "Chalkboard Spike"
        # Live round 5: the REAL observed pattern is a 'Google Flow - ' prefix.
        assert strip("Google Flow - gflow-cli t2i") == "gflow-cli t2i"
        assert strip("gflow-cli t2i - Google Flow") == "gflow-cli t2i"
        assert strip("Chalkboard Spike") == "Chalkboard Spike"  # raw fallback

    def test_trigger_active_probe_matches_name_by_contains(self) -> None:
        """Round 5 waste: the trigger's textContent carries the active project
        NAME plus icon-ligature noise, so an exact-equality probe missed and
        ~30 menu probes hunted for the project we were already in. The
        already-active probe must accept a contains-match on the resolved
        name (the trigger only ever shows the ACTIVE project, so a substring
        hit cannot select a wrong one)."""
        assert ".includes(norm(args.projectName))" in _PICKER_PROJECT_TRIGGER_ACTIVE_JS

    @pytest.mark.asyncio
    async def test_resolver_tier0_uses_document_title(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """Round 5: we navigated to /project/<id> BEFORE the picker opened, so
        document.title is the strongest name signal. The raw title is logged
        on every resolution so the real branding pattern is learnable."""
        page = _mock_async_page()
        page.evaluate = AsyncMock(
            return_value={
                "title": "Chalkboard Spike – Flow",
                "href_text": "other",
                "class_text": "",
            }
        )

        name = await VideoGenerationMixin._resolve_project_name(page, _PROJECT_ID_287)

        assert name == "Chalkboard Spike"
        resolved = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_project_name_resolved"
        ]
        assert resolved[0]["source"] == "title"
        assert resolved[0]["raw_title"] == "Chalkboard Spike – Flow"

    @pytest.mark.asyncio
    async def test_resolver_rejects_branding_only_title(self) -> None:
        """A title that is ONLY Flow branding must never become the candidate
        name — 'flow' as a contains-match would hit 'gflow-cli i2i' and click
        the wrong project. Falls through to the href tier."""
        page = _mock_async_page()
        page.evaluate = AsyncMock(
            return_value={"title": "Flow", "href_text": "gflow-cli i2i", "class_text": ""}
        )

        name = await VideoGenerationMixin._resolve_project_name(page, _PROJECT_ID_287)

        assert name == "gflow-cli i2i"

    @pytest.mark.asyncio
    async def test_resolver_unresolved_reports_raw_title(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        page = _mock_async_page()
        page.evaluate = AsyncMock(return_value={"title": "Flow", "href_text": "", "class_text": ""})

        name = await VideoGenerationMixin._resolve_project_name(page, _PROJECT_ID_287)

        assert name is None
        unresolved = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_project_name_unresolved"
        ]
        assert unresolved[0]["raw_title"] == "Flow"

    @pytest.mark.asyncio
    async def test_sync_project_name_override_beats_resolution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--project-name` / GFLOW_CLI_PROJECT_NAME is the highest-precedence
        name source — the user-supplied display name is used verbatim and the
        page resolver is not even consulted."""
        ensure = AsyncMock()
        resolver = AsyncMock(return_value="Derived Name")
        monkeypatch.setattr(VideoGenerationMixin, "_ensure_picker_project", ensure)
        monkeypatch.setattr(VideoGenerationMixin, "_resolve_project_name", resolver)
        page = _mock_async_page()
        page.url = _PROJECT_URL_287

        await VideoGenerationMixin._sync_picker_project(page, project_name="User Given")

        resolver.assert_not_awaited()
        assert ensure.await_args.kwargs["project_name"] == "User Given"

    @pytest.mark.asyncio
    async def test_menu_scroll_reaches_project_below_the_fold(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """Round 5: the project menu lists EVERY project (80 observed,
        recency-ordered, timestamp labels for unnamed ones) — the target's
        entry can sit below the visible fold. When the in-view match misses,
        the open portal is scrolled with the progress-bounded pattern and
        re-matched after every scroll."""
        trigger = self._trigger_mock(references_target=False)
        page = self._selector_page(trigger, menu=self._menu_mock())
        match_calls = 0
        scroll_calls = 0

        def _eval(js: str, *args: object) -> object:
            nonlocal match_calls, scroll_calls
            if "inner_html" in js:
                return None
            if "clicked" in js:
                match_calls += 1
                if match_calls >= 4:  # in-view miss + 2 scrolled misses, hit on 3rd scroll
                    return {"clicked": True, "matched_by": "name", "candidates": 80}
                return {"clicked": False, "matched_by": None, "candidates": 80}
            if "scrollTop" in js:
                scroll_calls += 1
                return [f"item-{scroll_calls}-{i}" for i in range(5)]  # window advances
            return 160  # population poll

        page.evaluate = AsyncMock(side_effect=_eval)

        result = await VideoGenerationMixin._ensure_picker_project(
            page, _PROJECT_ID_287, project_name="Chalkboard Spike"
        )

        assert result is True
        assert scroll_calls == 3, "the scroll loop must re-match after each scroll"
        probes = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_project_menu_scroll_probe"
        ]
        assert len(probes) == 3
        done = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_project_menu_scroll_done"
        ]
        assert done and done[-1]["reason"] == "found"

    @pytest.mark.asyncio
    async def test_menu_scroll_stall_terminates(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """A menu whose rendered item set stops changing (end of list) must
        stall-terminate — never spin to the ceiling — and still fall through
        to the miss dump."""
        trigger = self._trigger_mock(references_target=False)
        page = self._selector_page(trigger, menu=self._menu_mock())

        def _eval(js: str, *args: object) -> object:
            if "inner_html" in js:
                return None
            if "clicked" in js:
                return {"clicked": False, "matched_by": None, "candidates": 80}
            if "scrollTop" in js:
                return ["item-a", "item-b"]  # never changes -> end of list
            return 160

        page.evaluate = AsyncMock(side_effect=_eval)

        result = await VideoGenerationMixin._ensure_picker_project(page, _PROJECT_ID_287)

        assert result is False
        done = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_project_menu_scroll_done"
        ]
        assert done[-1]["reason"] == "stall"
        assert done[-1]["attempts"] == PICKER_GRID_SCROLL_STALL_LIMIT + 1
        misses = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_project_switch_miss"
        ]
        assert misses, "a stalled scroll must still produce the miss dump"

    @pytest.mark.asyncio
    async def test_sync_derives_target_project_from_page_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`--project` navigation put the project id in the editor URL — the
        sync wrapper derives the target from there (no signature threading)."""
        ensure = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_ensure_picker_project", ensure)
        page = _mock_async_page()
        page.url = f"{_PROJECT_URL_287}?media=x"

        await VideoGenerationMixin._sync_picker_project(page)

        assert ensure.await_args.args[-1] == _PROJECT_ID_287

    @pytest.mark.asyncio
    async def test_sync_passes_resolved_name_and_out_dir_to_ensure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        ensure = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_ensure_picker_project", ensure)
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_resolve_project_name",
            AsyncMock(return_value="Chalkboard Spike"),
        )
        page = _mock_async_page()
        page.url = _PROJECT_URL_287

        await VideoGenerationMixin._sync_picker_project(page, out_dir=tmp_path)

        assert ensure.await_args.kwargs["project_name"] == "Chalkboard Spike"
        assert ensure.await_args.kwargs["out_dir"] == tmp_path

    @pytest.mark.asyncio
    async def test_sync_noop_when_page_url_has_no_project(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ensure = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_ensure_picker_project", ensure)
        page = _mock_async_page()  # page.url is a MagicMock, not a str

        await VideoGenerationMixin._sync_picker_project(page)

        ensure.assert_not_awaited()


class TestSelectExistingAssetDiagnostics:
    """#287 live-diagnosis telemetry: the first live verification failed with
    ZERO events from the new code paths, so it was impossible to tell whether
    the search tiers ran, whether the progress probe saw any tiles, or
    whether the tile matcher missed. Every decision point now emits a
    structured event, and a final not-found writes a bounded picker DOM dump
    (+ screenshot) to the out-dir so the next live miss shows what the picker
    was actually rendering."""

    _FULL_UUID = "d6f1927a-3eae-4626-bc90-9a6ea7637bab"

    @pytest.mark.asyncio
    async def test_search_tier_event_redacts_term_and_reports_rendered_count(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=None,
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        page.evaluate = AsyncMock(return_value=["tile-a", "tile-b"])

        result = await VideoGenerationMixin._select_existing_asset(
            page, self._FULL_UUID, "Brass key", out_dir=None
        )

        assert result is not None
        tiers = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_search_tier"
        ]
        assert tiers, "expected a picker_search_tier event"
        assert "term" not in tiers[0]
        assert tiers[0]["term_length"] == len("Brass key")
        assert tiers[0]["found"] is True
        assert tiers[0]["rendered_tiles"] == 2

    @pytest.mark.asyncio
    async def test_stall_termination_emits_probe_and_done_events(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=TimeoutError("never visible"),
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        page.evaluate = AsyncMock(return_value=["tile-a"])  # grid never advances

        result = await VideoGenerationMixin._scroll_picker_grid_until_rendered(page, tile)

        assert result is False
        probes = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_scroll_probe"
        ]
        assert len(probes) == PICKER_GRID_SCROLL_STALL_LIMIT + 1
        assert probes[0]["rendered_tiles"] == 1
        assert probes[0]["new_tiles"] is None  # no previous fingerprint yet
        assert probes[1]["new_tiles"] == 0
        done = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_scroll_done"
        ]
        assert done, "expected a picker_scroll_done event"
        assert done[-1]["reason"] == "stall"
        assert done[-1]["attempts"] == PICKER_GRID_SCROLL_STALL_LIMIT + 1
        assert done[-1]["found"] is False

    @pytest.mark.asyncio
    async def test_legacy_budget_termination_is_reported_when_probe_blind(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """When the DOM probe yields no evidence (cohort DOM drift — deviation
        the first live run could not distinguish), the done event must SAY the
        legacy budget fired, so a silent fallback is observable in the log."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=TimeoutError("never visible"),
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        # page.evaluate deliberately NOT configured -> probe yields no evidence.

        result = await VideoGenerationMixin._scroll_picker_grid_until_rendered(page, tile)

        assert result is False
        done = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_scroll_done"
        ]
        assert done[-1]["reason"] == "legacy_budget"
        assert done[-1]["attempts"] == PICKER_GRID_SCROLL_ATTEMPTS
        assert done[-1]["found"] is False
        probes = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.picker_scroll_probe"
        ]
        assert probes and all(e["rendered_tiles"] is None for e in probes)

    @pytest.mark.asyncio
    async def test_not_found_writes_bounded_dom_dump(
        self, tmp_path: Path, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=TimeoutError("never visible"),
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        page.url = _PROJECT_URL_287
        page.screenshot = AsyncMock()
        dom_state = {
            "tile_count": 2,
            "tiles": ["<div role='option'><img src='...other-uuid...'/></div>"],
            "container_attrs": ["role=dialog", "aria-modal=true"],
            "project_selector_candidates": ["<button aria-haspopup='listbox'>Scratch 3</button>"],
            "target_project_in_dialog": False,
        }

        def _eval(js: str, *args: object) -> object:
            # The DOM-dump JS is the only one reading outerHTML.
            return dom_state if "outerHTML" in js else ["tile-a"]

        page.evaluate = AsyncMock(side_effect=_eval)

        result = await VideoGenerationMixin._select_existing_asset(
            page, self._FULL_UUID, "Missing catalog name", out_dir=tmp_path
        )

        assert result is False
        dump_path = tmp_path / f"debug_picker_dom_{self._FULL_UUID[:8]}.json"
        assert dump_path.exists(), "not-found must leave a picker DOM dump in the out-dir"
        data = json.loads(dump_path.read_text(encoding="utf-8"))
        assert data["media_id"] == self._FULL_UUID
        assert data["project_id"] == _PROJECT_ID_287
        assert data["picker"]["tile_count"] == 2
        assert data["picker"]["project_selector_candidates"]
        misses = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.existing_asset_not_found"
        ]
        assert misses, "expected an existing_asset_not_found event"
        assert misses[0]["media_id"] == self._FULL_UUID
        assert misses[0]["project_id"] == _PROJECT_ID_287
        assert misses[0]["dom_dump"] == str(dump_path)
        assert misses[0]["screenshot"], "expected a screenshot path"
        assert "resolved_by" not in misses[0]

    @pytest.mark.asyncio
    async def test_dom_dump_capture_failure_reports_none(
        self, tmp_path: Path, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """0.32.1 contract: a capture failure must never report a file that
        was not written — the miss event carries None, not a phantom path."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=TimeoutError("never visible"),
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        page.screenshot = AsyncMock(side_effect=RuntimeError("no screenshot either"))

        def _eval(js: str, *args: object) -> object:
            if "outerHTML" in js:
                raise RuntimeError("cohort DOM drift")
            return ["tile-a"]

        page.evaluate = AsyncMock(side_effect=_eval)

        result = await VideoGenerationMixin._select_existing_asset(
            page, self._FULL_UUID, "Missing catalog name", out_dir=tmp_path
        )

        assert result is False
        misses = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.existing_asset_not_found"
        ]
        assert misses[0]["dom_dump"] is None
        assert misses[0]["screenshot"] is None
        assert not list(tmp_path.glob("*.json"))


class TestAttachImageUuidRefsPickerScroll:
    """Image UUID refs search catalog names and retain local upload fallback."""

    @staticmethod
    def _dialog_mock() -> MagicMock:
        dialog = MagicMock()
        dialog.last = dialog
        dialog.hover = AsyncMock()
        dialog.wait_for = AsyncMock()  # closes immediately (one-step image attach)
        return dialog

    @staticmethod
    def _add_media_mock() -> MagicMock:
        add = MagicMock()
        add.first = add
        add.wait_for = AsyncMock()
        add.click = AsyncMock()
        return add

    @staticmethod
    def _search_mock() -> MagicMock:
        search = MagicMock()
        search.first = search
        search.press_sequentially = AsyncMock()
        search.fill = AsyncMock()
        search.wait_for = AsyncMock()
        search.count = AsyncMock(return_value=1)
        return search

    @staticmethod
    def _never_found_tile() -> MagicMock:
        tile = MagicMock()
        tile.first = tile
        tile.click = AsyncMock()
        tile.wait_for = AsyncMock(side_effect=TimeoutError("never visible"))
        tile.count = AsyncMock(return_value=0)
        return tile

    def _make_page(
        self, tiles: dict[str, MagicMock], *, search: MagicMock | None = None
    ) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
        page = _mock_async_page()
        add_media = self._add_media_mock()
        search = search if search is not None else self._search_mock()
        dialog = self._dialog_mock()

        def _locator(selector: str) -> MagicMock:
            if selector == ADD_MEDIA_BUTTON:
                return add_media
            if selector == PICKER_SEARCH_INPUT:
                return search
            if selector == DIALOG_ANY:
                return dialog
            for media_id, tile in tiles.items():
                if media_id in selector:
                    return tile
            raise AssertionError(f"unexpected picker selector: {selector!r}")

        page.locator = MagicMock(side_effect=_locator)
        page.mouse = MagicMock()
        page.mouse.wheel = AsyncMock()
        return page, add_media, search, dialog

    @pytest.mark.asyncio
    async def test_tile_never_found_falls_back_to_local_upload(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        install_log_capture: structlog.testing.LogCapture,
    ) -> None:
        tile = self._never_found_tile()
        page, _, _, _ = self._make_page({"uuid-1": tile})
        upload = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", upload)
        local_path = tmp_path / "cabin.png"
        content = b"recorded cabin"
        local_path.write_bytes(content)

        await VideoGenerationMixin._attach_image_uuid_refs(
            page,
            [("uuid-1", "Cabin", str(local_path), hashlib.sha256(content).hexdigest())],
            out_dir=None,
        )

        upload.assert_awaited_once()
        args, _ = upload.call_args
        assert args[1] == local_path
        page.mouse.wheel.assert_not_awaited()
        fallback = [
            event
            for event in install_log_capture.entries
            if event["event"] == "ui_automation_video.image_ref_upload_fallback"
        ]
        assert fallback[0]["resolved_by"] == "upload"

    @pytest.mark.asyncio
    async def test_image_fallback_is_reverified_immediately_before_upload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        tile = self._never_found_tile()
        page, _, _, _ = self._make_page({"uuid-1": tile})
        monkeypatch.setattr(
            VideoGenerationMixin, "_select_existing_asset", AsyncMock(return_value=False)
        )
        upload = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", upload)
        local_path = tmp_path / "recorded.png"
        original = b"recorded bytes"
        local_path.write_bytes(original)
        expected_sha256 = hashlib.sha256(original).hexdigest()
        local_path.write_bytes(b"private change")

        with pytest.raises(TransportTimeoutError, match="changed since it was recorded"):
            await VideoGenerationMixin._attach_image_uuid_refs(
                page,
                [("uuid-1", "Cabin", str(local_path), expected_sha256)],
                out_dir=None,
            )

        upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resolved_by_carries_no_search_term(
        self,
        monkeypatch: pytest.MonkeyPatch,
        install_log_capture: structlog.testing.LogCapture,
    ) -> None:
        display_name = "private catalog name must not escape"
        tile = self._never_found_tile()
        page, _, _, _ = self._make_page({"uuid-1": tile})
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_select_existing_asset",
            AsyncMock(return_value=True),
        )

        await VideoGenerationMixin._attach_image_uuid_refs(
            page, [("uuid-1", display_name, "", "")], out_dir=None
        )

        selected = [
            event
            for event in install_log_capture.entries
            if event["event"] == "ui_automation_video.image_ref_selected_existing"
        ][0]
        assert selected["resolved_by"] == "display_name"
        assert "term" not in selected
        assert display_name not in str(selected)

    @pytest.mark.asyncio
    async def test_tile_never_found_and_no_local_path_raises_same_message(self) -> None:
        tile = self._never_found_tile()
        page, _, _, _ = self._make_page({"uuid-1": tile})

        with pytest.raises(TransportTimeoutError) as excinfo:
            await VideoGenerationMixin._attach_image_uuid_refs(
                page, [("uuid-1", "Cabin", "", "")], out_dir=None
            )

        assert excinfo.value.detail == (
            "image ref 'uuid-1' could not be selected in the picker and "
            "has no local file to upload — re-generate it or pass a local path."
        )

    @pytest.mark.asyncio
    async def test_search_input_cleared_between_refs(self) -> None:
        tile_1 = MagicMock()
        tile_1.first = tile_1
        tile_1.click = AsyncMock()
        tile_1.wait_for = AsyncMock()
        tile_1.evaluate = AsyncMock(return_value=True)
        tile_1.count = AsyncMock(return_value=0)

        tile_2 = MagicMock()
        tile_2.first = tile_2
        tile_2.click = AsyncMock()
        tile_2.wait_for = AsyncMock()  # visible immediately, no search needed
        tile_2.evaluate = AsyncMock(return_value=True)
        tile_2.count = AsyncMock(return_value=0)

        page, _, search, _ = self._make_page({"uuid-1": tile_1, "uuid-2": tile_2})

        await VideoGenerationMixin._attach_image_uuid_refs(
            page,
            [("uuid-1", "Cabin", "", ""), ("uuid-2", "Lighthouse", "", "")],
            out_dir=None,
        )

        assert [call.args[0] for call in search.press_sequentially.await_args_list] == [
            "Cabin",
            "Lighthouse",
        ]
        # ...but the search box must be cleared before EVERY ref's lookup
        # (#282: a leftover search term from ref 1 previously shadowed ref 2).
        # Each lookup clears the previous term before typing its own.
        assert search.fill.await_count == 2
        assert all(c.args == ("",) for c in search.fill.call_args_list)

    @pytest.mark.asyncio
    async def test_picker_project_synced_before_every_ref_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#287 primary hypothesis: the picker's library view has its own
        active project — it must be synced to the target project BEFORE each
        ref's lookup, or the lookup scans the wrong project's grid."""
        calls: list[str] = []

        async def _sync(page: object, **kwargs: object) -> None:
            calls.append("sync")

        async def _select(*args: object, **kwargs: object) -> bool:
            calls.append("select")
            return True

        monkeypatch.setattr(VideoGenerationMixin, "_sync_picker_project", _sync)
        monkeypatch.setattr(VideoGenerationMixin, "_select_existing_asset", _select)
        tile = self._never_found_tile()
        page, _, _, _ = self._make_page({"uuid-1": tile, "uuid-2": tile})

        await VideoGenerationMixin._attach_image_uuid_refs(
            page,
            [("uuid-1", "", "", ""), ("uuid-2", "", "", "")],
            out_dir=None,
        )

        assert calls == ["sync", "select", "sync", "select"]


# ---------------------------------------------------------------------------
# #287: i2v frame slots accept an in-project asset UUID; upload rejections
# surface as a typed error instead of a bare RuntimeError.
# ---------------------------------------------------------------------------

_FRAME_REF_UUID = "d6f1927a-3eae-4626-bc90-9a6ea7637bab"
_FRAME_DISPLAY_NAME = "Brass key on marble surface"


def _frame_dialog_page() -> MagicMock:
    """Page mock for the frame-slot media dialog: locator() yields a search
    input whose count/fill are awaitable (absent from _cascade_page's fake)."""
    page = MagicMock()
    loc = MagicMock()
    loc.first = loc
    loc.count = AsyncMock(return_value=1)
    loc.fill = AsyncMock()
    loc.wait_for = AsyncMock()
    loc.press_sequentially = AsyncMock()
    page.locator = MagicMock(return_value=loc)
    page.wait_for_timeout = AsyncMock()
    return page


class TestAttachFrameByMediaId:
    @pytest.mark.asyncio
    async def test_selects_existing_asset_in_the_frame_dialog(
        self,
        monkeypatch: pytest.MonkeyPatch,
        install_log_capture: structlog.testing.LogCapture,
    ) -> None:
        slot = MagicMock()
        slot.click = AsyncMock()
        monkeypatch.setattr(
            VideoGenerationMixin, "_resolve_frame_slot", AsyncMock(return_value=slot)
        )
        select = AsyncMock(return_value=True)
        monkeypatch.setattr(VideoGenerationMixin, "_select_existing_asset", select)
        page = _frame_dialog_page()
        await VideoGenerationMixin._attach_frame_by_media_id(
            page, 0, "Start", _FRAME_REF_UUID, _FRAME_DISPLAY_NAME, out_dir=None
        )
        slot.click.assert_awaited_once()
        assert select.await_args.args[1] == _FRAME_REF_UUID
        assert select.await_args.args[2] == _FRAME_DISPLAY_NAME
        attached = [
            event
            for event in install_log_capture.entries
            if event["event"] == "ui_automation_video.frame_ref_attached"
        ]
        assert attached[0]["resolved_by"] == "display_name"

    @pytest.mark.asyncio
    async def test_missing_asset_raises_transport_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slot = MagicMock()
        slot.click = AsyncMock()
        monkeypatch.setattr(
            VideoGenerationMixin, "_resolve_frame_slot", AsyncMock(return_value=slot)
        )
        monkeypatch.setattr(
            VideoGenerationMixin, "_select_existing_asset", AsyncMock(return_value=None)
        )
        page = _frame_dialog_page()
        with pytest.raises(TransportTimeoutError, match=_FRAME_REF_UUID):
            await VideoGenerationMixin._attach_frame_by_media_id(
                page, 0, "Start", _FRAME_REF_UUID, _FRAME_DISPLAY_NAME, out_dir=None
            )

    @pytest.mark.asyncio
    async def test_named_picker_miss_uploads_recorded_local_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        slot = MagicMock()
        slot.click = AsyncMock()
        monkeypatch.setattr(
            VideoGenerationMixin, "_resolve_frame_slot", AsyncMock(return_value=slot)
        )
        monkeypatch.setattr(
            VideoGenerationMixin, "_select_existing_asset", AsyncMock(return_value=None)
        )
        upload = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", upload)
        local_path = tmp_path / "recorded-frame.png"
        content = b"\x89PNG\r\n\x1a\n"
        local_path.write_bytes(content)

        await VideoGenerationMixin._attach_frame_by_media_id(
            _frame_dialog_page(),
            0,
            "Start",
            _FRAME_REF_UUID,
            _FRAME_DISPLAY_NAME,
            out_dir=None,
            local_path=local_path,
            local_sha256=hashlib.sha256(content).hexdigest(),
        )

        upload.assert_awaited_once()
        assert upload.await_args.args[1] == local_path
        assert upload.await_args.kwargs == {
            "log_label": "Start_frame_ref",
            "out_dir": None,
        }

    @pytest.mark.asyncio
    async def test_frame_fallback_is_reverified_immediately_before_upload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        slot = MagicMock()
        slot.click = AsyncMock()
        monkeypatch.setattr(
            VideoGenerationMixin, "_resolve_frame_slot", AsyncMock(return_value=slot)
        )
        monkeypatch.setattr(
            VideoGenerationMixin, "_select_existing_asset", AsyncMock(return_value=False)
        )
        upload = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", upload)
        local_path = tmp_path / "recorded-frame.png"
        original = b"recorded bytes"
        local_path.write_bytes(original)
        expected_sha256 = hashlib.sha256(original).hexdigest()
        local_path.write_bytes(b"private change")

        with pytest.raises(TransportTimeoutError, match="changed since it was recorded"):
            await VideoGenerationMixin._attach_frame_by_media_id(
                _frame_dialog_page(),
                0,
                "Start",
                _FRAME_REF_UUID,
                _FRAME_DISPLAY_NAME,
                out_dir=None,
                local_path=local_path,
                local_sha256=expected_sha256,
            )

        upload.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_picker_project_synced_before_frame_ref_selection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#287 primary hypothesis: the frame-slot media dialog's library view
        must be aligned to the target project BEFORE the UUID lookup runs."""
        calls: list[str] = []
        slot = MagicMock()
        slot.click = AsyncMock()
        monkeypatch.setattr(
            VideoGenerationMixin, "_resolve_frame_slot", AsyncMock(return_value=slot)
        )

        async def _sync(page: object, **kwargs: object) -> None:
            calls.append("sync")

        async def _select(*args: object, **kwargs: object) -> bool:
            calls.append("select")
            return True

        monkeypatch.setattr(VideoGenerationMixin, "_sync_picker_project", _sync)
        monkeypatch.setattr(VideoGenerationMixin, "_select_existing_asset", _select)
        page = _frame_dialog_page()

        await VideoGenerationMixin._attach_frame_by_media_id(
            page, 0, "Start", _FRAME_REF_UUID, _FRAME_DISPLAY_NAME, out_dir=None
        )

        assert calls == ["sync", "select"]


class TestAttachI2VFramesRefIdRouting:
    @pytest.mark.asyncio
    async def test_ref_ids_route_to_attach_frame_by_media_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.video import GenerateVideoRequest, Mode

        by_id = AsyncMock()
        local = AsyncMock()
        remote = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_attach_frame_by_media_id", by_id)
        monkeypatch.setattr(VideoGenerationMixin, "_attach_frame", local)
        monkeypatch.setattr(VideoGenerationMixin, "_attach_remote_frame", remote)
        request = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            start_image_ref_id=_FRAME_REF_UUID,
            end_image_ref_id=_FRAME_REF_UUID,
            start_image_ref_display_name="Start key",
            end_image_ref_display_name="End key",
            start_image_ref_local_path=Path("start.png"),
            end_image_ref_local_path=Path("end.png"),
            start_image_ref_local_sha256="a" * 64,
            end_image_ref_local_sha256="b" * 64,
        )
        page = _cascade_page(set())
        await VideoGenerationMixin._attach_i2v_frames(page, request, out_dir=None)
        assert by_id.await_count == 2
        local.assert_not_awaited()
        remote.assert_not_awaited()
        slots = [(c.args[1], c.args[2]) for c in by_id.await_args_list]
        assert slots == [(0, "Start"), (1, "End")]
        assert [c.args[4] for c in by_id.await_args_list] == ["Start key", "End key"]
        assert [c.kwargs["local_path"] for c in by_id.await_args_list] == [
            Path("start.png"),
            Path("end.png"),
        ]
        assert [c.kwargs["local_sha256"] for c in by_id.await_args_list] == [
            "a" * 64,
            "b" * 64,
        ]

    @pytest.mark.asyncio
    async def test_project_name_override_reaches_frame_by_media_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#287 round 5: `--project-name` rides the request into the frame-ref
        attach, where the picker project-menu match consumes it."""
        from gflow_cli.api.video import GenerateVideoRequest, Mode

        by_id = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_attach_frame_by_media_id", by_id)
        request = GenerateVideoRequest(
            prompt="x",
            mode=Mode.I2V,
            start_image_ref_id=_FRAME_REF_UUID,
            project_name="Chalkboard Spike",
        )
        page = _cascade_page(set())
        await VideoGenerationMixin._attach_i2v_frames(page, request, out_dir=None)
        assert by_id.await_args.kwargs["project_name"] == "Chalkboard Spike"


class TestUploadRejectionTypedError:
    @pytest.mark.asyncio
    async def test_http_400_raises_media_upload_rejected_error(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from gflow_cli.errors import MediaUploadRejectedError

        handlers: dict[str, Any] = {}
        page = MagicMock()
        page.on = MagicMock(side_effect=handlers.__setitem__)
        page.remove_listener = MagicMock()
        page.wait_for_timeout = AsyncMock()

        chooser = MagicMock()

        async def _set_files(_path: str) -> None:
            handlers["response"](SimpleNamespace(url="https://x/uploadImage?y", status=400))

        chooser.set_files = AsyncMock(side_effect=_set_files)

        class _FcInfo:
            @property
            def value(self) -> Any:
                async def _get() -> MagicMock:
                    return chooser

                return _get()

        class _FcCm:
            async def __aenter__(self) -> _FcInfo:
                return _FcInfo()

            async def __aexit__(self, *args: object) -> bool:
                return False

        page.expect_file_chooser = MagicMock(return_value=_FcCm())
        loc = MagicMock()
        loc.first = loc
        loc.click = AsyncMock()
        page.locator = MagicMock(return_value=loc)

        image = tmp_path / "s1.jpg"
        image.write_bytes(b"\xff\xd8\xff")
        with pytest.raises(MediaUploadRejectedError, match=r"HTTP\s*400"):
            await VideoGenerationMixin._upload_via_open_dialog(
                page, image, log_label="Start", out_dir=None
            )
        page.remove_listener.assert_called_once()  # finally-detach on the raise path


class TestCaptureDebugScreenshotFailure:
    @pytest.mark.asyncio
    async def test_capture_failure_returns_none_not_a_phantom_path(self, tmp_path: Path) -> None:
        """#283 follow-up (observed live 2026-07-11): when page.screenshot
        raises, the function reported a path that was never written — error
        messages then pointed users at a nonexistent file."""
        from gflow_cli.api.transports.ui_automation_video import _capture_debug_screenshot

        page = MagicMock()
        page.screenshot = AsyncMock(side_effect=RuntimeError("target closed"))
        shot = await _capture_debug_screenshot(page, tmp_path, "nope.png")
        assert shot is None
        assert not (tmp_path / "nope.png").exists()

    @pytest.mark.asyncio
    async def test_image_module_copy_also_returns_none_on_failure(self, tmp_path: Path) -> None:
        # The function is duplicated in ui_automation.py (circular-import
        # discipline) — the failure-path fix must hold in BOTH copies.
        from gflow_cli.api.transports.ui_automation import (
            _capture_debug_screenshot as ua_capture,
        )

        page = MagicMock()
        page.screenshot = AsyncMock(side_effect=RuntimeError("target closed"))
        assert await ua_capture(page, tmp_path, "nope.png") is None


class TestAttachReferencesDedup:
    """#314: image i2i dedups a repeated local ref by selecting the existing
    library asset (Flow names uploads by their exact filename) instead of
    re-uploading a duplicate. R2V video keeps upload-every-time (default)."""

    @staticmethod
    def _picker(*, search_present: bool = True, tile_present: bool = True) -> tuple:
        page = MagicMock()
        search = MagicMock()
        search.first = search
        search.count = AsyncMock(return_value=1 if search_present else 0)
        search.fill = AsyncMock()
        search.press_sequentially = AsyncMock()
        tile = MagicMock()
        tile.first = tile
        tile.count = AsyncMock(return_value=1 if tile_present else 0)
        tile.click = AsyncMock()
        role_loc = MagicMock()
        role_loc.filter = MagicMock(return_value=tile)  # get_by_role("option").filter(...)
        dialog = MagicMock()
        dialog.last = dialog
        dialog.wait_for = AsyncMock()  # goes hidden → image auto-attach on click

        def _locator(sel: str) -> MagicMock:
            return dialog if sel == DIALOG_ANY else search

        page.locator = MagicMock(side_effect=_locator)
        page.get_by_role = MagicMock(return_value=role_loc)
        page.get_by_alt_text = MagicMock(return_value=MagicMock())
        page.wait_for_timeout = AsyncMock()
        return page, search, tile

    @pytest.mark.asyncio
    async def test_selects_existing_when_filename_matches(self, monkeypatch) -> None:
        monkeypatch.setattr(VideoGenerationMixin, "_sync_picker_project", AsyncMock())
        page, search, tile = self._picker(tile_present=True)

        got = await VideoGenerationMixin._try_select_existing_by_filename(
            page, "zzdedupprobe.png", out_dir=None
        )

        assert got is True
        tile.click.assert_awaited()
        # Matched by the img ALT = the EXACT filename (locale-invariant; the
        # option's accessible name also carries a localised "Image" suffix).
        assert page.get_by_alt_text.call_args.args[0] == "zzdedupprobe.png"
        assert page.get_by_alt_text.call_args.kwargs.get("exact") is True
        search.press_sequentially.assert_awaited()

    @pytest.mark.asyncio
    async def test_returns_false_and_clears_search_on_no_match(self, monkeypatch) -> None:
        monkeypatch.setattr(VideoGenerationMixin, "_sync_picker_project", AsyncMock())
        # Not in the initial DOM AND not surfaced by a virtualised-grid scroll.
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_scroll_picker_grid_until_rendered",
            AsyncMock(return_value=False),
        )
        page, search, tile = self._picker(tile_present=False)

        got = await VideoGenerationMixin._try_select_existing_by_filename(
            page, "novel.png", out_dir=None
        )

        assert got is False
        tile.click.assert_not_awaited()
        # Cleared the filter so the upload fallback starts from a clean grid.
        assert search.fill.await_args_list[-1].args == ("",)

    @pytest.mark.asyncio
    async def test_scroll_fallback_finds_offscreen_match(self, monkeypatch) -> None:
        # An existing match absent from the initial DOM is surfaced by scrolling
        # the virtualised grid → it must be selected, NOT re-uploaded (finding #1).
        monkeypatch.setattr(VideoGenerationMixin, "_sync_picker_project", AsyncMock())
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_scroll_picker_grid_until_rendered",
            AsyncMock(return_value=True),
        )
        page, _search, tile = self._picker(tile_present=False)  # count 0 initially

        got = await VideoGenerationMixin._try_select_existing_by_filename(
            page, "offscreen.png", out_dir=None
        )

        assert got is True
        tile.click.assert_awaited()  # attached via the scrolled-into-view tile

    @pytest.mark.asyncio
    async def test_returns_false_when_no_search_box(self, monkeypatch) -> None:
        monkeypatch.setattr(VideoGenerationMixin, "_sync_picker_project", AsyncMock())
        page, _search, tile = self._picker(search_present=False)

        got = await VideoGenerationMixin._try_select_existing_by_filename(
            page, "x.png", out_dir=None
        )

        assert got is False
        tile.click.assert_not_awaited()

    @staticmethod
    def _attach_page() -> MagicMock:
        page = MagicMock()
        add = MagicMock()
        add.first = add
        add.wait_for = AsyncMock()
        add.click = AsyncMock()
        page.locator = MagicMock(return_value=add)
        page.wait_for_timeout = AsyncMock()
        return page

    @pytest.mark.asyncio
    async def test_prefer_existing_selects_and_skips_upload(self, monkeypatch, tmp_path) -> None:
        ref = tmp_path / "son.jpg"
        ref.write_bytes(b"x")
        select = AsyncMock(return_value=True)
        upload = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_try_select_existing_by_filename", select)
        monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", upload)

        await VideoGenerationMixin._attach_references(
            self._attach_page(), [ref], out_dir=None, prefer_existing=True
        )

        select.assert_awaited_once()
        upload.assert_not_awaited()  # deduped → no re-upload

    @pytest.mark.asyncio
    async def test_prefer_existing_uploads_when_no_match(self, monkeypatch, tmp_path) -> None:
        ref = tmp_path / "fresh.jpg"
        ref.write_bytes(b"x")
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_try_select_existing_by_filename",
            AsyncMock(return_value=False),
        )
        upload = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", upload)

        await VideoGenerationMixin._attach_references(
            self._attach_page(), [ref], out_dir=None, prefer_existing=True
        )

        upload.assert_awaited_once()  # fallback upload for a fresh file

    @pytest.mark.asyncio
    async def test_default_never_dedups_video_path(self, monkeypatch, tmp_path) -> None:
        ref = tmp_path / "frame.jpg"
        ref.write_bytes(b"x")
        select = AsyncMock(return_value=True)
        upload = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_try_select_existing_by_filename", select)
        monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", upload)

        # prefer_existing defaults False → R2V video path is unchanged.
        await VideoGenerationMixin._attach_references(self._attach_page(), [ref], out_dir=None)

        select.assert_not_awaited()
        upload.assert_awaited_once()


class TestVideoUiModePolicy:
    """#299 PR-A: the video path binds its driver through ``get_ui_driver`` so
    the mode policy (pre-submit exit-28 fail-fast, $0) covers video like it
    covers images. No agentic video driver exists, so every request clamps to
    classic-required; an env-sourced ``agentic`` degrades with a warning."""

    def _transport(self, monkeypatch: pytest.MonkeyPatch) -> UiAutomationTransport:
        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(transport, "_enter_editor", AsyncMock())
        monkeypatch.setattr(transport, "_send_prompt", AsyncMock())
        _stub_video_helpers(
            monkeypatch,
            generate_resp={"status": 200, "url": _T2V_URL, "body": {"media": [{"name": "vid-1"}]}},
        )
        monkeypatch.setattr(
            VideoGenerationMixin,
            "_poll_video_status",
            AsyncMock(
                return_value=VideoStatus(
                    media_id="vid-1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"
                )
            ),
        )
        monkeypatch.setattr(transport, "_download_video", AsyncMock(return_value=Path("v.mp4")))
        return transport

    @pytest.mark.asyncio
    async def test_binds_classic_via_policy_after_editor_mount(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.transports.drivers import factory
        from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver
        from gflow_cli.config import UiMode

        transport = self._transport(monkeypatch)
        order: list[str] = []
        bind_modes: list[object] = []

        async def _enter(*_a: object, **_k: object) -> None:
            order.append("enter_editor")

        monkeypatch.setattr(transport, "_enter_editor", _enter)

        async def _overlays(*_a: object, **_k: object) -> None:
            order.append("dismiss_overlays")

        monkeypatch.setattr(transport, "_dismiss_blocking_overlays", _overlays)

        async def _bind(page, *, ui_mode, transport):  # type: ignore[no-untyped-def]  # noqa: ARG001
            order.append("bind")
            bind_modes.append(ui_mode)
            return ClassicFlowUiDriver(transport=transport)

        monkeypatch.setattr(factory, "get_ui_driver", _bind)

        await transport.generate_video(request=GenerateVideoRequest(prompt="x"), download=False)
        # The bind probes the DOM, so it must happen after the editor mounts
        # AND after overlay dismissal (predict condition — an overlay on top of
        # the composer would make the cohort probe misread). Overlays are then
        # re-dismissed AFTER the bind: the classic recovery's sanctioned reload
        # can re-mount the #26 overlay (code-review finding).
        assert order == ["enter_editor", "dismiss_overlays", "bind", "dismiss_overlays"]
        assert bind_modes == [UiMode.CLASSIC]

    @pytest.mark.asyncio
    async def test_unreachable_arm_aborts_before_submit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gflow_cli.api.transports.drivers import factory
        from gflow_cli.config import UiMode
        from gflow_cli.errors import UiModeUnavailableError

        transport = self._transport(monkeypatch)
        monkeypatch.setattr(
            factory,
            "get_ui_driver",
            AsyncMock(side_effect=UiModeUnavailableError(UiMode.CLASSIC)),
        )
        with pytest.raises(UiModeUnavailableError):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"), download=False)
        # Pre-submit abort: neither the prompt nor any settings/attach step ran.
        cast(AsyncMock, transport._send_prompt).assert_not_awaited()
        cast(AsyncMock, VideoGenerationMixin._select_video_model).assert_not_awaited()
        cast(AsyncMock, VideoGenerationMixin._attach_frame).assert_not_awaited()

    @pytest.mark.asyncio
    async def test_env_agentic_clamps_to_classic_with_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        install_log_capture: structlog.testing.LogCapture,
    ) -> None:
        import gflow_cli.config as config_mod
        from gflow_cli.api.transports.drivers import factory
        from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver
        from gflow_cli.config import UiMode

        transport = self._transport(monkeypatch)
        monkeypatch.setattr(config_mod, "resolve_ui_mode", lambda _cli: UiMode.AGENTIC)
        bind_modes: list[object] = []

        async def _bind(page, *, ui_mode, transport):  # type: ignore[no-untyped-def]  # noqa: ARG001
            bind_modes.append(ui_mode)
            return ClassicFlowUiDriver(transport=transport)

        monkeypatch.setattr(factory, "get_ui_driver", _bind)

        await transport.generate_video(request=GenerateVideoRequest(prompt="x"), download=False)
        assert bind_modes == [UiMode.CLASSIC]
        events = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.ui_mode_agentic_clamped"
        ]
        assert len(events) == 1
        assert events[0]["requested"] == "agentic"

    @pytest.mark.asyncio
    async def test_request_classic_threads_without_env_resolve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import gflow_cli.config as config_mod
        from gflow_cli.api.transports.drivers import factory
        from gflow_cli.api.transports.drivers.classic import ClassicFlowUiDriver
        from gflow_cli.config import UiMode

        transport = self._transport(monkeypatch)

        def _explode(_cli: object) -> object:
            raise AssertionError(
                "resolve_ui_mode must not be consulted when the DTO carries a mode"
            )

        monkeypatch.setattr(config_mod, "resolve_ui_mode", _explode)
        bind_modes: list[object] = []

        async def _bind(page, *, ui_mode, transport):  # type: ignore[no-untyped-def]  # noqa: ARG001
            bind_modes.append(ui_mode)
            return ClassicFlowUiDriver(transport=transport)

        monkeypatch.setattr(factory, "get_ui_driver", _bind)

        req = GenerateVideoRequest(prompt="x", ui_mode=UiMode.CLASSIC)
        await transport.generate_video(request=req, download=False)
        assert bind_modes == [UiMode.CLASSIC]


class TestSelectExistingAssetNameResolver:
    """#546 rename self-healing — RED contract for the refresh-on-miss seam.

    Pinned seam (this class DEFINES it; not implemented yet):

    * ``_select_existing_asset`` gains a keyword-only
      ``name_resolver: Callable[[str], str | None] | None = None``. It is a
      SYNC callable, called with the media UUID, returning the CURRENT Flow
      display name (or ``None`` when it cannot resolve one).
    * On a picker-search miss with a resolver present, the search is retried
      EXACTLY ONCE with the resolver's fresh name, then the existing fallback
      chain proceeds unchanged. Cached name = optimization; listing = truth;
      UUID = identity (the tile assertion stays ``img[src*=<uuid>]``).
    * The transport never imports data/ — write-through of the fresh name
      happens INSIDE the CLI-provided callback, so no return channel exists
      here beyond the returned string.
    * Both ``_select_existing_asset`` callers — ``_attach_image_uuid_refs``
      (image-ref loop) and ``_attach_frame_by_media_id`` (i2v remote frame
      path) — gain the same optional keyword-only ``name_resolver`` kwarg and
      thread it through verbatim.
    """

    _UUID = "5a80906f-31cc-4a87-9782-95f14bb165ce"

    @pytest.mark.asyncio
    async def test_first_search_hit_never_calls_resolver(self) -> None:
        """Contract 1: happy path pays zero extra work — a first-search hit
        must not touch the resolver at all."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=None, count_side_effect=1
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        resolver = MagicMock(return_value="Fresh name")

        result = await VideoGenerationMixin._select_existing_asset(
            page, self._UUID, "Cached name", out_dir=None, name_resolver=resolver
        )

        assert result is True
        resolver.assert_not_called()
        tile.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_miss_with_fresh_name_retries_once_and_attaches(self) -> None:
        """Contract 2: miss + a DIFFERENT resolved name -> exactly one retry
        with the fresh name; the retried hit attaches via the picker (no
        upload fallback), and the resolver ran exactly once with the UUID."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=[TimeoutError("stale name filtered it out"), None],
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        search = page.locator(PICKER_SEARCH_INPUT)
        resolver = MagicMock(return_value="Fresh renamed caption")

        result = await VideoGenerationMixin._select_existing_asset(
            page, self._UUID, "Stale cached caption", out_dir=None, name_resolver=resolver
        )

        assert result is True
        resolver.assert_called_once_with(self._UUID)
        assert [c.args[0] for c in search.press_sequentially.await_args_list] == [
            "Stale cached caption",
            "Fresh renamed caption",
        ]
        tile.click.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("resolved", [None, "Stale cached caption"])
    async def test_miss_with_unhelpful_resolution_does_not_retry(
        self, resolved: str | None
    ) -> None:
        """Contract 3: resolver returning ``None`` or the SAME stale name is
        pinned as NO retry — the search runs once and the existing fallback
        chain proceeds unchanged."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=TimeoutError("never visible"),
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        search = page.locator(PICKER_SEARCH_INPUT)
        resolver = MagicMock(return_value=resolved)

        result = await VideoGenerationMixin._select_existing_asset(
            page, self._UUID, "Stale cached caption", out_dir=None, name_resolver=resolver
        )

        assert result is False
        resolver.assert_called_once_with(self._UUID)
        assert [c.args[0] for c in search.press_sequentially.await_args_list] == [
            "Stale cached caption"
        ]
        tile.click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_miss_then_retry_also_misses(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """Contract 2b (mis-loop guard): miss + a DIFFERENT resolved name whose
        retry ALSO misses -> resolver called exactly once, exactly two searches
        (never a loop), terminal ``False`` with the miss diagnostics fired so
        the fallback chain proceeds."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=TimeoutError("never visible"),
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        search = page.locator(PICKER_SEARCH_INPUT)
        resolver = MagicMock(return_value="Fresh renamed caption")

        result = await VideoGenerationMixin._select_existing_asset(
            page, self._UUID, "Stale cached caption", out_dir=None, name_resolver=resolver
        )

        assert result is False
        resolver.assert_called_once_with(self._UUID)
        assert [c.args[0] for c in search.press_sequentially.await_args_list] == [
            "Stale cached caption",
            "Fresh renamed caption",
        ]
        tile.click.assert_not_awaited()
        misses = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.existing_asset_not_found"
        ]
        assert len(misses) == 1, "miss diagnostics must fire exactly once on final miss"

    @pytest.mark.asyncio
    async def test_resolver_exception_is_swallowed_with_warning(
        self, install_log_capture: structlog.testing.LogCapture
    ) -> None:
        """Contract 4: a raising resolver must never crash the generation —
        the exception is swallowed with a structlog warning and the fallback
        chain proceeds (miss result unchanged)."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=TimeoutError("never visible"),
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        search = page.locator(PICKER_SEARCH_INPUT)
        resolver = MagicMock(side_effect=RuntimeError("listing fetch blew up"))

        result = await VideoGenerationMixin._select_existing_asset(
            page, self._UUID, "Stale cached caption", out_dir=None, name_resolver=resolver
        )

        assert result is False
        resolver.assert_called_once_with(self._UUID)
        assert [c.args[0] for c in search.press_sequentially.await_args_list] == [
            "Stale cached caption"
        ]
        warnings = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.name_resolver_failed"
        ]
        assert warnings, "resolver failure must be logged as a warning"
        assert warnings[0]["log_level"] == "warning"
        assert warnings[0]["media_id"] == self._UUID

    @pytest.mark.asyncio
    async def test_no_resolver_keeps_todays_miss_behavior(self) -> None:
        """Contract 5 (regression guard, green today): with no resolver the
        behavior is identical to the pre-#546 miss — one search, no retry,
        terminal ``False``. Companion to ``TestSelectExistingAssetLargeGrid.
        test_display_name_miss_does_not_scroll_or_search_uuid``."""
        tile = TestSelectExistingAssetPickerScroll._tile_mock(
            wait_for_side_effect=TimeoutError("never visible"),
            count_side_effect=0,
        )
        page = TestSelectExistingAssetPickerScroll._page_with_tile(tile)
        search = page.locator(PICKER_SEARCH_INPUT)

        result = await VideoGenerationMixin._select_existing_asset(
            page, self._UUID, "Stale cached caption", out_dir=None
        )

        assert result is False
        assert [c.args[0] for c in search.press_sequentially.await_args_list] == [
            "Stale cached caption"
        ]
        tile.click.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_attach_image_uuid_refs_threads_resolver_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Caller seam: the image-ref loop accepts ``name_resolver`` and hands
        it verbatim to ``_select_existing_asset`` for every ref."""
        helper = TestAttachImageUuidRefsPickerScroll()
        page, _, _, _ = helper._make_page({self._UUID: helper._never_found_tile()})
        select = AsyncMock(return_value=True)
        monkeypatch.setattr(VideoGenerationMixin, "_select_existing_asset", select)
        resolver = MagicMock(return_value=None)

        await VideoGenerationMixin._attach_image_uuid_refs(
            page,
            [(self._UUID, "Cabin", "", "")],
            out_dir=None,
            name_resolver=resolver,
        )

        assert select.await_args.kwargs["name_resolver"] is resolver

    @pytest.mark.asyncio
    async def test_attach_frame_by_media_id_threads_resolver_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Caller seam: the i2v frame path accepts ``name_resolver`` and hands
        it verbatim to ``_select_existing_asset``."""
        slot = MagicMock()
        slot.click = AsyncMock()
        monkeypatch.setattr(
            VideoGenerationMixin, "_resolve_frame_slot", AsyncMock(return_value=slot)
        )
        select = AsyncMock(return_value=True)
        monkeypatch.setattr(VideoGenerationMixin, "_select_existing_asset", select)
        page = _frame_dialog_page()
        resolver = MagicMock(return_value=None)

        await VideoGenerationMixin._attach_frame_by_media_id(
            page,
            0,
            "Start",
            _FRAME_REF_UUID,
            _FRAME_DISPLAY_NAME,
            out_dir=None,
            name_resolver=resolver,
        )

        assert select.await_args.kwargs["name_resolver"] is resolver
