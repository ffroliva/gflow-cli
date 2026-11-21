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
    # Trash response with integer 1 and trimmed trailing nulls
    frame_trash = [[["media-123", None, None, ["meta", None, 1]]]]
    parsed_trash = parse_trash_response(frame_trash, expected_media_id="media-123")
    assert parsed_trash["media_id"] == "media-123"
    assert parsed_trash["archived"] is True
    assert parsed_trash["project_id"] == ""
    assert parsed_trash["workflow_id"] == ""

    # Restore response with integer 0 / None and trimmed trailing metadata
    frame_restore = [[["media-123", None, None, []]]]
    parsed_restore = parse_restore_response(frame_restore, expected_media_id="media-123")
    assert parsed_restore["media_id"] == "media-123"
    assert parsed_restore["archived"] is False


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
    client = FlowApiClient(profile_dir=profile_dir, headless=True)
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
    }
    monkeypatch.setattr(
        "gflow_cli.services.media_trash.FlowApiClient",
        MagicMock(return_value=mock_client),
    )

    result = await trash_media_service("local-uuid-1")
    assert result.status == "trashed"
    mock_direct.assert_awaited_once()
    mock_client.trash_media.assert_awaited_once_with("m-flow-1", "p-flow-1")


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
    }
    monkeypatch.setattr(
        "gflow_cli.services.media_trash.FlowApiClient",
        MagicMock(return_value=mock_client),
    )

    result = await restore_media_service("local-uuid-1")
    assert result.status == "restored"
    mock_direct.assert_awaited_once()
    mock_client.restore_media.assert_awaited_once_with("m-flow-1", "p-flow-1")


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
    flow_workflow_id: str = "wf-1",
    status: str = "ready",
) -> None:
    now_str = datetime.now(UTC).isoformat()
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
                kind=AssetKind.IMAGE,
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
        return_value={"media_id": "m-flow-1", "project_id": "p-flow-1", "status": "trashed"}
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
        return_value={"media_id": "m-shared", "project_id": "p-a", "status": "trashed"}
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    res = await trash_media_service("m-shared", profile="prof-a")
    assert res.profile_name == "prof-a"
    assert res.media_id == "m-shared"


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
        return_value={"media_id": "m-100", "project_id": "p-100", "status": "trashed"}
    )
    mock_restore = AsyncMock(
        return_value={"media_id": "m-100", "project_id": "p-100", "status": "restored"}
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
async def test_media_trash_service_with_explicit_project_bypasses_catalog(
    profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = profile_dir.parent.parent / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    reset_settings()

    mock_trash = AsyncMock(
        return_value={"media_id": "uncataloged-id", "project_id": "proj-999", "status": "trashed"}
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    # Calling with project_id should not query catalog and succeed
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
