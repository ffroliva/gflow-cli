"""CLI tests for `gflow media trash` and `gflow media restore` (#1363)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from gflow_cli.api import flow_wire
from gflow_cli.cli import main
from gflow_cli.config import reset_settings
from gflow_cli.data.models import AssetKind, AssetRecord, ProjectRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "catalog.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    monkeypatch.setenv("GFLOW_CLI_HOME", str(tmp_path))
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "1")
    reset_settings()

    profile_dir = tmp_path / "profiles" / "default"
    profile_dir.mkdir(parents=True, exist_ok=True)

    now_str = datetime.now(UTC).isoformat()
    with DataStore.open(db_path) as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", profile_dir)
        repo.upsert_project(
            ProjectRecord(
                id=str(uuid.uuid4()),
                profile_name="default",
                flow_project_id="p-123",
                title="CLI Test Proj",
                source="cli",
                created_at=now_str,
            )
        )
        repo.upsert_asset(
            AssetRecord(
                id="asset-local-1",
                profile_name="default",
                flow_project_id="p-123",
                flow_media_id="media-123",
                flow_workflow_id="wf-123",
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
                created_at=now_str,
            )
        )
    return db_path


def test_cli_media_help(runner: CliRunner) -> None:
    res = runner.invoke(main, ["media", "--help"])
    assert res.exit_code == 0
    assert "trash" in res.output
    assert "restore" in res.output


def test_cli_media_trash_requires_yes_in_non_interactive_shell(
    runner: CliRunner, seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gflow_cli.cli_media._is_interactive", lambda: False)
    res = runner.invoke(main, ["media", "trash", "media-123"])
    assert res.exit_code == 2
    assert "--yes is required" in res.output


def test_cli_media_trash_with_yes_succeeds(
    runner: CliRunner, seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_trash = AsyncMock(
        return_value={"media_id": "media-123", "project_id": "p-123", "status": "trashed"}
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    res = runner.invoke(main, ["media", "trash", "media-123", "--yes", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["status"] == "trashed"
    assert data["media_id"] == "media-123"
    assert data["project_id"] == "p-123"
    assert data["charged"] == "no"
    assert "restore_hint" in data
    mock_trash.assert_awaited_once()


def test_cli_media_restore_succeeds(
    runner: CliRunner, seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_restore = AsyncMock(
        return_value={"media_id": "media-123", "project_id": "p-123", "status": "restored"}
    )
    monkeypatch.setattr(flow_wire, "direct_restore_media", mock_restore)

    res = runner.invoke(main, ["media", "restore", "media-123", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["status"] == "restored"
    assert data["media_id"] == "media-123"
    assert data["project_id"] == "p-123"
    assert data["charged"] == "no"
    mock_restore.assert_awaited_once()


def test_cli_media_trash_interactive_abort(
    runner: CliRunner, seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gflow_cli.cli_media._is_interactive", lambda: True)
    # Simulate user entering 'n' to confirmation prompt
    res = runner.invoke(main, ["media", "trash", "media-123"], input="n\n")
    assert res.exit_code == 0
    assert "Aborted." in res.output


def test_cli_media_trash_interactive_confirm(
    runner: CliRunner, seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gflow_cli.cli_media._is_interactive", lambda: True)
    mock_trash = AsyncMock(
        return_value={"media_id": "media-123", "project_id": "p-123", "status": "trashed"}
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    # Simulate user entering 'y' to confirmation prompt
    res = runner.invoke(main, ["media", "trash", "media-123"], input="y\n")
    assert res.exit_code == 0
    assert "Moved media media-123 to Flow's Trash" in res.output
    mock_trash.assert_awaited_once()


def test_cli_media_trash_output_format_id(
    runner: CliRunner, seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_trash = AsyncMock(
        return_value={"media_id": "media-123", "project_id": "p-123", "status": "trashed"}
    )
    monkeypatch.setattr(flow_wire, "direct_trash_media", mock_trash)

    res = runner.invoke(main, ["media", "trash", "media-123", "--yes", "-o", "id"])
    assert res.exit_code == 0
    assert res.output.strip() == "media-123"


def test_cli_media_trash_refuses_empty_id(runner: CliRunner, seeded_db: Path) -> None:
    res = runner.invoke(main, ["media", "trash", "   ", "--yes"])
    assert res.exit_code == 2
    assert "must not be empty or blank" in res.output


def test_cli_media_restore_refuses_empty_id(runner: CliRunner, seeded_db: Path) -> None:
    res = runner.invoke(main, ["media", "restore", "   "])
    assert res.exit_code == 2
    assert "must not be empty or blank" in res.output

