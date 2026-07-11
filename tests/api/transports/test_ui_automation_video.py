"""Unit tests for the video-generation mixin (ui_automation_video.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import (
    ADD_MEDIA_BUTTON,
    DIALOG_ANY,
    FRAME_SLOT_BY_LABEL,
    FRAME_SLOTS_STRUCT,
    PICKER_CONTEXT_INCLUDE,
    PICKER_GRID_SCROLL_ATTEMPTS,
    PICKER_INCLUDE_BUTTON,
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
        return loc

    page.locator = MagicMock(side_effect=_locator)
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock()
    page.keyboard.press = AsyncMock()
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
    @pytest.mark.asyncio
    async def test_clicks_the_count_one_tab(self) -> None:

        sel = "[role='tab']:text-is('1x')"  # _set_output_count(1) probes the '1x' label
        page = _cascade_page({sel})
        await VideoGenerationMixin._set_output_count_one(page)
        page.locator.assert_any_call(sel)

    @pytest.mark.asyncio
    async def test_missing_count_tab_is_non_fatal(self) -> None:
        page = _cascade_page(set())
        await VideoGenerationMixin._set_output_count_one(page)  # must not raise


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
    async def test_missing_trigger_is_non_fatal(self) -> None:
        from gflow_cli.api.video import VideoModel

        page = _cascade_page(set())
        # must not raise
        await VideoGenerationMixin._select_video_model(page, VideoModel.OMNI_FLASH, out_dir=None)

    @pytest.mark.asyncio
    async def test_missing_option_escapes_to_recover(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod
        from gflow_cli.api.video import VideoModel

        # trigger visible but the option is not -> Escape closes the stray menu
        page = _cascade_page({mod.MODEL_PICKER_TRIGGER})
        await VideoGenerationMixin._select_video_model(page, VideoModel.OMNI_FLASH, out_dir=None)
        page.keyboard.press.assert_any_call("Escape")


class TestSelectVideoDuration:
    @pytest.mark.asyncio
    async def test_clicks_the_duration_tab(self) -> None:
        sel = "[role='tab']:text-is('6s')"
        page = _cascade_page({sel})
        await VideoGenerationMixin._select_video_duration(page, 6)
        page.locator.assert_any_call(sel)

    @pytest.mark.asyncio
    async def test_missing_duration_tab_is_non_fatal(self) -> None:
        page = _cascade_page(set())
        await VideoGenerationMixin._select_video_duration(page, 10)  # must not raise


class TestSetOutputCount:
    @pytest.mark.asyncio
    async def test_clicks_the_count_n_tab(self) -> None:
        sel = "[role='tab']:text-is('x3')"
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
    monkeypatch.setattr(VideoGenerationMixin, "_set_output_count_one", AsyncMock())
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


class TestGenerateVideoGuards:
    @pytest.mark.asyncio
    async def test_requires_setup(self) -> None:
        transport = UiAutomationTransport()
        with pytest.raises(RuntimeError, match="setup"):
            await transport.generate_video(request=GenerateVideoRequest(prompt="x"))

    @pytest.mark.asyncio
    async def test_i2v_with_omni_flash_raises_before_any_browser_call(
        self,
        monkeypatch: pytest.MonkeyPatch,
        install_log_capture: structlog.testing.LogCapture,
    ) -> None:
        """Issue #125: omni-flash + i2v must raise ModelModeIncompatibilityError
        BEFORE any DOM interaction, and emit `model_mode_rejected`."""
        from gflow_cli.api.video import VideoModel
        from gflow_cli.errors import ModelModeIncompatibilityError

        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        # If the guard fails to fire first, _enter_editor would run — make it
        # explode so a regression is caught loudly rather than silently passing.
        monkeypatch.setattr(
            transport,
            "_enter_editor",
            AsyncMock(side_effect=AssertionError("guard must fire before _enter_editor")),
        )
        req = GenerateVideoRequest(
            prompt="rise up",
            mode=Mode.I2V,
            model=VideoModel.OMNI_FLASH,
            start_image=Path("a.png"),
            end_image=Path("b.png"),
        )
        with pytest.raises(ModelModeIncompatibilityError, match="#125"):
            await transport.generate_video(request=req, download=False)

        events = [
            e
            for e in install_log_capture.entries
            if e["event"] == "ui_automation_video.model_mode_rejected"
        ]
        assert len(events) == 1
        evt = events[0]
        assert evt["model"] == "omni_flash"
        assert evt["mode"] == "I2V"
        assert evt["has_start_image"] is True
        assert evt["has_end_image"] is True
        assert evt["issue_ref"] == "#125"

    @pytest.mark.asyncio
    async def test_i2v_start_only_with_omni_flash_also_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """omni-flash is rejected for start-only i2v too (probe v5 evidence)."""
        from gflow_cli.api.video import VideoModel
        from gflow_cli.errors import ModelModeIncompatibilityError

        transport = UiAutomationTransport()
        transport._page = _mock_async_page()
        transport._setup_done = True
        monkeypatch.setattr(
            transport,
            "_enter_editor",
            AsyncMock(side_effect=AssertionError("guard must fire first")),
        )
        req = GenerateVideoRequest(
            prompt="rise up",
            mode=Mode.I2V,
            model=VideoModel.OMNI_FLASH,
            start_image=Path("a.png"),
        )
        with pytest.raises(ModelModeIncompatibilityError):
            await transport.generate_video(request=req, download=False)

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
        assert select_call.await_args.kwargs.get("required") is True
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

        loc.wait_for = _wait_for
        loc.click = _click
        return loc

    page.locator = MagicMock(side_effect=_locator)
    page.wait_for_timeout = AsyncMock()
    page.keyboard.press = AsyncMock()
    page.screenshot = AsyncMock()
    return page


class TestSelectVideoModelRequired:
    @pytest.mark.asyncio
    async def test_required_raises_when_option_never_found(self) -> None:
        """Issue #125 Layer 1: with required=True (i2v), a model-option miss is
        FATAL — raise rather than let Flow fall back to omni-flash -> T2V."""
        from gflow_cli.errors import VideoModelSelectionError

        page = _select_model_page(option_visible_on_attempt=None)
        with pytest.raises(VideoModelSelectionError, match="#125"):
            await VideoGenerationMixin._select_video_model(
                page, VideoModel.VEO_3_1_LITE, out_dir=None, required=True
            )

    @pytest.mark.asyncio
    async def test_not_required_warns_and_returns_on_miss(self) -> None:
        """required=False (t2v/r2v): a miss is non-fatal (Flow default applies)."""
        page = _select_model_page(option_visible_on_attempt=None)
        # Must NOT raise.
        await VideoGenerationMixin._select_video_model(
            page, VideoModel.VEO_3_1_LITE, out_dir=None, required=False
        )

    @pytest.mark.asyncio
    async def test_retries_trigger_click_and_succeeds_second_attempt(self) -> None:
        """Reliability: the first trigger click may not open the menu; the
        second attempt finds the option and selects it (no raise)."""
        page = _select_model_page(option_visible_on_attempt=2)
        await VideoGenerationMixin._select_video_model(
            page, VideoModel.VEO_3_1_LITE, out_dir=None, required=True
        )


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
        # role-based match: the raw name is passed as the accessible name,
        # never interpolated into a `:has-text('...')` CSS string.
        page.get_by_role.assert_called_once()
        args, kwargs = page.get_by_role.call_args
        assert args[0] == "option"
        assert kwargs.get("name") == "Wren's cabin"
        page.locator.assert_not_called()

    def test_matches_exactly_so_a_substring_name_cannot_attach_the_wrong_tile(self) -> None:
        # PR #245 review #4: without exact=True, get_by_role's default substring
        # match makes 'cabin' also select 'cabin at night' → .first attaches the
        # wrong image silently.
        page = MagicMock()
        VideoGenerationMixin._remote_option_tile(page, "cabin")
        _, kwargs = page.get_by_role.call_args
        assert kwargs.get("exact") is True


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
    """#282: the picker grid (react-virtuoso) is virtualised — a tile that
    isn't in the initial viewport (and isn't surfaced by the display-name
    search) may still exist just off-screen. `_select_existing_asset` must
    scroll and re-check between scrolls before giving up, the same way
    `_find_picker_entity_tile` does for the entity picker."""

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
    async def test_tile_visible_only_after_scrolling_is_selected(self) -> None:
        # Not visible in the initial viewport; count() reports absent for the
        # first 3 checks, then present on the 4th — the immediate re-check
        # right after the scroll loop breaks then succeeds.
        tile = self._tile_mock(
            wait_for_side_effect=[TimeoutError("not visible yet"), None],
            count_side_effect=[0, 0, 0, 1],
        )
        page = self._page_with_tile(tile)

        result = await VideoGenerationMixin._select_existing_asset(page, "uuid-1", "", out_dir=None)

        assert result is True, "tile found via scrolling must be selected"
        tile.click.assert_awaited_once()
        # 3 scrolls before the tile rendered into the DOM.
        assert page.mouse.wheel.await_count == 3

    @pytest.mark.asyncio
    async def test_tile_absent_after_exhausting_scrolls_returns_false(self) -> None:
        tile = self._tile_mock(
            wait_for_side_effect=TimeoutError("never visible"),
            count_side_effect=0,
        )
        page = self._page_with_tile(tile)

        result = await VideoGenerationMixin._select_existing_asset(page, "uuid-1", "", out_dir=None)

        assert result is False, "existing fallback behaviour must be unchanged"
        tile.click.assert_not_awaited()
        assert page.mouse.wheel.await_count == PICKER_GRID_SCROLL_ATTEMPTS

    @pytest.mark.asyncio
    async def test_failed_display_name_search_is_cleared_before_scrolling(self) -> None:
        """A FAILED display-name search leaves the picker grid filtered on
        that term; scrolling a still-filtered grid can never surface a tile
        that the filter excludes. `_select_existing_asset` must clear the
        search input before falling back to the scroll loop."""
        tile = self._tile_mock(
            wait_for_side_effect=[
                TimeoutError("not visible in initial viewport"),
                TimeoutError("not surfaced by display-name search"),
                None,
            ],
            count_side_effect=[0, 0, 1],
        )
        page = self._page_with_tile(tile)
        search = page.locator(PICKER_SEARCH_INPUT)

        result = await VideoGenerationMixin._select_existing_asset(
            page, "uuid-1", "Wren's cabin", out_dir=None
        )

        assert result is True, "tile found via scrolling after the search is cleared"
        search.press_sequentially.assert_awaited_once()
        search.fill.assert_awaited_once_with("")
        # 2 scrolls before the tile rendered into the DOM.
        assert page.mouse.wheel.await_count == 2


class TestAttachImageUuidRefsPickerScroll:
    """#282: every UUID ref after the first raised `TransportTimeoutError`
    because `_select_existing_asset` gave up before the virtualised grid had
    a chance to render the tile, and a leftover display-name search term from
    a prior ref could shadow the next ref's lookup."""

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
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tile = self._never_found_tile()
        page, _, _, _ = self._make_page({"uuid-1": tile})
        upload = AsyncMock()
        monkeypatch.setattr(VideoGenerationMixin, "_upload_via_open_dialog", upload)

        await VideoGenerationMixin._attach_image_uuid_refs(
            page, [("uuid-1", "Cabin", "/tmp/cabin.png")], out_dir=None
        )

        upload.assert_awaited_once()
        args, _ = upload.call_args
        assert args[1] == Path("/tmp/cabin.png")
        assert page.mouse.wheel.await_count == PICKER_GRID_SCROLL_ATTEMPTS

    @pytest.mark.asyncio
    async def test_tile_never_found_and_no_local_path_raises_same_message(self) -> None:
        tile = self._never_found_tile()
        page, _, _, _ = self._make_page({"uuid-1": tile})

        with pytest.raises(TransportTimeoutError) as excinfo:
            await VideoGenerationMixin._attach_image_uuid_refs(
                page, [("uuid-1", "Cabin", "")], out_dir=None
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
        tile_1.wait_for = AsyncMock(side_effect=[TimeoutError("not visible"), None])
        tile_1.count = AsyncMock(return_value=0)

        tile_2 = MagicMock()
        tile_2.first = tile_2
        tile_2.click = AsyncMock()
        tile_2.wait_for = AsyncMock()  # visible immediately, no search needed
        tile_2.count = AsyncMock(return_value=0)

        page, _, search, _ = self._make_page({"uuid-1": tile_1, "uuid-2": tile_2})

        await VideoGenerationMixin._attach_image_uuid_refs(
            page,
            [("uuid-1", "Cabin", ""), ("uuid-2", "Lighthouse", "")],
            out_dir=None,
        )

        # ref 1 needed the display-name search fallback...
        search.press_sequentially.assert_awaited_once()
        assert search.press_sequentially.call_args.args[0] == "Cabin"
        # ...but the search box must be cleared before EVERY ref's lookup
        # (#282: a leftover search term from ref 1 previously shadowed ref 2).
        assert search.fill.await_count == 2
        assert all(c.args == ("",) for c in search.fill.call_args_list)

    @pytest.mark.asyncio
    async def test_attach_refs_succeeds_when_picker_has_no_search_input(self) -> None:
        """A picker variant without a search box (#174: the full-page
        media-library drift) must not be a hard dependency for every ref.
        The clear-search-state fix for #282 must be presence-guarded: if the
        search input isn't in the DOM at all (`count() == 0`), skip clearing
        it rather than unconditionally `.fill("")`, which would otherwise
        wait out a full actionability timeout against a non-existent element
        before failing."""
        tile = MagicMock()
        tile.first = tile
        tile.click = AsyncMock()
        tile.wait_for = AsyncMock()  # visible immediately, no search needed
        tile.count = AsyncMock(return_value=0)

        no_search = MagicMock()
        no_search.first = no_search
        no_search.count = AsyncMock(return_value=0)
        no_search.fill = AsyncMock(
            side_effect=TimeoutError("search input not present in this picker variant")
        )
        no_search.press_sequentially = AsyncMock()

        page, _, search, _ = self._make_page({"uuid-1": tile}, search=no_search)

        # Must not raise: the tile is found immediately, so the only thing
        # that could break this ref is an unconditional search-box clear.
        await VideoGenerationMixin._attach_image_uuid_refs(
            page, [("uuid-1", "Cabin", "")], out_dir=None
        )

        tile.click.assert_awaited_once()
        search.fill.assert_not_awaited()


# ---------------------------------------------------------------------------
# #287: i2v frame slots accept an in-project asset UUID; upload rejections
# surface as a typed error instead of a bare RuntimeError.
# ---------------------------------------------------------------------------

_FRAME_REF_UUID = "d6f1927a-3eae-4626-bc90-9a6ea7637bab"


def _frame_dialog_page() -> MagicMock:
    """Page mock for the frame-slot media dialog: locator() yields a search
    input whose count/fill are awaitable (absent from _cascade_page's fake)."""
    page = MagicMock()
    loc = MagicMock()
    loc.first = loc
    loc.count = AsyncMock(return_value=1)
    loc.fill = AsyncMock()
    page.locator = MagicMock(return_value=loc)
    page.wait_for_timeout = AsyncMock()
    return page


class TestAttachFrameByMediaId:
    @pytest.mark.asyncio
    async def test_selects_existing_asset_in_the_frame_dialog(
        self, monkeypatch: pytest.MonkeyPatch
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
            page, 0, "Start", _FRAME_REF_UUID, out_dir=None
        )
        slot.click.assert_awaited_once()
        assert select.await_args.args[1] == _FRAME_REF_UUID

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
            VideoGenerationMixin, "_select_existing_asset", AsyncMock(return_value=False)
        )
        page = _frame_dialog_page()
        with pytest.raises(TransportTimeoutError, match=_FRAME_REF_UUID):
            await VideoGenerationMixin._attach_frame_by_media_id(
                page, 0, "Start", _FRAME_REF_UUID, out_dir=None
            )


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
        )
        page = _cascade_page(set())
        await VideoGenerationMixin._attach_i2v_frames(page, request, out_dir=None)
        assert by_id.await_count == 2
        local.assert_not_awaited()
        remote.assert_not_awaited()
        slots = [(c.args[1], c.args[2]) for c in by_id.await_args_list]
        assert slots == [(0, "Start"), (1, "End")]


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
