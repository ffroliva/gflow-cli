"""Unit tests for media trash and restore MCP tools (#1363)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gflow_cli.api import flow_wire
from gflow_cli.config import reset_settings
from gflow_cli.data.models import AssetKind, AssetRecord, ProjectRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.mcp.media_trash_tools import gflow_media_restore, gflow_media_trash
from gflow_cli.mcp.server import server


@pytest.fixture
def mcp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "1")
    reset_settings()

    profile_dir = tmp_path / "profiles" / "default"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile_default").mkdir(parents=True, exist_ok=True)

    with DataStore.open(db_path) as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", profile_dir)
        repo.upsert_project(
            ProjectRecord(
                id="p-rec-1",
                profile_name="default",
                flow_project_id="proj-uuid-1",
                title="MCP Test Proj",
                source="cli",
                created_at="2026-11-21T00:00:00Z",
            )
        )
        repo.upsert_asset(
            AssetRecord(
                id="asset-rec-1",
                profile_name="default",
                flow_project_id="proj-uuid-1",
                flow_media_id="media-uuid-1",
                flow_workflow_id="wf-uuid-1",
                flow_media_generation_id=None,
                kind=AssetKind.IMAGE,
                status="ready",
                model="imagen-3.0-fast-generate-001",
                aspect_ratio="16:9",
                width=1280,
                height=720,
                duration_seconds=None,
                seed=1,
                metadata_json={},
                created_at="2026-11-21T00:00:00Z",
            )
        )
    return db_path


def test_mcp_media_trash_tools_are_registered() -> None:
    tools = server._tool_manager._tools  # pyright: ignore[reportPrivateUsage]
    assert "gflow_media_trash" in tools
    assert "gflow_media_restore" in tools
    trash_tool = tools["gflow_media_trash"]
    assert "media_id" in trash_tool.parameters.get("properties", {})
    assert "confirm" in trash_tool.parameters.get("properties", {})


@pytest.mark.asyncio
async def test_mcp_media_trash_refuses_unconfirmed(mcp_env: Path) -> None:
    res = await gflow_media_trash(media_id="media-uuid-1", confirm=False)
    assert res.get("status") == "error"
    err = res.get("error", {})
    assert err.get("type") == "https://gflow-cli.dev/errors/confirmation-required"
    assert "confirm=True" in err.get("detail", "")
    assert err.get("status") == 400
    assert err.get("charged") == "no"


@pytest.mark.asyncio
async def test_mcp_media_trash_confirmed_succeeds(
    mcp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_trash = AsyncMock(
        return_value={
            "media_id": "media-uuid-1",
            "project_id": "proj-uuid-1",
            "status": "trashed",
            "archived": True,
        }
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    res = await gflow_media_trash(media_id="media-uuid-1", confirm=True)
    assert res.get("status") == "trashed"
    assert res.get("media_id") == "media-uuid-1"
    assert res.get("project_id") == "proj-uuid-1"
    assert res.get("archived") is True
    assert res.get("charged") == "no"
    assert "restore_hint" in res
    mock_trash.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_media_restore_succeeds(mcp_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mock_restore = AsyncMock(
        return_value={
            "media_id": "media-uuid-1",
            "project_id": "proj-uuid-1",
            "status": "restored",
            "archived": False,
        }
    )
    monkeypatch.setattr(flow_wire, "direct_restore_media", mock_restore)

    res = await gflow_media_restore(media_id="media-uuid-1")
    assert res.get("status") == "restored"
    assert res.get("media_id") == "media-uuid-1"
    assert res.get("project_id") == "proj-uuid-1"
    assert res.get("archived") is False
    assert res.get("charged") == "no"
    mock_restore.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_media_trash_refuses_empty_id(mcp_env: Path) -> None:
    res = await gflow_media_trash(media_id="   ", confirm=True)
    assert res.get("status") == "error"
    err = res.get("error", {})
    assert "empty or blank" in err.get("detail", "")


@pytest.mark.asyncio
async def test_mcp_media_restore_refuses_empty_id(mcp_env: Path) -> None:
    res = await gflow_media_restore(media_id="   ")
    assert res.get("status") == "error"
    err = res.get("error", {})
    assert "empty or blank" in err.get("detail", "")


@pytest.mark.asyncio
async def test_mcp_media_trash_with_project_id_succeeds_without_catalog(
    mcp_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_trash = AsyncMock(
        return_value={
            "media_id": "uncataloged-m",
            "project_id": "proj-ext",
            "status": "trashed",
            "archived": True,
        }
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    res = await gflow_media_trash(media_id="uncataloged-m", project_id="proj-ext", confirm=True)
    assert res.get("status") == "trashed"
    assert res.get("media_id") == "uncataloged-m"
    assert res.get("project_id") == "proj-ext"
    assert res.get("archived") is True
    mock_trash.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_media_trash_validates_profile(mcp_env: Path) -> None:
    res = await gflow_media_trash(
        media_id="media-uuid-1", profile="nonexistent-profile-xyz", confirm=True
    )
    assert res.get("status") == "error"
    err = res.get("error", {})
    assert "Profile" in err.get("title", "")


def test_mcp_media_tools_use_shared_guarded_and_have_no_aliases() -> None:
    import gflow_cli.mcp.media_trash_tools as mtt

    assert getattr(mtt.gflow_media_trash, "__gflow_guarded__", False) is True
    assert getattr(mtt.gflow_media_restore, "__gflow_guarded__", False) is True
    assert "ctx" in mtt.gflow_media_trash.__annotations__
    assert "ctx" in mtt.gflow_media_restore.__annotations__
    assert "media_trash" not in mtt.__dict__
    assert "media_restore" not in mtt.__dict__
