# SPDX-License-Identifier: MIT
"""Unit tests for `gflow project` CLI subcommands (list, show, create, rename)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli.api.dto import ProjectInfo
from gflow_cli.cli import main
from gflow_cli.data.models import ProjectRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    with DataStore.open(p) as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", tmp_path / "profile")
        repo.upsert_project(
            ProjectRecord(
                id="rec-1",
                profile_name="default",
                flow_project_id="proj-100",
                title="Initial Title",
                source="gflow-cli",
            )
        )
    return p


def test_project_list_text(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    result = runner.invoke(main, ["project", "list"])
    assert result.exit_code == 0
    assert "proj-100" in result.output
    assert "Initial Title" in result.output


def test_project_list_json(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    result = runner.invoke(main, ["project", "list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert len(payload["projects"]) == 1
    assert payload["projects"][0]["project_id"] == "proj-100"
    assert payload["projects"][0]["title"] == "Initial Title"


def test_project_show_text(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    result = runner.invoke(main, ["project", "show", "proj-100"])
    assert result.exit_code == 0
    assert "Project ID: proj-100" in result.output
    assert "Title: Initial Title" in result.output


def test_project_show_json(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    result = runner.invoke(main, ["project", "show", "proj-100", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["project"]["flow_project_id"] == "proj-100"
    assert payload["project"]["title"] == "Initial Title"


def test_project_show_not_found(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    result = runner.invoke(main, ["project", "show", "proj-missing"])
    assert result.exit_code == 1
    assert "Project not found" in result.output


def test_project_show_not_found_json(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))
    result = runner.invoke(main, ["project", "show", "proj-missing", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["title"] == "Project Not Found"


def test_project_create_dual_side(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))

    mock_client = AsyncMock()
    mock_client.create_project.return_value = ProjectInfo(
        project_id="proj-200", title="My New Project"
    )
    mock_client.__aenter__.return_value = mock_client

    with (
        patch("gflow_cli.cli_project.FlowApiClient", return_value=mock_client),
        patch("gflow_cli.cli_project._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_project._make_provider_dir", return_value=db_path.parent / "profile"),
    ):
        result = runner.invoke(main, ["project", "create", "--name", "My New Project"])
        assert result.exit_code == 0, result.output
        assert "proj-200" in result.output
        assert "My New Project" in result.output
        mock_client.create_project.assert_awaited_once_with(title="My New Project")

    # Verify local catalog sync
    repo = DataRepository(DataStore.open(db_path))
    rec = repo.get_project("default", "proj-200")
    assert rec is not None
    assert rec.title == "My New Project"


def test_project_create_json(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))

    mock_client = AsyncMock()
    mock_client.create_project.return_value = ProjectInfo(
        project_id="proj-201", title="JSON Project"
    )
    mock_client.__aenter__.return_value = mock_client

    with (
        patch("gflow_cli.cli_project.FlowApiClient", return_value=mock_client),
        patch("gflow_cli.cli_project._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_project._make_provider_dir", return_value=db_path.parent / "profile"),
    ):
        result = runner.invoke(main, ["project", "create", "--name", "JSON Project", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["status"] == "ok"
        assert payload["project_id"] == "proj-201"
        assert payload["title"] == "JSON Project"


def test_project_rename_dual_side(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))

    mock_client = AsyncMock()
    mock_client.rename_project.return_value = {"result": {"data": {"json": {}}}}
    mock_client.__aenter__.return_value = mock_client

    with (
        patch("gflow_cli.cli_project.FlowApiClient", return_value=mock_client),
        patch("gflow_cli.cli_project._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_project._make_provider_dir", return_value=db_path.parent / "profile"),
    ):
        result = runner.invoke(main, ["project", "rename", "proj-100", "Renamed Project Title"])
        assert result.exit_code == 0, result.output
        assert "Renamed project proj-100 to 'Renamed Project Title'" in result.output
        mock_client.rename_project.assert_awaited_once_with("proj-100", "Renamed Project Title")

    # Verify local catalog sync
    repo = DataRepository(DataStore.open(db_path))
    rec = repo.get_project("default", "proj-100")
    assert rec is not None
    assert rec.title == "Renamed Project Title"


def test_project_rename_json(
    runner: CliRunner, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_path))

    mock_client = AsyncMock()
    mock_client.rename_project.return_value = {"result": {"data": {"json": {}}}}
    mock_client.__aenter__.return_value = mock_client

    with (
        patch("gflow_cli.cli_project.FlowApiClient", return_value=mock_client),
        patch("gflow_cli.cli_project._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_project._make_provider_dir", return_value=db_path.parent / "profile"),
    ):
        result = runner.invoke(main, ["project", "rename", "proj-100", "JSON Renamed", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["status"] == "ok"
        assert payload["project_id"] == "proj-100"
        assert payload["title"] == "JSON Renamed"


def test_project_rename_invalid_id(runner: CliRunner) -> None:
    result = runner.invoke(main, ["project", "rename", "bad/id", "New Title"])
    assert result.exit_code == 2
    assert "project id must be 1-128 chars" in result.output
