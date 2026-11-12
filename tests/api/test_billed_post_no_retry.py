"""A billed POST is sent once, never replayed on a 5xx or 429 (#1260).

``extend_video`` and ``upsample_image`` carry a single-use reCAPTCHA token in their body.
A 5xx can arrive after Flow accepted the job, so replaying the same body either re-bills
or is refused for the spent token. #1010 already made the page-owned image submit single
shot; these are the credit-billed POSTs that go through ``_post_json``.

The tests drive the real ``_post_json`` against a fake page, so they see the requests Flow
would see, not a mock of the method.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import gflow_cli.api._flow_session as session_mod
from gflow_cli.api import routes
from gflow_cli.api._retry import MAX_ATTEMPTS, _make_retrying  # pyright: ignore[reportPrivateUsage]
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.errors import NetworkError

_M = "3f4272fd-2897-4621-b0cd-0fb9576758d3"
_P = "0b6d3c1e-5a4f-4c2b-9e7d-1a2b3c4d5e6f"
_S = "9c8b7a6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
_LISTING = Path(__file__).parent / "fixtures" / "project_initial_data_extend_models.json"


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.headers: dict[str, str] = {}

    async def text(self) -> str:
        return "{}"


class _Request:
    def __init__(self, status: int) -> None:
        self.status = status
        self.posted: list[str] = []

    async def post(self, url: str, **_: Any) -> _Resp:
        self.posted.append(url)
        return _Resp(self.status)


class _Page:
    def __init__(self, status: int) -> None:
        self.request = _Request(status)
        self.url = "https://labs.google/fx/tools/flow"


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int) -> tuple[Any, _Page]:
    monkeypatch.setattr(
        session_mod,
        "post_with_retry",
        lambda **kw: _make_retrying(wait_seconds=lambda _: 0, **kw).__aiter__(),
    )
    page = _Page(status)
    c: Any = FlowApiClient(profile_dir=tmp_path / "prof")
    c._checkout_page = AsyncMock(return_value=page)
    c._checkin_page = lambda _page: None
    c._request_headers = AsyncMock(return_value={})
    c._mint_recaptcha_token = AsyncMock(return_value="tok-single-use")
    c._page = page
    return c, page


async def test_a_billed_post_is_sent_once_on_a_5xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, page = _client(tmp_path, monkeypatch, 503)
    with pytest.raises(NetworkError) as caught:
        await c._post_json(routes.EXTEND_VIDEO, {"a": 1}, route_name="x", billed=True)
    assert len(page.request.posted) == 1
    assert caught.value.charged == "maybe"
    assert caught.value.next_action == "reconcile"  # check before re-running, never auto-retry
    assert caught.value.retryable is False
    hint = caught.value.to_problem_details()["remediation_hint"]
    assert "try again" not in hint.lower()  # the class default would invite a re-bill


async def test_an_unbilled_post_still_retries_a_5xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, page = _client(tmp_path, monkeypatch, 503)
    with pytest.raises(NetworkError):
        await c._post_json(routes.CHECK_VIDEO_STATUS, {"a": 1}, route_name="poll")
    assert len(page.request.posted) == MAX_ATTEMPTS


async def test_extend_video_is_sent_once_on_a_5xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    c, page = _client(tmp_path, monkeypatch, 503)
    c.fetch_project_listing = AsyncMock(return_value=json.loads(_LISTING.read_text("utf-8")))
    with pytest.raises(NetworkError):
        await c.extend_video(
            media_id=_M, project_id=_P, scene_id=_S, position=1, prompt="the wave recedes"
        )
    assert page.request.posted == [routes.EXTEND_VIDEO]


async def test_upsample_image_is_sent_once_on_a_5xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, page = _client(tmp_path, monkeypatch, 503)
    with pytest.raises(NetworkError):
        await c.upsample_image(
            media_id=_M,
            project_id=_P,
            target_resolution=TargetResolution.RES_2K,
            out_path=tmp_path / "out.png",
        )
    assert page.request.posted == [routes.UPSAMPLE_IMAGE]
