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
    FRAME_SLOT_BY_LABEL,
    FRAME_SLOTS_STRUCT,
    PICKER_CONTEXT_INCLUDE,
    PICKER_INCLUDE_BUTTON,
    VideoGenerationMixin,
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
        page.locator.return_value = loc
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
