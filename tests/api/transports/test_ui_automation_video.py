"""Unit tests for the video-generation mixin (ui_automation_video.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoStatus


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
        with pytest.raises(RuntimeError, match="mode-switch"):
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)

    @pytest.mark.asyncio
    async def test_raises_when_video_tab_missing(self) -> None:
        from gflow_cli.api.transports import ui_automation_video as mod

        page = _cascade_page({mod.MODE_SWITCH_TRIGGER_SELECTORS[0]})
        with pytest.raises(RuntimeError, match="Video tab"):
            await VideoGenerationMixin._switch_to_video_mode(page, out_dir=None)


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
    async def test_i2v_routes_to_frames_and_attach(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        req = GenerateVideoRequest(prompt="x", mode=Mode.I2V, start_image=Path("a.png"))
        await transport.generate_video(request=req, download=False)
        VideoGenerationMixin._switch_video_sub_mode.assert_awaited()  # type: ignore[attr-defined]
        VideoGenerationMixin._attach_frame.assert_awaited()  # type: ignore[attr-defined]

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
