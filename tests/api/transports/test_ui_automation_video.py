"""Unit tests for the video-generation mixin (ui_automation_video.py)."""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin


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


_STATUS_URL = (
    "https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus"
)


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
