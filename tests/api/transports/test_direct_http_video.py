"""Direct text-to-video and image-to-video (Task 14): captured frames, poll, read, download.

Rows cite docs/superpowers/plans/2026-11-27-headless-direct-transport/SCENARIO.md.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs

import httpx
import pytest

from gflow_cli.api import flow_wire
from gflow_cli.api._flow_retry import run_with_retry
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.api.submit_policy import (
    BrowserVisibility,
    DirectAttemptEnded,
    Indeterminate,
    NotSent,
    VisibilitySetting,
)
from gflow_cli.api.transports.batchexecute import UNUSUAL_ACTIVITY_REASON
from gflow_cli.api.transports.direct_http import DirectHttpTransport
from gflow_cli.api.transports.frame_builder import (
    build_i2v_body,
    build_t2v_body,
    centred_crop,
)
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel
from gflow_cli.errors import (
    InsufficientCreditsError,
    NetworkError,
    TransportTimeoutError,
    WireFormatError,
)
from tests.api.test_flow_retry import FakeHost
from tests.api.transports.test_direct_http import _maseq_reply
from tests.api.transports.test_direct_http_gateway import _SHELL_HTML

_PROJECT = "proj-123"
_WF = "b247b16d-3bd8-42bb-8551-fe0dbbcbf8cf"
_MEDIA = "0e0b9575-b6d4-47a8-adc4-306560b8ee63"
_PROJECT_UUID = "11111111-2222-3333-4444-555555555555"
_UPLOADED = "f9efcc22-495d-4c89-9b9b-f9dce015742b"
_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
ANY_CROP = [None, 0.352344, 1, 0.647656]  # a 1200x630 frame into 9:16
_VIDEO_URL = f"https://flow-content.google/video/{_WF}?Expires=1&Signature=s"
_OMNI_T2V = GenerateVideoRequest(
    prompt="a paper boat",
    model=VideoModel.OMNI_1_2_FLASH,
    duration=4,
    resolution="360p",
    aspect=Aspect.PORTRAIT,
)


# --- reply builders ------------------------------------------------------------------


def _frame(rpcid: str, payload: Any) -> str:
    row = [["wrb.fr", rpcid, json.dumps(payload), None, None, None, "generic"]]
    return f")]}}'\n{json.dumps(row)}"


def _error_frame(rpcid: str, reason: str) -> str:
    row = [["wrb.fr", rpcid, None, None, None, [3, None, [[reason]]], "generic"]]
    return f")]}}'\n{json.dumps(row)}"


def _record(
    status: int,
    *,
    media: str | None = _MEDIA,
    size: int | None = None,
    url: str | None = None,
) -> list[Any]:
    details: list[Any] = [None] * 14
    details[8] = [status]
    details[13] = size
    return [_WF, _PROJECT_UUID if media else None, media, None, None, details, [url]]


def _submitted() -> str:
    return _frame("YhhmEf", [[_record(6)]])


def _done(rpcid: str = "jwpduf", *, url: str | None = None) -> str:
    return _frame(rpcid, [[_record(3, size=len(_MP4), url=url)]])


def _png(tmp_path: Path, width: int = 1200, height: int = 630) -> Path:
    ihdr = struct.pack(">II", width, height) + bytes([8, 2, 0, 0, 0])
    chunk = b"IHDR" + ihdr
    data = struct.pack(">I", len(ihdr)) + chunk + struct.pack(">I", zlib.crc32(chunk))
    path = tmp_path / "start.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + data + b"\x00" * 32)
    return path


def _f_req(body: bytes) -> list[Any]:
    """The inner payload of a form-encoded batchexecute body."""
    outer = json.loads(parse_qs(body.decode())["f.req"][0])
    return json.loads(outer[0][0][1])


class Flow:
    """A scripted flow.google.com: shell GETs, batchexecute POSTs and the mp4 GET."""

    def __init__(self, replies: dict[str, list[str]]) -> None:
        self.replies = replies
        self.events: list[str] = []
        self.bodies: dict[str, list[list[Any]]] = {}
        self.download_headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.host == "flow.google.com":
            return httpx.Response(200, text=_SHELL_HTML)
        if request.method == "GET":
            self.events.append(f"GET {request.url.host}")
            self.download_headers.append(request.headers)
            return httpx.Response(200, content=_MP4)
        rpcid = request.url.params["rpcids"]
        self.events.append(rpcid)
        self.bodies.setdefault(rpcid, []).append(_f_req(request.content))
        queue = self.replies[rpcid]
        return httpx.Response(200, text=queue.pop(0) if len(queue) > 1 else queue[0])

    def count(self, rpcid: str) -> int:
        return self.events.count(rpcid)


def _page() -> MagicMock:
    page = MagicMock()
    page.url = f"https://flow.google.com/project/{_PROJECT}"
    page.goto = AsyncMock()
    page.close = AsyncMock()
    page.context = MagicMock()
    page.context.cookies = AsyncMock(
        return_value=[{"name": "SID", "value": "sid-1", "domain": ".google.com"}]
    )
    page.context.new_page = AsyncMock(side_effect=lambda: _page())
    return page


def _transport(tmp_path: Path, flow: Flow) -> DirectHttpTransport:
    t = DirectHttpTransport(http_transport=httpx.MockTransport(flow))
    t._setup_done = True
    t._profile_dir = tmp_path
    t._page = _page()
    t._poll_interval_s = 0.0
    t.park_deferred_page = AsyncMock()  # type: ignore[method-assign]
    return t


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> Any:
    flow_wire.reset_state()
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    monkeypatch.setattr(flow_wire, "read_credits", AsyncMock(return_value=CreditsInfo(100)))
    yield
    flow_wire.reset_state()


def _happy(**over: list[str]) -> Flow:
    replies = {
        "YhhmEf": [_submitted()],
        "eb1hJf": [_frame("eb1hJf", [[_record(6)]])],
        "maseQ": [_maseq_reply(_UPLOADED)],
        "jwpduf": [_done()],
        "as29s": [_done("as29s", url=_VIDEO_URL)],
    }
    return Flow(replies | over)


# --- frames --------------------------------------------------------------------------


def test_yhhmef_frame_matches_captured_default() -> None:
    body = build_t2v_body(
        prompt="a paper boat",
        wire_key="macaron_cookie_t2v_4s_360p",
        aspect_slot=1,
        duration=4,
        project_id=_PROJECT,
        recaptcha_token="TOKEN",
        at_token="AT",
        request_ids=("U1", "U2"),
        batch_id="B",
    )
    form = parse_qs(body)
    assert form["at"] == ["AT"]
    outer = json.loads(form["f.req"][0])
    assert outer[0][0][0] == "YhhmEf"
    assert outer[0][0][2:] == [None, "generic"]
    assert json.loads(outer[0][0][1]) == [
        [
            [
                [None, None, [[["a paper boat"]]]],
                "macaron_cookie_t2v_4s_360p",
                1,
                None,
                [None, None, None, None, "U1", "U2"],
                None,
                None,
                [4],
            ]
        ],
        [None, 22, None, None, None, _PROJECT, None, None, None, None, ["TOKEN", 1]],
        ["B", 1],
    ]


def test_eb1hjf_frame_matches_captured_default() -> None:
    crop = [None, 0.352344, 1, 0.647656]
    body = build_i2v_body(
        prompt="a paper boat",
        wire_key="veo_3_1_i2v_lite",
        aspect_slot=1,
        start_media_id=_UPLOADED,
        crop=crop,
        project_id=_PROJECT,
        recaptcha_token="TOKEN",
        at_token="AT",
        request_ids=("U1", "U2"),
        batch_id="B",
    )
    outer = json.loads(parse_qs(body)["f.req"][0])
    assert outer[0][0][0] == "eb1hJf"
    assert json.loads(outer[0][0][1]) == [
        [
            [
                [None, None, [[["a paper boat"]]]],
                "veo_3_1_i2v_lite",
                1,
                None,
                [None, _UPLOADED, None, None, None, crop],
                [None, None, None, None, "U1", "U2"],
            ]
        ],
        [None, 22, None, None, None, _PROJECT, None, None, None, None, ["TOKEN", 1]],
        ["B", 1],
    ]


def test_eb1hjf_crop_box_for_1200x630() -> None:
    assert centred_crop(1200, 630, aspect_slot=2) == [None, 0.033333, 1, 0.966667]
    assert centred_crop(1200, 630, aspect_slot=1) == [None, 0.352344, 1, 0.647656]
    assert centred_crop(630, 1200, aspect_slot=2) == [0.352344, None, 0.647656, 1]


# --- a direct t2v end to end, and its post-send failures -----------------------------


@pytest.mark.asyncio
async def test_direct_t2v_polls_reads_and_downloads_the_original_mp4(tmp_path: Path) -> None:
    flow = _happy()
    started: list[Any] = []
    result = await _transport(tmp_path, flow).generate_video(
        request=_OMNI_T2V, project_id=_PROJECT, out_dir=tmp_path, on_started=started.append
    )
    assert flow.events == ["YhhmEf", "jwpduf", "as29s", "GET flow-content.google"]
    assert flow.bodies["jwpduf"] == [[None, None, [[_WF]]]]
    assert flow.bodies["as29s"] == [[_WF]]  # the bare workflow id: the original mp4's form
    assert "cookie" not in flow.download_headers[0]
    assert result.status.succeeded
    assert result.transport == "direct_http"
    assert (result.workflow_id, result.project_id) == (_WF, _PROJECT)
    assert result.local_path is not None
    assert Path(result.local_path).read_bytes() == _MP4
    assert [s.media_id for s in started] == [_MEDIA]


@pytest.mark.asyncio
async def test_cookie_failure_during_jwpduf_poll_is_reconcile_one_send(tmp_path: Path) -> None:
    """Row 3: a cookie read that fails after the send is indeterminate; nothing is resent."""
    flow = _happy()
    transport = _transport(tmp_path, flow)
    good = [{"name": "SID", "value": "sid-1", "domain": ".google.com"}]

    async def cookies(*_: Any) -> list[dict[str, str]]:
        if "YhhmEf" in flow.events:
            raise RuntimeError("Target page, context or browser has been closed")
        return good

    transport._page.context.cookies = cookies  # type: ignore[union-attr]
    host = FakeHost()
    with pytest.raises(NetworkError) as caught:
        await run_with_retry(
            host,
            direct=lambda before: transport.generate_video(
                request=_OMNI_T2V, project_id=_PROJECT, out_dir=tmp_path, on_before_submit=before
            ),
            ui=AsyncMock(side_effect=AssertionError("the UI path must not run")),
            setting=VisibilitySetting.AUTO,
        )
    assert flow.count("YhhmEf") == 1
    assert host.relaunches == []
    assert (caught.value.charged, caught.value.resolved_next_action()) == ("maybe", "reconcile")


@pytest.mark.asyncio
async def test_jwpduf_status_4_without_media_is_not_done(tmp_path: Path) -> None:
    """Row 25: a stub (status 4, no media id) is not a finished or failed clip."""
    stub = _frame("jwpduf", [[_record(4, media=None)]])
    flow = _happy(jwpduf=[stub, stub, _done()])
    result = await _transport(tmp_path, flow).generate_video(
        request=_OMNI_T2V, project_id=_PROJECT, out_dir=tmp_path
    )
    assert flow.count("jwpduf") == 3
    assert result.status.succeeded

    flow = _happy(jwpduf=[stub])
    with pytest.raises(DirectAttemptEnded) as ended:
        await _transport(tmp_path, flow).generate_video(
            request=_OMNI_T2V, project_id=_PROJECT, out_dir=tmp_path, poll_timeout_s=0.0
        )
    assert isinstance(ended.value.outcome, Indeterminate)
    assert isinstance(ended.value.error, TransportTimeoutError)
    assert ended.value.error.retryable is False
    assert flow.count("YhhmEf") == 1
    assert flow.count("as29s") == 0


@pytest.mark.asyncio
async def test_as29s_without_video_url_raises_reconcile_without_resubmit(tmp_path: Path) -> None:
    """Row 25: the clip finished but the read names no mp4: reconcile, never a resubmit."""
    flow = _happy(as29s=[_done("as29s", url=None)])
    transport = _transport(tmp_path, flow)
    host = FakeHost()
    with pytest.raises(WireFormatError) as caught:
        await run_with_retry(
            host,
            direct=lambda before: transport.generate_video(
                request=_OMNI_T2V, project_id=_PROJECT, out_dir=tmp_path, on_before_submit=before
            ),
            ui=AsyncMock(side_effect=AssertionError("the UI path must not run")),
            setting=VisibilitySetting.AUTO,
        )
    assert flow.count("YhhmEf") == 1
    assert host.relaunches == []
    assert caught.value.retryable is False
    assert caught.value.resolved_next_action() == "reconcile"
    assert _MEDIA in (caught.value.remediation_hint or "")


@pytest.mark.asyncio
async def test_download_host_outside_allowlist_refused(tmp_path: Path) -> None:
    """Row 27: a download URL on a foreign host is refused before any request to it."""
    evil = f"https://evil.example/video/{_WF}?x=1"
    flow = _happy(as29s=[_done("as29s", url=evil)])
    with pytest.raises(DirectAttemptEnded) as ended:
        await _transport(tmp_path, flow).generate_video(
            request=_OMNI_T2V, project_id=_PROJECT, out_dir=tmp_path
        )
    assert isinstance(ended.value.error, WireFormatError)
    assert "evil.example" in ended.value.error.detail
    assert not any(e.startswith("GET") for e in flow.events)
    assert flow.count("YhhmEf") == 1


# --- the retry loop ------------------------------------------------------------------


def _client(tmp_path: Path, flow: Flow) -> tuple[FlowApiClient, list[str]]:
    client = FlowApiClient(tmp_path, visibility=BrowserVisibility.HEADLESS)
    client.transport = _transport(tmp_path, flow)

    async def relaunch(visibility: BrowserVisibility) -> None:
        flow.events.append(f"relaunch:{visibility.value}")

    client.relaunch = relaunch  # type: ignore[method-assign]
    checkpoints: list[str] = []
    return client, checkpoints


@pytest.mark.asyncio
async def test_video_refusal_retries_once_from_new_headless_process(tmp_path: Path) -> None:
    """Row 1: a refused YhhmEf is not billed; one relaunch, one more send, then the clip."""
    flow = _happy(YhhmEf=[_error_frame("YhhmEf", UNUSUAL_ACTIVITY_REASON), _submitted()])
    client, checkpoints = _client(tmp_path, flow)

    result = await client.generate_video(
        req=_OMNI_T2V,
        project_id=_PROJECT,
        out_dir=tmp_path,
        on_checkpoint=lambda c: checkpoints.append(c.phase),
    )

    assert flow.events == [
        "YhhmEf",
        "relaunch:headless",
        "YhhmEf",
        "jwpduf",
        "as29s",
        "GET flow-content.google",
    ]
    assert checkpoints == [
        "submit_attempted",
        "submit_refused",
        "submit_attempted",
        "remote_started",
    ]
    assert result.status.succeeded
    assert result.transport == "direct_http"


@pytest.mark.asyncio
async def test_i2v_retry_reuses_uploaded_media_id(tmp_path: Path) -> None:
    flow = _happy(
        eb1hJf=[
            _error_frame("eb1hJf", UNUSUAL_ACTIVITY_REASON),
            _frame("eb1hJf", [[_record(6)]]),
        ]
    )
    client, _ = _client(tmp_path, flow)
    request = GenerateVideoRequest(prompt="a paper boat", mode=Mode.I2V, start_image=_png(tmp_path))

    result = await client.generate_video(req=request, project_id=_PROJECT, out_dir=tmp_path)

    assert flow.events[:4] == ["maseQ", "eb1hJf", "relaunch:headless", "eb1hJf"]
    assert flow.count("maseQ") == 1
    items = [body[0][0] for body in flow.bodies["eb1hJf"]]
    assert all(item[1] == "veo_3_1_i2v_lite" and item[2] == 1 for item in items)
    assert [item[4] for item in items] == [[None, _UPLOADED, None, None, None, ANY_CROP]] * 2
    assert result.status.succeeded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_",
    [
        # t2v Veo 3.1 Lite was measured at 16:9 only
        GenerateVideoRequest(prompt="x", model=VideoModel.VEO_3_1_LITE, aspect=Aspect.PORTRAIT),
        # an end frame (nprQif) is unmeasured
        GenerateVideoRequest(
            prompt="x", mode=Mode.I2V, start_image=Path("a.png"), end_image=Path("b.png")
        ),
        # a bare t2v carries no model to key on
        GenerateVideoRequest(prompt="x"),
    ],
)
async def test_unmeasured_video_shape_raises_not_sent_route(
    tmp_path: Path, request_: GenerateVideoRequest
) -> None:
    """Rows 9, 10: only an exact measured key goes direct; nothing is sent otherwise."""
    flow = _happy()
    with pytest.raises(DirectAttemptEnded) as ended:
        await _transport(tmp_path, flow).generate_video(
            request=request_, project_id=_PROJECT, out_dir=tmp_path
        )
    assert ended.value.outcome == NotSent("unsupported_shape")
    assert flow.events == []


# --- the balance read before a direct submit -----------------------------------------


@pytest.mark.asyncio
async def test_zero_balance_raises_insufficient_credits_before_any_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow_wire, "read_credits", AsyncMock(return_value=CreditsInfo(0)))
    flow = _happy()
    client, _ = _client(tmp_path, flow)
    with pytest.raises(InsufficientCreditsError) as caught:
        await client.generate_video(req=_OMNI_T2V, project_id=_PROJECT, out_dir=tmp_path)
    assert caught.value.charged == "no"
    assert flow.events == []


@pytest.mark.asyncio
async def test_a_failed_balance_read_skips_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow_wire, "read_credits", AsyncMock(side_effect=OSError("no cookies")))
    flow = _happy()
    result = await _transport(tmp_path, flow).generate_video(
        request=_OMNI_T2V, project_id=_PROJECT, out_dir=tmp_path
    )
    assert result.status.succeeded
    assert flow.count("YhhmEf") == 1
