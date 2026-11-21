"""Unit tests for media trash and restore wire mutation (#1363).

Covers payload encoding, response frame validation, direct wire dispatch,
and FlowApiClient fallback execution.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from gflow_cli.api import flow_wire
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.flow_wire import WireFallbackReason, WireReadUnavailable
from gflow_cli.api.transports._media_trash_wire import (
    RESTORE_FIELD_MASKS,
    TRASH_FIELD_MASK,
    TRASH_RPC,
    build_restore_payload,
    build_trash_payload,
    parse_restore_response,
    parse_trash_response,
)
from gflow_cli.config import reset_settings
from gflow_cli.errors import WireFormatError

_FAKE_COOKIES = [("SID", "fake-sid"), ("OSID", "fake-osid")]
_FAKE_AT = "AFAKEATTOKEN:1700000000000"
_SHELL = (
    '<html><script>window.WIZ_global_data = {"SNlM0e":"'
    + _FAKE_AT
    + '","FdrFJe":"-123","cfb2h":"boq_labs-flow-frontend_20261110.00_p0"};</script></html>'
)

# Realistic pGCYOe reply frame echoing media record
_TRASH_REPLY_BODY = (
    ')]}\'\n\n120\n[["wrb.fr","pGCYOe","[[[\\"m-1\\",null,null,['
    '\\"meta\\",[1,1],true,null,\\"wf-1\\",\\"uuid-1\\",[1,1]],\\"proj-1\\"]]]",'
    'null,null,null,"generic"]]\n'
)

_RESTORE_REPLY_BODY = (
    ')]}\'\n\n120\n[["wrb.fr","pGCYOe","[[[\\"m-1\\",null,null,['
    '\\"meta\\",[1,1],null,null,\\"wf-1\\",\\"uuid-1\\",[1,1]],\\"proj-1\\"]]]",'
    'null,null,null,"generic"]]\n'
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def profile_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_wire: None) -> Path:
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "1")
    reset_settings()
    p_dir = tmp_path / "profiles" / "test_prof"
    p_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(flow_wire, "read_cookies_for", lambda _p: list(_FAKE_COOKIES))
    monkeypatch.setattr(flow_wire, "profile_last_version", lambda _p: "131.0.0.0")
    return p_dir


# ---------------------------------------------------------------------------
# Task 1 & 3: Payload encoding and response parsing
# ---------------------------------------------------------------------------


def test_build_trash_payload_matches_measured_shape() -> None:
    payload = build_trash_payload("media-123", "proj-456")
    expected = [
        [["media-123", None, None, [None, None, 1], "proj-456"]],
        [[TRASH_FIELD_MASK]],
    ]
    assert payload == expected
    assert TRASH_FIELD_MASK == "metadata.archived"
    assert len(TRASH_FIELD_MASK) == 17
    assert TRASH_RPC == "pGCYOe"


def test_build_restore_payload_matches_measured_shape() -> None:
    payload = build_restore_payload("media-123", "proj-456")
    expected = [
        [["media-123", "", None, [], "proj-456"]],
        [["metadata.archived", "collection_id"]],
    ]
    assert payload == expected
    assert RESTORE_FIELD_MASKS == ("metadata.archived", "collection_id")


def test_parse_trash_response_validates_trashed_state() -> None:
    frame = [[["media-123", None, None, ["meta", [1, 1], True, None, "wf-1", "u-1"], "proj-456"]]]
    parsed = parse_trash_response(frame, expected_media_id="media-123")
    assert parsed["media_id"] == "media-123"
    assert parsed["project_id"] == "proj-456"
    assert parsed["workflow_id"] == "wf-1"
    assert parsed["archived"] is True
    assert parsed["status"] == "trashed"


def test_parse_restore_response_validates_restored_state() -> None:
    frame = [[["media-123", None, None, ["meta", [1, 1], None, None, "wf-1", "u-1"], "proj-456"]]]
    parsed = parse_restore_response(frame, expected_media_id="media-123")
    assert parsed["media_id"] == "media-123"
    assert parsed["project_id"] == "proj-456"
    assert parsed["workflow_id"] == "wf-1"
    assert parsed["archived"] is False
    assert parsed["status"] == "restored"


def test_parse_trash_response_media_id_mismatch_raises() -> None:
    frame = [[["other-media", None, None, ["meta", [1, 1], True], "proj-456"]]]
    with pytest.raises(WireFormatError, match="media ID mismatch"):
        parse_trash_response(frame, expected_media_id="media-123")


def test_parse_trash_response_not_archived_raises() -> None:
    frame = [[["media-123", None, None, ["meta", [1, 1], None], "proj-456"]]]
    with pytest.raises(WireFormatError, match="did not set archived flag to True"):
        parse_trash_response(frame, expected_media_id="media-123")


def test_parse_restore_response_not_cleared_raises() -> None:
    frame = [[["media-123", None, None, ["meta", [1, 1], True], "proj-456"]]]
    with pytest.raises(WireFormatError, match="did not clear archived flag"):
        parse_restore_response(frame, expected_media_id="media-123")


def test_parse_invalid_frame_structure_raises() -> None:
    with pytest.raises(WireFormatError, match="Unexpected pGCYOe"):
        parse_trash_response([], expected_media_id="media-123")

    with pytest.raises(WireFormatError, match="Unexpected pGCYOe"):
        parse_restore_response([[]], expected_media_id="media-123")


# ---------------------------------------------------------------------------
# Task 3: Direct wire dispatch and fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_trash_media_success(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL)
        assert "pGCYOe" in str(request.url)
        return httpx.Response(200, text=_TRASH_REPLY_BODY)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**{**kw, "transport": transport}),
    )

    res = await flow_wire.direct_trash_media(profile_dir, "m-1", "proj-1")
    assert res["status"] == "trashed"
    assert res["media_id"] == "m-1"
    assert res["project_id"] == "proj-1"


@pytest.mark.asyncio
async def test_direct_restore_media_success(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL)
        assert "pGCYOe" in str(request.url)
        return httpx.Response(200, text=_RESTORE_REPLY_BODY)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**{**kw, "transport": transport}),
    )

    res = await flow_wire.direct_restore_media(profile_dir, "m-1", "proj-1")
    assert res["status"] == "restored"
    assert res["media_id"] == "m-1"
    assert res["project_id"] == "proj-1"


@pytest.mark.asyncio
async def test_direct_trash_media_fallback_on_stale_cookie(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(401)
        return httpx.Response(401)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**{**kw, "transport": transport}),
    )

    with pytest.raises(WireReadUnavailable) as exc_info:
        await flow_wire.direct_trash_media(profile_dir, "m-1", "proj-1")

    assert exc_info.value.reason == WireFallbackReason.HTTP_401


# ---------------------------------------------------------------------------
# Task 3: FlowApiClient page context evaluation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_trash_media_page_evaluation(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FlowApiClient(profile_dir=profile_dir, headless=True)
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={"status": 200, "text": _TRASH_REPLY_BODY})

    client._checkout_page = AsyncMock(return_value=fake_page)  # pyright: ignore[reportPrivateUsage]
    client._checkin_page = MagicMock()  # pyright: ignore[reportPrivateUsage]

    res = await client.trash_media("m-1", "proj-1")
    assert res["status"] == "trashed"
    assert res["media_id"] == "m-1"
    fake_page.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_restore_media_page_evaluation(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FlowApiClient(profile_dir=profile_dir, headless=True)
    fake_page = MagicMock()
    fake_page.evaluate = AsyncMock(return_value={"status": 200, "text": _RESTORE_REPLY_BODY})

    client._checkout_page = AsyncMock(return_value=fake_page)  # pyright: ignore[reportPrivateUsage]
    client._checkin_page = MagicMock()  # pyright: ignore[reportPrivateUsage]

    res = await client.restore_media("m-1", "proj-1")
    assert res["status"] == "restored"
    assert res["media_id"] == "m-1"
    fake_page.evaluate.assert_awaited_once()
