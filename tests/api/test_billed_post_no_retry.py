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
from playwright.async_api import Error as PwError
from playwright.async_api import TimeoutError as PwTimeout

import gflow_cli.api._flow_session as session_mod
from gflow_cli.api import routes
from gflow_cli.api._retry import MAX_ATTEMPTS, _make_retrying  # pyright: ignore[reportPrivateUsage]
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.errors import EXIT_CODE_MAP, NetworkError, RateLimitError

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
    """Answers each POST with the next status; the last one repeats."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.posted: list[str] = []

    async def post(self, url: str, **_: Any) -> _Resp:
        self.posted.append(url)
        return _Resp(self.statuses[min(len(self.posted), len(self.statuses)) - 1])


class _Page:
    def __init__(self, *statuses: int) -> None:
        self.request = _Request(*statuses)
        self.url = "https://labs.google/fx/tools/flow"


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *statuses: int) -> tuple[Any, _Page]:
    monkeypatch.setattr(
        session_mod,
        "post_with_retry",
        lambda **kw: _make_retrying(wait_seconds=lambda _: 0, **kw).__aiter__(),
    )
    page = _Page(*statuses)
    c: Any = FlowApiClient(profile_dir=tmp_path / "prof")
    c._ensure_access_token = AsyncMock(return_value="ya29.fake")
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


async def test_a_billed_post_is_sent_once_on_a_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, page = _client(tmp_path, monkeypatch, 429)
    with pytest.raises(RateLimitError):
        await c._post_json(
            routes.EXTEND_VIDEO,
            {"a": 1},
            route_name="batchAsyncGenerateVideoExtendVideo",
            billed=True,
        )
    assert len(page.request.posted) == 1


async def test_a_billed_post_resends_once_after_a_401_but_never_retries_the_5xx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The 401 resend is kept (refused at auth, nothing billed); the resend is billed too,
    # so its 5xx must not be retried with the same single-use token.
    c, page = _client(tmp_path, monkeypatch, 401, 503)
    with pytest.raises(NetworkError) as caught:
        await c._post_json(
            routes.EXTEND_VIDEO,
            {"a": 1},
            route_name="batchAsyncGenerateVideoExtendVideo",
            billed=True,
        )
    assert len(page.request.posted) == 2
    assert caught.value.next_action == "reconcile"


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


class _RaisingRequest(_Request):
    """Every POST raises a Playwright engine error after being counted as sent (#1274)."""

    def __init__(self, exc: BaseException) -> None:
        super().__init__()
        self.exc = exc

    async def post(self, url: str, **_: Any) -> _Resp:
        self.posted.append(url)
        raise self.exc


@pytest.mark.parametrize(
    "exc",
    [PwTimeout("Timeout 30000ms exceeded"), PwError("net::ERR_CONNECTION_RESET")],
    ids=["timeout", "reset"],
)
async def test_a_billed_post_engine_error_is_sent_once_and_asks_to_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    c, page = _client(tmp_path, monkeypatch)
    page.request = _RaisingRequest(exc)
    with pytest.raises(NetworkError) as caught:
        await c._post_json(routes.EXTEND_VIDEO, {"a": 1}, route_name="x", billed=True)
    assert len(page.request.posted) == 1
    assert _exit_code_for(caught.value) == 6  # not the catch-all exit 1
    assert caught.value.charged == "maybe"
    assert caught.value.next_action == "reconcile"
    assert caught.value.retryable is False
    # The cause's first line is kept; Playwright's multi-line call log is not.
    assert caught.value.detail == f"{type(exc).__name__} on a billed request ({exc}); not resent"
    hint = caught.value.to_problem_details()["remediation_hint"]
    assert "check the project in flow" in hint.lower()


async def test_a_billed_post_detail_keeps_only_the_first_line_of_the_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, page = _client(tmp_path, monkeypatch)
    page.request = _RaisingRequest(PwError("net::ERR_CONNECTION_RESET\nCall log:\n  - POST ..."))
    with pytest.raises(NetworkError) as caught:
        await c._post_json(routes.EXTEND_VIDEO, {"a": 1}, route_name="x", billed=True)
    assert caught.value.detail == (
        "Error on a billed request (net::ERR_CONNECTION_RESET); not resent"
    )


async def test_a_billed_token_fetch_timeout_posts_nothing_and_is_not_a_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing reached Flow, so "Flow may have accepted this" would be false.
    c, page = _client(tmp_path, monkeypatch)
    c._request_headers = AsyncMock(side_effect=PwTimeout("Timeout 30000ms exceeded"))
    with pytest.raises(PwTimeout):
        await c._post_json(routes.EXTEND_VIDEO, {"a": 1}, route_name="x", billed=True)
    assert page.request.posted == []


async def test_an_unbilled_post_engine_error_still_retries_and_surfaces_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, page = _client(tmp_path, monkeypatch)
    page.request = _RaisingRequest(PwTimeout("Timeout 30000ms exceeded"))
    with pytest.raises(PwTimeout):
        await c._post_json(routes.CHECK_VIDEO_STATUS, {"a": 1}, route_name="poll")
    assert len(page.request.posted) == MAX_ATTEMPTS


def _exit_code_for(err: Exception) -> int:
    """Mirror the CLI's EXIT_CODE_MAP isinstance walk."""
    return next((code for cls, code in EXIT_CODE_MAP.items() if isinstance(err, cls)), 1)
