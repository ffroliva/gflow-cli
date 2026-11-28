"""Direct 1080p upscale (Task 15): ``p0UkFb``, the ``_upsampled`` poll, read and download.

Rows cite docs/superpowers/plans/2026-11-27-headless-direct-transport/SCENARIO.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from gflow_cli.api import flow_wire
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.api.submit_policy import BrowserVisibility
from gflow_cli.api.transports.batchexecute import UNUSUAL_ACTIVITY_REASON
from gflow_cli.api.transports.direct_http import DirectHttpTransport
from gflow_cli.config import get_settings
from gflow_cli.data.models import AssetKind, AssetRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import UpscaleUnavailableError, WireFormatError, legacy_exit_code_for
from tests.api.test_migrated_video_upscale import _mp4
from tests.api.transports.test_direct_http_video import (
    _PROJECT,
    _PROJECT_UUID,
    _WF,
    Flow,
    _error_frame,
    _frame,
    _page,
)

_SOURCE = "0e0b9575-b6d4-47a8-adc4-306560b8ee63"  # the clip being upscaled
_UP = f"{_WF}_upsampled"
_UP_MEDIA = "62bda194-68ee-4e1b-b305-be4c5e84a999"
_UP_URL = f"https://flow-content.google/video/{_UP}?Expires=1&Signature=s"
_MP4_1080 = _mp4((1920, 1080), (0, 0))
_PROFILE = "upscaler"


def _up_record(
    status: int, *, media: str | None = _UP_MEDIA, size: int | None = None, url: str | None = None
) -> list[Any]:
    details: list[Any] = [None] * 14
    details[8] = [status]
    details[13] = size
    return [_UP, _PROJECT_UUID if media else None, media, None, None, details, [url]]


def _done(rpcid: str, *, size: int, url: str | None = None) -> str:
    return _frame(rpcid, [[_up_record(3, size=size, url=url)]])


class UpscaleFlow(Flow):
    """``Flow`` that also keeps each POST's ``source-path``."""

    def __init__(self, replies: dict[str, list[str]], *, mp4: bytes = _MP4_1080) -> None:
        super().__init__(replies)
        self.source_paths: dict[str, list[str]] = {}
        self.download = lambda: httpx.Response(200, content=mp4)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            rpcid = request.url.params["rpcids"]
            self.source_paths.setdefault(rpcid, []).append(request.url.params["source-path"])
        return super().__call__(request)


def _happy(*, mp4: bytes = _MP4_1080, **over: list[str]) -> UpscaleFlow:
    replies = {
        "p0UkFb": [_frame("p0UkFb", [[_up_record(6)]])],
        "jwpduf": [_done("jwpduf", size=len(mp4))],
        "as29s": [_done("as29s", size=len(mp4), url=_UP_URL)],
    }
    return UpscaleFlow(replies | over, mp4=mp4)


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


def _catalog(
    *,
    model: str = "macaron_cookie_t2v_4s_360p",
    aspect: str | None = "landscape",
    workflow_id: str | None = _WF,
) -> None:
    """Record the source clip in the per-test catalog."""
    with DataStore.open(get_settings().resolved_db_path()) as store:
        repo = DataRepository(store)
        repo.upsert_profile(_PROFILE, Path(f"profile_{_PROFILE}"))
        repo.upsert_asset(
            AssetRecord(
                id="asset-1",
                profile_name=_PROFILE,
                flow_project_id=None,  # a projects row is not needed for the route
                flow_media_id=_SOURCE,
                flow_workflow_id=workflow_id,
                flow_media_generation_id=None,
                kind=AssetKind.VIDEO,
                status="ready",
                model=model,
                aspect_ratio=aspect,
                width=None,
                height=None,
                duration_seconds=4.0,
                seed=None,
                metadata_json={},
            )
        )


def _client(tmp_path: Path, flow: UpscaleFlow) -> tuple[FlowApiClient, AsyncMock]:
    profile_dir = tmp_path / f"profile_{_PROFILE}"
    profile_dir.mkdir()
    client = FlowApiClient(profile_dir, visibility=BrowserVisibility.HEADLESS)
    transport = DirectHttpTransport(http_transport=httpx.MockTransport(flow))
    transport._setup_done = True
    transport._profile_dir = profile_dir
    transport._page = _page()
    transport._poll_interval_s = 0.0
    client.transport = transport

    async def relaunch(visibility: BrowserVisibility) -> None:
        flow.events.append(f"relaunch:{visibility.value}")

    client.relaunch = relaunch  # type: ignore[method-assign]
    client._checkout_page = AsyncMock(return_value=_page())  # type: ignore[method-assign]
    client._checkin_page = lambda page: None  # type: ignore[method-assign]
    menu = AsyncMock(return_value=b"menu-bytes")
    return client, menu


async def _upscale(client: FlowApiClient, menu: AsyncMock, tmp_path: Path, **kw: Any) -> Path:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("gflow_cli.api.transports.migrated_video_upscale.upscale_video_migrated", menu)
        target = await client.upsample_video(
            media_id=_SOURCE,
            project_id=_PROJECT,
            scale=kw.get("scale", "1080p"),
            out_path=tmp_path / "out" / f"{_SOURCE}.mp4",
        )
    return Path(str(target))


@pytest.mark.asyncio
async def test_p0ukfb_frame_carries_workflow_and_aspect(tmp_path: Path) -> None:
    """Row 26: the 32-slot item names the source's workflow, aspect and media id."""
    _catalog()
    flow = _happy()
    client, menu = _client(tmp_path, flow)

    target = await _upscale(client, menu, tmp_path)

    payload = flow.bodies["p0UkFb"][0]
    item = payload[0][0]
    assert len(item) == 32
    assert item[0] == [None, _WF]
    assert item[2] == 2  # 16:9
    assert item[4][:4] == [None, _SOURCE, None, None]
    assert isinstance(item[4][4], str)
    assert item[4][4]
    assert item[6] == 2
    assert item[31] == "veo_3_1_upsampler_1080p"
    assert all(item[i] is None for i in (1, 3, *range(7, 31)))
    assert payload[1] == [
        None,
        22,
        None,
        None,
        None,
        _PROJECT,
        None,
        None,
        None,
        None,
        ["mock_token", 1],
    ]
    assert flow.source_paths["p0UkFb"] == [f"/project/{_PROJECT}/edit/{_SOURCE}"]
    assert target.read_bytes() == _MP4_1080
    menu.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsampled_suffix_found_by_exact_id(tmp_path: Path) -> None:
    """Row 26: a stub for ``<wf>_upsampled`` is not done; the poll waits for the media id,
    then reads ``as29s`` with the derived id and downloads the original mp4."""
    _catalog()
    stub = _frame("jwpduf", [[_up_record(4, media=None)]])
    flow = _happy(jwpduf=[stub, _done("jwpduf", size=len(_MP4_1080))])
    client, menu = _client(tmp_path, flow)

    await _upscale(client, menu, tmp_path)

    assert flow.events == ["p0UkFb", "jwpduf", "jwpduf", "as29s", "GET flow-content.google"]
    assert flow.bodies["jwpduf"][0] == [None, None, [[_UP]]]
    assert flow.bodies["as29s"] == [[_UP]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("catalogued", "scale", "aspect"),
    [
        (False, "1080p", "landscape"),  # not in the local catalog
        (True, "720p", "landscape"),  # only 1080p goes direct
        (True, "1080p", None),  # no aspect recorded
        (True, "1080p", "portrait"),  # 9:16 upscale is not measured
    ],
)
async def test_uncatalogued_source_uses_menu(
    tmp_path: Path, catalogued: bool, scale: str, aspect: str | None
) -> None:
    """Row 26: what the catalog cannot describe goes to the menu, with no direct attempt."""
    if catalogued:
        _catalog(aspect=aspect)
    flow = _happy()
    client, menu = _client(tmp_path, flow)

    target = await _upscale(client, menu, tmp_path, scale=scale)

    assert flow.events == []
    menu.assert_awaited_once()
    assert target.read_bytes() == b"menu-bytes"


@pytest.mark.asyncio
async def test_wrong_dimensions_raise_existing_error(tmp_path: Path) -> None:
    """A 640x360 mp4 is the original, not the export: the menu path's error, never resent."""
    _catalog()
    small = _mp4((640, 360))
    flow = _happy(mp4=small)
    client, menu = _client(tmp_path, flow)

    with pytest.raises(WireFormatError, match="640x360") as info:
        await _upscale(client, menu, tmp_path)

    assert flow.count("p0UkFb") == 1
    assert info.value.retryable is False
    menu.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replies",
    [
        {"p0UkFb": [_error_frame("p0UkFb", "PUBLIC_ERROR_UNSUPPORTED_ITEM")]},
        {"jwpduf": [_frame("jwpduf", [[[_UP, None, None, None, None, [None] * 8 + [[7]]]]])]},
    ],
    ids=["error_frame", "failed_record"],
)
async def test_flow_refusal_maps_to_exit_22(tmp_path: Path, replies: dict[str, list[str]]) -> None:
    """Flow refusing the item (an error frame, or a failed ``_upsampled`` record) is exit
    22, as the menu path's unlisted scale is (#1338); it is not sent again."""
    _catalog()
    flow = _happy(**replies)
    client, menu = _client(tmp_path, flow)

    with pytest.raises(UpscaleUnavailableError) as info:
        await _upscale(client, menu, tmp_path)

    assert legacy_exit_code_for(info.value) == 22
    assert info.value.retryable is False
    assert flow.count("p0UkFb") == 1
    menu.assert_not_awaited()


@pytest.mark.asyncio
async def test_unusual_activity_retries_once_through_the_loop(tmp_path: Path) -> None:
    """``UNUSUAL_ACTIVITY`` on ``p0UkFb`` goes through the retry loop like any submit."""
    _catalog()
    flow = _happy(
        p0UkFb=[
            _error_frame("p0UkFb", UNUSUAL_ACTIVITY_REASON),
            _frame("p0UkFb", [[_up_record(6)]]),
        ]
    )
    client, menu = _client(tmp_path, flow)

    await _upscale(client, menu, tmp_path)

    assert flow.events[:3] == ["p0UkFb", "relaunch:headless", "p0UkFb"]
    menu.assert_not_awaited()
