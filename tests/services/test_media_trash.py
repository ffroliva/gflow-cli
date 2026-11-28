"""Unit tests for media trash and restore wire mutation (#1363).

Covers payload encoding, response frame validation, direct wire dispatch,
and FlowApiClient fallback execution.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from gflow_cli.api import flow_wire
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.flow_wire import WireFallbackReason, WireReadUnavailable
from gflow_cli.api.submit_policy import BrowserVisibility
from gflow_cli.api.transports._media_trash_wire import (
    RESTORE_FIELD_MASKS,
    TRASH_FIELD_MASK,
    TRASH_RPC,
    build_restore_payload,
    build_trash_payload,
    parse_restore_response,
    parse_trash_response,
)
from gflow_cli.config import get_settings, reset_settings
from gflow_cli.data.models import AssetKind, AssetRecord, ProjectRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataStoreError, WireFormatError
from gflow_cli.services.media_trash import (
    MediaTrashResult,
    restore_media_service,
    trash_media_service,
)
from tests.e2e.test_media_trash_e2e import cleanup_e2e_media_trash

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


def test_parse_mutation_response_tolerant_to_integer_flags_and_trimmed_nulls() -> None:
    # Full trash response shape (from spike capture)
    frame_trash_full = [
        [
            [
                "media-123",
                None,
                None,
                ["meta", [1, 1], True, None, "wf-123", "u-123", [1, 1]],
                "proj-123",
            ]
        ]
    ]
    parsed_full = parse_trash_response(frame_trash_full, expected_media_id="media-123")
    assert parsed_full["media_id"] == "media-123"
    assert parsed_full["archived"] is True
    assert parsed_full["status"] == "trashed"
    assert parsed_full["project_id"] == "proj-123"
    assert parsed_full["workflow_id"] == "wf-123"

    # Trash response with integer 1 and trimmed trailing nulls
    frame_trash_trimmed = [[["media-123", None, None, ["meta", None, 1]]]]
    parsed_trash = parse_trash_response(frame_trash_trimmed, expected_media_id="media-123")
    assert parsed_trash["media_id"] == "media-123"
    assert parsed_trash["archived"] is True
    assert parsed_trash["project_id"] == ""
    assert parsed_trash["workflow_id"] == ""

    # Full restore response shape (from spike capture, null at [0][0][3][2])
    frame_restore_full = [
        [
            [
                "media-123",
                None,
                None,
                ["meta", [1, 1], None, None, "wf-123", "u-123", [1, 1]],
                "proj-123",
            ]
        ]
    ]
    parsed_restore_full = parse_restore_response(frame_restore_full, expected_media_id="media-123")
    assert parsed_restore_full["media_id"] == "media-123"
    assert parsed_restore_full["archived"] is False
    assert parsed_restore_full["status"] == "restored"

    # Restore response with trailing-null trimmed metadata (len 2, slot 2 missing)
    frame_restore_trimmed_meta = [[["media-123", None, None, ["meta", [1, 1]]]]]
    parsed_trimmed_meta = parse_restore_response(
        frame_restore_trimmed_meta, expected_media_id="media-123"
    )
    assert parsed_trimmed_meta["media_id"] == "media-123"
    assert parsed_trimmed_meta["archived"] is False

    # Restore response with empty metadata list
    frame_restore_empty_meta = [[["media-123", None, None, []]]]
    parsed_restore = parse_restore_response(frame_restore_empty_meta, expected_media_id="media-123")
    assert parsed_restore["media_id"] == "media-123"
    assert parsed_restore["archived"] is False

    # Restore response with item-level trailing-null trimming (slot 3 missing)
    frame_restore_trimmed_item = [[["media-123"]]]
    parsed_trimmed_item = parse_restore_response(
        frame_restore_trimmed_item, expected_media_id="media-123"
    )
    assert parsed_trimmed_item["media_id"] == "media-123"
    assert parsed_trimmed_item["archived"] is False

    # Trash response where archived is missing/None/False -> refused with typed error
    for invalid_meta in (
        ["meta", [1, 1], None],
        ["meta", [1, 1], False],
        ["meta", [1, 1]],  # trimmed before slot 2
        [],  # empty metadata
    ):
        with pytest.raises(WireFormatError, match="did not set archived flag to True") as exc_info:
            parse_trash_response(
                [[["media-123", None, None, invalid_meta]]], expected_media_id="media-123"
            )
        assert exc_info.value.charged == "no"

    # Trash response with item-level trimmed structure (no metadata slot)
    with pytest.raises(WireFormatError, match="did not set archived flag to True") as exc_info_item:
        parse_trash_response([[["media-123"]]], expected_media_id="media-123")
    assert exc_info_item.value.charged == "no"


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
    client = FlowApiClient(profile_dir=profile_dir, visibility=BrowserVisibility.HEADLESS)
    fake_page = MagicMock()
    fake_page.url = "https://flow.google.com"
    fake_page.goto = AsyncMock()

    async def mock_eval(script: Any, *args: Any) -> Any:
        if "at" in str(script) and "bl" in str(script) and "fsid" in str(script):
            return {"at": "token-1", "fsid": "1", "bl": "boq"}
        return {"status": 200, "text": _TRASH_REPLY_BODY}

    fake_page.evaluate = AsyncMock(side_effect=mock_eval)

    client._checkout_page = AsyncMock(return_value=fake_page)  # pyright: ignore[reportPrivateUsage]
    client._checkin_page = MagicMock()  # pyright: ignore[reportPrivateUsage]

    res = await client.trash_media("m-1", "proj-1")
    assert res["status"] == "trashed"
    assert res["media_id"] == "m-1"
    fake_page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_restore_media_page_evaluation(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FlowApiClient(profile_dir=profile_dir, visibility=BrowserVisibility.HEADLESS)
    fake_page = MagicMock()
    fake_page.url = "https://flow.google.com"
    fake_page.goto = AsyncMock()

    async def mock_eval(script: Any, *args: Any) -> Any:
        if "at" in str(script) and "bl" in str(script) and "fsid" in str(script):
            return {"at": "token-1", "fsid": "1", "bl": "boq"}
        return {"status": 200, "text": _RESTORE_REPLY_BODY}

    fake_page.evaluate = AsyncMock(side_effect=mock_eval)

    client._checkout_page = AsyncMock(return_value=fake_page)  # pyright: ignore[reportPrivateUsage]
    client._checkin_page = MagicMock()  # pyright: ignore[reportPrivateUsage]

    res = await client.restore_media("m-1", "proj-1")
    assert res["status"] == "restored"
    assert res["media_id"] == "m-1"
    fake_page.goto.assert_awaited_once()


@pytest.mark.asyncio
async def test_page_trash_media_http_error(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports._media_trash_wire import page_trash_media
    from gflow_cli.errors import FlowApiError

    fake_page = MagicMock()
    fake_page.url = "https://flow.google.com"
    fake_page.goto = AsyncMock()

    async def mock_eval(script: Any, *args: Any) -> Any:
        if "at" in str(script) and "bl" in str(script) and "fsid" in str(script):
            return {"at": "token-1", "fsid": "1", "bl": "boq"}
        return {"status": 500, "text": "Server Error"}

    fake_page.evaluate = AsyncMock(side_effect=mock_eval)

    with pytest.raises(FlowApiError) as exc_info:
        await page_trash_media(fake_page, "m-1", "proj-1")
    assert exc_info.value.status == 500
    assert "HTTP 500 from Flow page evaluation" in exc_info.value.detail
    assert exc_info.value.charged == "no"


@pytest.mark.asyncio
async def test_page_trash_media_rejection_separate_fields(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports._media_trash_wire import page_trash_media
    from gflow_cli.errors import FlowApiError

    fake_page = MagicMock()
    fake_page.url = "https://flow.google.com"
    fake_page.goto = AsyncMock()

    rejection_body = ')]}\'\n\n[["wrb.fr","pGCYOe",null,null,null,[7,null,["PermissionDenied"]]]]\n'

    async def mock_eval(script: Any, *args: Any) -> Any:
        if "at" in str(script) and "bl" in str(script) and "fsid" in str(script):
            return {"at": "token-1", "fsid": "1", "bl": "boq"}
        return {"status": 200, "text": rejection_body}

    fake_page.evaluate = AsyncMock(side_effect=mock_eval)

    with pytest.raises(FlowApiError) as exc_info:
        await page_trash_media(fake_page, "m-1", "proj-1")

    err = exc_info.value
    assert err.status == 200  # HTTP status
    assert getattr(err, "code", None) == 7  # gRPC code in separate field
    assert err.charged == "no"
    assert "PermissionDenied" not in err.detail  # redacted / no raw echo


@pytest.mark.asyncio
async def test_page_trash_media_empty_at_token_raises_auth_error(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports._media_trash_wire import page_trash_media
    from gflow_cli.errors import AuthExpiredError

    fake_page = MagicMock()
    fake_page.url = "https://flow.google.com"
    fake_page.goto = AsyncMock()

    async def mock_eval(script: Any, *args: Any) -> Any:
        # Empty 'at' token
        return {"at": "", "fsid": "1", "bl": "boq"}

    fake_page.evaluate = AsyncMock(side_effect=mock_eval)
    fake_page.content = AsyncMock(return_value="<html></html>")  # no SNlM0e either

    with pytest.raises(AuthExpiredError) as exc_info:
        await page_trash_media(fake_page, "m-1", "proj-1")
    assert exc_info.value.charged == "no"
    assert "remediation_hint" in dir(exc_info.value)


@pytest.mark.asyncio
async def test_trash_media_service_fallback_to_client_on_wire_unavailable(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    _seed_test_asset(
        db_path,
        asset_id="local-uuid-1",
        flow_media_id="m-flow-1",
        flow_project_id="p-flow-1",
        profile_name="test_prof",
    )

    # direct_trash_media raises WireReadUnavailable
    mock_direct = AsyncMock(side_effect=WireReadUnavailable(WireFallbackReason.HTTP_401))
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_direct)

    # client.trash_media is called
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.trash_media.return_value = {
        "media_id": "m-flow-1",
        "project_id": "p-flow-1",
        "status": "trashed",
        "archived": True,
    }
    monkeypatch.setattr(
        "gflow_cli.services.media_trash.FlowApiClient",
        MagicMock(return_value=mock_client),
    )

    result = await trash_media_service("local-uuid-1")
    assert result.status == "trashed"
    mock_direct.assert_awaited_once()
    # #1451: an image is sent by its workflow id, on the fallback path too.
    mock_client.trash_media.assert_awaited_once_with("wf-1", "p-flow-1")


@pytest.mark.asyncio
async def test_restore_media_service_fallback_to_client_on_wire_unavailable(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    _seed_test_asset(
        db_path,
        asset_id="local-uuid-1",
        flow_media_id="m-flow-1",
        flow_project_id="p-flow-1",
        profile_name="test_prof",
    )

    # direct_restore_media raises WireReadUnavailable
    mock_direct = AsyncMock(side_effect=WireReadUnavailable(WireFallbackReason.HTTP_401))
    monkeypatch.setattr(flow_wire, "direct_restore_media", mock_direct)

    # client.restore_media is called
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.restore_media.return_value = {
        "media_id": "m-flow-1",
        "project_id": "p-flow-1",
        "status": "restored",
        "archived": False,
    }
    monkeypatch.setattr(
        "gflow_cli.services.media_trash.FlowApiClient",
        MagicMock(return_value=mock_client),
    )

    result = await restore_media_service("local-uuid-1")
    assert result.status == "restored"
    mock_direct.assert_awaited_once()
    # #1451: an image is sent by its workflow id, on the fallback path too.
    mock_client.restore_media.assert_awaited_once_with("wf-1", "p-flow-1")


# ---------------------------------------------------------------------------
# Task 4: Service layer and catalog identifier resolution
# ---------------------------------------------------------------------------


def _seed_test_asset(
    db_path: Path,
    *,
    asset_id: str,
    flow_media_id: str,
    flow_project_id: str,
    profile_name: str = "default",
    flow_workflow_id: str | None = "wf-1",
    status: str = "ready",
    kind: AssetKind = AssetKind.IMAGE,
    created_at: str | None = None,
) -> None:
    now_str = created_at or datetime.now(UTC).isoformat()
    with DataStore.open(db_path) as store:
        repo = DataRepository(store)
        repo.upsert_profile(
            name=profile_name,
            profile_dir=Path(f"/home/{profile_name}/.config/gflow"),
        )
        repo.upsert_project(
            ProjectRecord(
                id=str(uuid.uuid4()),
                profile_name=profile_name,
                flow_project_id=flow_project_id,
                title="Test Project",
                source="cli",
                created_at=now_str,
            )
        )
        repo.upsert_asset(
            AssetRecord(
                id=asset_id,
                profile_name=profile_name,
                flow_project_id=flow_project_id,
                flow_media_id=flow_media_id,
                flow_workflow_id=flow_workflow_id,
                flow_media_generation_id=None,
                kind=kind,
                status=status,
                model="imagen-3.0-fast-generate-001",
                aspect_ratio="16:9",
                width=1280,
                height=720,
                duration_seconds=None,
                seed=1,
                metadata_json={},
                created_at=now_str,
            )
        )


@pytest.mark.asyncio
async def test_media_trash_service_resolves_local_id(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    _seed_test_asset(
        db_path,
        asset_id="local-uuid-1",
        flow_media_id="m-flow-1",
        flow_project_id="p-flow-1",
        profile_name="test_prof",
    )

    mock_trash = AsyncMock(
        return_value={
            "media_id": "m-flow-1",
            "project_id": "p-flow-1",
            "status": "trashed",
            "archived": True,
        }
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    result = await trash_media_service("local-uuid-1")
    assert isinstance(result, MediaTrashResult)
    assert result.media_id == "m-flow-1"
    assert result.project_id == "p-flow-1"
    assert result.status == "trashed"
    assert result.charged == "no"
    assert "gflow media restore m-flow-1" in (result.restore_hint or "")
    mock_trash.assert_awaited_once()


@pytest.mark.asyncio
async def test_media_trash_service_refuses_cross_profile_ambiguity(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    _seed_test_asset(
        db_path,
        asset_id="local-a",
        flow_media_id="m-shared",
        flow_project_id="p-a",
        profile_name="prof-a",
    )
    _seed_test_asset(
        db_path,
        asset_id="local-b",
        flow_media_id="m-shared",
        flow_project_id="p-b",
        profile_name="prof-b",
    )

    with pytest.raises(DataStoreError) as exc_info:
        await trash_media_service("m-shared", profile=None)

    assert "multiple profiles" in exc_info.value.detail
    assert "--profile" in exc_info.value.detail

    mock_trash = AsyncMock(
        return_value={
            "media_id": "m-shared",
            "project_id": "p-a",
            "status": "trashed",
            "archived": True,
        }
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    res = await trash_media_service("m-shared", profile="prof-a")
    assert res.profile_name == "prof-a"
    assert res.media_id == "m-shared"
    assert res.archived is True


@pytest.mark.asyncio
async def test_media_trash_service_leaves_catalog_status_unchanged(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    _seed_test_asset(
        db_path,
        asset_id="local-asset",
        flow_media_id="m-100",
        flow_project_id="p-100",
        profile_name="test_prof",
        status="ready",
    )

    mock_trash = AsyncMock(
        return_value={
            "media_id": "m-100",
            "project_id": "p-100",
            "status": "trashed",
            "archived": True,
        }
    )
    mock_restore = AsyncMock(
        return_value={
            "media_id": "m-100",
            "project_id": "p-100",
            "status": "restored",
            "archived": False,
        }
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)
    monkeypatch.setattr(flow_wire, "direct_restore_media", mock_restore)

    res_trash = await trash_media_service("m-100")
    assert res_trash.status == "trashed"

    with DataStore.open(db_path) as store:
        repo = DataRepository(store)
        row_trashed = repo.find_assets_by_any_id("m-100")[0]
        assert row_trashed.flow_media_id == "m-100"
        # Status in database must NOT be changed to 'trashed' (catalog status untouched)
        db_status = store.conn.execute(
            "SELECT status FROM assets WHERE flow_media_id = ?", ("m-100",)
        ).fetchone()
        assert db_status is not None
        assert db_status[0] == "ready"  # untouched

    res_restore = await restore_media_service("m-100")
    assert res_restore.status == "restored"

    with DataStore.open(db_path) as store:
        db_status = store.conn.execute(
            "SELECT status FROM assets WHERE flow_media_id = ?", ("m-100",)
        ).fetchone()
        assert db_status is not None
        assert db_status[0] == "ready"


@pytest.mark.asyncio
async def test_media_trash_service_refuses_empty_or_blank_id(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    for blank in ("", "   ", "\t\n"):
        with pytest.raises(DataStoreError) as exc_info:
            await trash_media_service(blank)
        assert "empty or blank" in exc_info.value.detail.lower()
        assert exc_info.value.charged == "no"


@pytest.mark.asyncio
async def test_media_trash_service_empty_id_does_not_match_blank_workflow_id(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    _seed_test_asset(
        db_path,
        asset_id="asset-with-empty-wf",
        flow_media_id="real-image-id",
        flow_project_id="p-100",
        flow_workflow_id="",
        profile_name="test_prof",
    )

    with pytest.raises(DataStoreError) as exc_info:
        await trash_media_service("")
    assert "empty or blank" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_media_trash_service_refuses_same_profile_ambiguity(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    _seed_test_asset(
        db_path,
        asset_id="local-1",
        flow_media_id="m-1",
        flow_project_id="p-1",
        flow_workflow_id="wf-shared",
        profile_name="test_prof",
    )
    _seed_test_asset(
        db_path,
        asset_id="local-2",
        flow_media_id="m-2",
        flow_project_id="p-1",
        flow_workflow_id="wf-shared",
        profile_name="test_prof",
    )

    with pytest.raises(DataStoreError) as exc_info:
        await trash_media_service("wf-shared", profile="test_prof")
    assert "multiple" in exc_info.value.detail.lower()
    assert "m-1" in exc_info.value.detail
    assert "m-2" in exc_info.value.detail


@pytest.mark.asyncio
async def test_media_trash_service_with_explicit_project_sends_an_uncataloged_id_as_given(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    mock_trash = AsyncMock(
        return_value={
            "media_id": "uncataloged-id",
            "project_id": "proj-999",
            "status": "trashed",
            "archived": True,
        }
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    # An id the catalog does not know is sent as given, under the --project project
    result = await trash_media_service("uncataloged-id", project_id="proj-999", profile="test_prof")
    assert result.media_id == "uncataloged-id"
    assert result.project_id == "proj-999"
    assert result.profile_name == "test_prof"
    expected_pdir = get_settings().profile_subdir("test_prof")
    mock_trash.assert_awaited_once_with(expected_pdir, "uncataloged-id", "proj-999")


@pytest.mark.asyncio
async def test_all_media_trash_errors_charge_no_and_have_correct_remediation(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    # 1. Uncataloged ID DataStoreError
    with pytest.raises(DataStoreError) as exc_info:
        await trash_media_service("nonexistent-id")
    err = exc_info.value
    assert err.charged == "no"
    hint = err.remediation_hint or ""
    assert "--project" in hint
    assert "media ID" in hint or "workflow UUID" in hint or "catalog ID" in hint
    assert "file permissions" not in hint.lower()

    # 2. WireFormatError
    with pytest.raises(WireFormatError) as exc_info2:
        parse_trash_response([], expected_media_id="m-1")
    assert exc_info2.value.charged == "no"


@pytest.mark.asyncio
async def test_direct_trash_flow_refusal_raises_flow_api_error_without_fallback(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rpc_error_body = ')]}\'\n\n[["wrb.fr","pGCYOe",null,null,null,[7,null,["PermissionDenied"]]]]\n'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL)
        return httpx.Response(200, text=rpc_error_body)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**{**kw, "transport": httpx.MockTransport(handler)}),
    )

    client_mock = MagicMock()
    monkeypatch.setattr("gflow_cli.services.media_trash.FlowApiClient", client_mock)

    from gflow_cli.errors import FlowApiError

    with pytest.raises(FlowApiError) as exc_info:
        await flow_wire.direct_trash_media(profile_dir, "m-1", "proj-1")

    err = exc_info.value
    assert getattr(err, "code", getattr(err, "status", None)) == 7
    assert err.charged == "no"
    client_mock.assert_not_called()


@pytest.mark.asyncio
async def test_direct_trash_429_has_own_route_and_does_not_throttle_credits(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL)
        return httpx.Response(429, headers={"Retry-After": "30"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(**{**kw, "transport": httpx.MockTransport(handler)}),
    )

    from gflow_cli.errors import RateLimitError

    with pytest.raises(RateLimitError) as exc_info:
        await flow_wire.direct_trash_media(profile_dir, "m-1", "proj-1")

    err = exc_info.value
    assert err.route != "credits_wire"
    assert "credits" not in (err.remediation_hint or "").lower()
    assert err.charged == "no"
    # Crucial: _throttled window must NOT be set on credits throttle
    assert profile_dir not in flow_wire._throttled


# ---------------------------------------------------------------------------
# Offline tests for e2e cleanup helper (#1363 Defect 1)
# ---------------------------------------------------------------------------


def test_cleanup_e2e_media_trash_call_shape_with_fake_service() -> None:
    calls: list[dict[str, Any]] = []

    async def fake_restore(
        target_id: str, *, project_id: str | None = None, profile: str | None = None
    ) -> Any:
        calls.append({"target_id": target_id, "project_id": project_id, "profile": profile})

    state = {
        "needs_restore": True,
        "media_id": "media-test-123",
        "project_id": "proj-test-456",
        "profile": "prof-test",
    }
    cleanup_e2e_media_trash(state, restore_fn=fake_restore)
    assert calls == [
        {
            "target_id": "media-test-123",
            "project_id": "proj-test-456",
            "profile": "prof-test",
        }
    ]


def test_cleanup_e2e_media_trash_signature_matches_restore_media_service() -> None:
    import inspect

    sig = inspect.signature(restore_media_service)
    bound = sig.bind(
        target_id="media-test-123",
        project_id="proj-test-456",
        profile="prof-test",
    )
    assert bound.arguments["target_id"] == "media-test-123"
    assert bound.arguments["project_id"] == "proj-test-456"
    assert bound.arguments["profile"] == "prof-test"


def test_cleanup_e2e_media_trash_loud_on_failure(capsys: pytest.CaptureFixture[str]) -> None:
    async def failing_restore(
        target_id: str, *, project_id: str | None = None, profile: str | None = None
    ) -> Any:
        raise RuntimeError("Network failure restoring media")

    state = {
        "needs_restore": True,
        "media_id": "loud-media-999",
        "project_id": "loud-proj-888",
        "profile": "loud-prof-777",
    }
    with pytest.raises(RuntimeError, match="Network failure restoring media"):
        cleanup_e2e_media_trash(state, restore_fn=failing_restore)

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "[E2E CLEANUP FAILURE]" in output
    assert "loud-media-999" in output
    assert (
        "gflow media restore loud-media-999 --project loud-proj-888 --profile loud-prof-777"
        in output
    )


# ---------------------------------------------------------------------------
# Offline tests for archived result field & refusal (#1363 Defect 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_trash_service_returns_archived_in_result_and_as_json(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    mock_trash = AsyncMock(
        return_value={
            "media_id": "m-arch-1",
            "project_id": "proj-arch-1",
            "workflow_id": "wf-arch-1",
            "status": "trashed",
            "archived": True,
        }
    )
    mock_restore = AsyncMock(
        return_value={
            "media_id": "m-arch-1",
            "project_id": "proj-arch-1",
            "workflow_id": "wf-arch-1",
            "status": "restored",
            "archived": False,
        }
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)
    monkeypatch.setattr(flow_wire, "direct_restore_media", mock_restore)

    res_trash = await trash_media_service("m-arch-1", project_id="proj-arch-1", profile="test_prof")
    assert res_trash.archived is True
    assert res_trash.as_json()["archived"] is True

    res_restore = await restore_media_service(
        "m-arch-1", project_id="proj-arch-1", profile="test_prof"
    )
    assert res_restore.archived is False
    assert res_restore.as_json()["archived"] is False


@pytest.mark.asyncio
async def test_media_trash_service_refuses_when_trash_did_not_set_archived(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    # When wire/client returns mutation response without archived flag set to True
    mock_trash = AsyncMock(
        return_value={
            "media_id": "m-arch-bad",
            "project_id": "proj-bad",
            "status": "trashed",
            "archived": False,
        }
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    with pytest.raises(WireFormatError, match="did not set archived flag to True") as exc_info:
        await trash_media_service("m-arch-bad", project_id="proj-bad", profile="test_prof")
    assert exc_info.value.charged == "no"


# ---------------------------------------------------------------------------
# #1451: images are trashed and restored by their workflow id
# ---------------------------------------------------------------------------


def _echo(sent_id: str, project_id: str, *, archived: bool, workflow_slot: str) -> dict[str, Any]:
    """What the wire layer returns: Flow's echo of the id that was sent."""
    return {
        "media_id": sent_id,
        "project_id": project_id,
        "workflow_id": workflow_slot,
        "archived": archived,
        "status": "trashed" if archived else "restored",
    }


@pytest.fixture
def catalog(profile_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()
    return db_path


@pytest.mark.asyncio
@pytest.mark.parametrize("use_project", [False, True])
async def test_image_is_trashed_by_its_workflow_id(
    catalog: Path, monkeypatch: pytest.MonkeyPatch, use_project: bool
) -> None:
    _seed_test_asset(
        catalog,
        asset_id="local-img",
        flow_media_id="img-media",
        flow_workflow_id="img-wf",
        flow_project_id="p-1",
        profile_name="test_prof",
    )
    # Flow echoes the id it was sent and puts the reported media id in the workflow slot.
    mock_trash = AsyncMock(
        return_value=_echo("img-wf", "p-1", archived=True, workflow_slot="img-media")
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    result = await trash_media_service(
        "img-media", profile="test_prof", project_id="p-1" if use_project else None
    )

    expected_pdir = get_settings().profile_subdir("test_prof")
    mock_trash.assert_awaited_once_with(expected_pdir, "img-wf", "p-1")
    assert result.media_id == "img-media"
    assert result.workflow_id == "img-wf"
    assert result.status == "trashed"
    assert "gflow media restore img-media" in (result.restore_hint or "")


@pytest.mark.asyncio
async def test_image_is_restored_by_its_workflow_id(
    catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_test_asset(
        catalog,
        asset_id="local-img",
        flow_media_id="img-media",
        flow_workflow_id="img-wf",
        flow_project_id="p-1",
        profile_name="test_prof",
    )
    mock_restore = AsyncMock(
        return_value=_echo("img-wf", "p-1", archived=False, workflow_slot="img-media")
    )
    monkeypatch.setattr(flow_wire, "direct_restore_media", mock_restore)

    result = await restore_media_service("img-media", profile="test_prof")

    expected_pdir = get_settings().profile_subdir("test_prof")
    mock_restore.assert_awaited_once_with(expected_pdir, "img-wf", "p-1")
    assert result.media_id == "img-media"
    assert result.workflow_id == "img-wf"
    assert result.status == "restored"


@pytest.mark.asyncio
async def test_image_given_by_its_workflow_id_reports_its_media_id(
    catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workaround from #1451 (passing the workflow id) keeps working."""
    _seed_test_asset(
        catalog,
        asset_id="local-img",
        flow_media_id="img-media",
        flow_workflow_id="img-wf",
        flow_project_id="p-1",
        profile_name="test_prof",
    )
    mock_trash = AsyncMock(
        return_value=_echo("img-wf", "p-1", archived=True, workflow_slot="img-media")
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    result = await trash_media_service("img-wf", profile="test_prof", project_id="p-1")

    expected_pdir = get_settings().profile_subdir("test_prof")
    mock_trash.assert_awaited_once_with(expected_pdir, "img-wf", "p-1")
    assert result.media_id == "img-media"


@pytest.mark.asyncio
async def test_video_is_still_trashed_by_its_media_id(
    catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_test_asset(
        catalog,
        asset_id="local-vid",
        flow_media_id="vid-media",
        flow_workflow_id="vid-wf",
        flow_project_id="p-1",
        profile_name="test_prof",
        kind=AssetKind.VIDEO,
    )
    mock_trash = AsyncMock(
        return_value=_echo("vid-media", "p-1", archived=True, workflow_slot="vid-wf")
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    result = await trash_media_service("vid-media", profile="test_prof")

    expected_pdir = get_settings().profile_subdir("test_prof")
    mock_trash.assert_awaited_once_with(expected_pdir, "vid-media", "p-1")
    assert result.media_id == "vid-media"
    assert result.workflow_id == "vid-wf"


@pytest.mark.asyncio
async def test_image_without_a_workflow_id_falls_back_to_its_media_id(
    catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_test_asset(
        catalog,
        asset_id="local-img",
        flow_media_id="img-media",
        flow_workflow_id=None,
        flow_project_id="p-1",
        profile_name="test_prof",
    )
    mock_trash = AsyncMock(return_value=_echo("img-media", "p-1", archived=True, workflow_slot=""))
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    result = await trash_media_service("img-media", profile="test_prof")

    expected_pdir = get_settings().profile_subdir("test_prof")
    mock_trash.assert_awaited_once_with(expected_pdir, "img-media", "p-1")
    assert result.media_id == "img-media"


@pytest.mark.asyncio
async def test_direct_trash_checks_the_echo_against_the_id_it_sent(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flow's echo of the workflow id passes; an echo of another id is refused."""
    reply = _TRASH_REPLY_BODY.replace("m-1", "img-wf")
    monkeypatch.setattr(
        flow_wire,
        "_execute_direct_rpc",
        AsyncMock(return_value=(httpx.Response(200, text=reply), 0.01)),
    )
    res = await flow_wire.direct_trash_media(profile_dir, "img-wf", "proj-1")
    assert res["media_id"] == "img-wf"

    with pytest.raises(WireFormatError, match="media ID mismatch"):
        await flow_wire.direct_trash_media(profile_dir, "img-media", "proj-1")


def _seed_cross_column_collision(catalog: Path, *, video_project: str) -> None:
    """``"x"`` is an image's media id and, in a newer row, a video's workflow id."""
    _seed_test_asset(
        catalog,
        asset_id="local-img",
        flow_media_id="x",
        flow_workflow_id="img-wf",
        flow_project_id="p-1",
        profile_name="test_prof",
        created_at="2026-11-01T00:00:00+00:00",
    )
    _seed_test_asset(
        catalog,
        asset_id="local-vid",
        flow_media_id="vid-media",
        flow_workflow_id="x",
        flow_project_id=video_project,
        profile_name="test_prof",
        kind=AssetKind.VIDEO,
        created_at="2026-11-02T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_project_narrows_an_id_that_matches_several_assets(
    catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_cross_column_collision(catalog, video_project="p-2")
    mock_trash = AsyncMock(return_value=_echo("img-wf", "p-1", archived=True, workflow_slot="x"))
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    result = await trash_media_service("x", profile="test_prof", project_id="p-1")

    expected_pdir = get_settings().profile_subdir("test_prof")
    mock_trash.assert_awaited_once_with(expected_pdir, "img-wf", "p-1")
    assert result.media_id == "x"


@pytest.mark.asyncio
@pytest.mark.parametrize("video_project", ["p-1", "p-3"])
async def test_project_refuses_an_id_that_stays_ambiguous(
    catalog: Path, monkeypatch: pytest.MonkeyPatch, video_project: str
) -> None:
    """Both matches in the project, or neither: refused before anything is sent."""
    _seed_cross_column_collision(catalog, video_project=video_project)
    mock_trash = AsyncMock()
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    project = "p-1" if video_project == "p-1" else "p-9"
    with pytest.raises(DataStoreError, match="Multiple media records match"):
        await trash_media_service("x", profile="test_prof", project_id=project)
    mock_trash.assert_not_awaited()
