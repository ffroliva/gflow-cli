"""Tests for `FlowApiClient.poll_video_status` — the outbound video poller.

This poller had to be written from scratch. `routes.CHECK_VIDEO_STATUS` has
existed as a constant with **zero consumers**: production T2V/I2V rides
`ui_automation_video.py`, whose poller passively scans *Flow's own* captured
status traffic and so assumes the SPA is on-screen polling on our behalf. A
direct-wire submit (the extend route) gives Flow's UI no reason to poll our
media id, so that poller would sit until its deadline having seen nothing.

`_post_json` is monkey-patched, and `asyncio.sleep` is stubbed, so these run
instantly with no Playwright and no network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import ContentPolicyError, FlowApiError, TransportTimeoutError

if TYPE_CHECKING:
    from pathlib import Path

MEDIA = "37930141-ee54-4fe2-9f60-9eb959ca11ff"
PROJECT = "7d3d6bd9-a39f-4c2d-b772-146e73e539cf"


def _status(state: str, *, reasons: list[str] | None = None) -> dict[str, Any]:
    """One batchCheckAsyncVideoGenerationStatus response, captured shape."""
    media_status: dict[str, Any] = {"mediaGenerationStatus": state}
    if reasons:
        media_status["failureReasons"] = reasons
    return {"media": [{"name": MEDIA, "mediaMetadata": {"mediaStatus": media_status}}]}


def _client(tmp_path: Path, responses: list[Any]) -> FlowApiClient:
    c = FlowApiClient(profile_dir=tmp_path / "prof")
    c._post_json = AsyncMock(side_effect=responses)  # type: ignore[method-assign]
    return c


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record sleep durations instead of serving them — a 110s job would
    otherwise make this suite unusable."""
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("gflow_cli.api.client.asyncio.sleep", _fake_sleep)
    return slept


@pytest.mark.asyncio
async def test_returns_on_terminal_success(tmp_path: Path) -> None:
    c = _client(tmp_path, [_status("MEDIA_GENERATION_STATUS_SUCCESSFUL")])
    status = await c.poll_video_status(MEDIA, project_id=PROJECT)
    assert status.succeeded
    assert status.media_id == MEDIA


@pytest.mark.asyncio
async def test_polls_until_terminal(tmp_path: Path) -> None:
    c = _client(
        tmp_path,
        [
            _status("MEDIA_GENERATION_STATUS_SCHEDULED"),
            _status("MEDIA_GENERATION_STATUS_ACTIVE"),
            _status("MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        ],
    )
    status = await c.poll_video_status(MEDIA, project_id=PROJECT)
    assert status.succeeded
    assert c._post_json.await_count == 3  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_raises_on_terminal_failure(tmp_path: Path) -> None:
    """A failed segment has still been billed — it must never be mistaken for
    'still running' or silently returned as a success-shaped object."""
    c = _client(tmp_path, [_status("MEDIA_GENERATION_STATUS_FAILED", reasons=["SOME_REASON"])])
    with pytest.raises(FlowApiError, match="SOME_REASON"):
        await c.poll_video_status(MEDIA, project_id=PROJECT)


@pytest.mark.asyncio
async def test_content_safety_failure_maps_to_content_policy(tmp_path: Path) -> None:
    """A safety rejection surfacing as a terminal FAILED must land on the
    existing taxonomy, not a generic API error — the remediation differs."""
    c = _client(
        tmp_path,
        [_status("MEDIA_GENERATION_STATUS_FAILED", reasons=["PUBLIC_ERROR_UNSAFE_GENERATION"])],
    )
    with pytest.raises(ContentPolicyError):
        await c.poll_video_status(MEDIA, project_id=PROJECT)


@pytest.mark.asyncio
async def test_waits_before_the_first_poll(tmp_path: Path, _no_real_sleep: list[float]) -> None:
    """`veo_3_1_extension_lite` takes ~110s. Polling immediately just burns
    requests against a WAF-scored host for a job that cannot be done yet."""
    c = _client(tmp_path, [_status("MEDIA_GENERATION_STATUS_SUCCESSFUL")])
    await c.poll_video_status(MEDIA, project_id=PROJECT, initial_delay_s=90.0)
    assert _no_real_sleep[0] == 90.0


@pytest.mark.asyncio
async def test_poll_interval_has_a_floor(tmp_path: Path, _no_real_sleep: list[float]) -> None:
    """A 2s interval would fire ~825 requests over a 15-segment run instead of
    ~75. The floor is enforced in code, not left to callers."""
    c = _client(
        tmp_path,
        [
            _status("MEDIA_GENERATION_STATUS_ACTIVE"),
            _status("MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        ],
    )
    await c.poll_video_status(MEDIA, project_id=PROJECT, initial_delay_s=0.0, poll_interval=0.5)
    assert all(s >= 5.0 for s in _no_real_sleep if s > 0)


@pytest.mark.asyncio
async def test_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(tmp_path, [_status("MEDIA_GENERATION_STATUS_ACTIVE")] * 50)
    ticks = iter([0.0] + [1000.0] * 50)
    monkeypatch.setattr("gflow_cli.api.client.time.monotonic", lambda: next(ticks))
    with pytest.raises(TransportTimeoutError):
        await c.poll_video_status(MEDIA, project_id=PROJECT, initial_delay_s=0.0, timeout_s=60.0)


@pytest.mark.asyncio
async def test_sends_the_captured_request_shape(tmp_path: Path) -> None:
    c = _client(tmp_path, [_status("MEDIA_GENERATION_STATUS_SUCCESSFUL")])
    await c.poll_video_status(MEDIA, project_id=PROJECT)
    _args, kwargs = c._post_json.await_args  # type: ignore[attr-defined]
    url, body = _args[0], _args[1]
    assert url.endswith("video:batchCheckAsyncVideoGenerationStatus")
    assert body == {"media": [{"name": MEDIA, "projectId": PROJECT}]}
    assert kwargs["route_name"] == "batchCheckAsyncVideoGenerationStatus"
