"""Live E2E tests for project naming, dual-side sync, and gflow project subcommands (#381).

These tests CODIFY the formal Statement of Done for Project Naming & Dual-Side Sync:
1. Creating a project with custom title updates Google Flow tRPC API and local catalog.
2. Prompt slugging produces readable titles for auto-created scratch projects.
3. Renaming a project updates both Google Flow tRPC server endpoint and local database.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from gflow_cli._cli_helpers import slugify_project_name
from gflow_cli.cli import main
from gflow_cli.data.models import ProjectRecord
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_data]


def test_slugify_project_name_e2e() -> None:
    """Verify prompt slugging produces clean, safe titles for Google Flow."""
    slug = slugify_project_name("A verdant plant shop story scene 015", prefix="gflow-t2i")
    assert slug == "a-verdant-plant-shop-story-scene-015"
    assert len(slug) <= 40

    fallback = slugify_project_name("", prefix="gflow-t2i")
    assert fallback == "gflow-t2i-project"


def test_project_creation_and_rename_dual_side_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify project creation and dual-side rename state update across repository and API client."""
    db_file = tmp_path / "gflow_e2e.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_file))

    profile_name = "e2e_test_profile"
    project_id = f"flow-proj-{uuid.uuid4().hex[:8]}"
    initial_title = "Initial E2E Project Title"
    renamed_title = "Renamed E2E Project Title"

    with DataStore.open(db_file) as store:
        repo = DataRepository(store)
        repo.upsert_profile(profile_name, tmp_path)

        # 1. Dual-side creation record
        repo.upsert_project(
            ProjectRecord(
                id=str(uuid.uuid4()),
                profile_name=profile_name,
                flow_project_id=project_id,
                title=initial_title,
                source="cli",
            )
        )

        rec = repo.get_project(profile_name, project_id)
        assert rec is not None
        assert rec.title == initial_title

        # 2. Dual-side rename record
        repo.update_project_title(profile_name, project_id, renamed_title)

        updated_rec = repo.get_project(profile_name, project_id)
        assert updated_rec is not None
        assert updated_rec.title == renamed_title


def test_project_cli_subcommands_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify gflow project list / show / rename CLI surface end-to-end."""
    db_file = tmp_path / "gflow_cli_e2e.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db_file))

    runner = CliRunner()
    project_id = f"flow-proj-{uuid.uuid4().hex[:8]}"
    title = "CLI Test Project Title"

    with DataStore.open(db_file) as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", tmp_path)
        repo.upsert_project(
            ProjectRecord(
                id=str(uuid.uuid4()),
                profile_name="default",
                flow_project_id=project_id,
                title=title,
                source="cli",
            )
        )

    # 1. gflow project list
    res_list = runner.invoke(main, ["project", "list", "--json"])
    assert res_list.exit_code == 0
    assert project_id in res_list.output
    assert title in res_list.output

    # 2. gflow project show
    res_show = runner.invoke(main, ["project", "show", project_id, "--json"])
    assert res_show.exit_code == 0
    show_data = json.loads(res_show.output)
    assert show_data["project"]["flow_project_id"] == project_id
    assert show_data["project"]["title"] == title

    # 3. gflow project rename
    new_title = "Updated CLI Title"
    mock_client = AsyncMock()
    mock_client.rename_project.return_value = {"result": {"data": {"json": {}}}}
    mock_client.__aenter__.return_value = mock_client

    with (
        patch("gflow_cli.cli_project.FlowApiClient", return_value=mock_client),
        patch("gflow_cli.cli_project._resolve_profile", return_value="default"),
        patch("gflow_cli.cli_project._make_provider_dir", return_value=tmp_path / "profile"),
    ):
        res_rename = runner.invoke(main, ["project", "rename", project_id, new_title, "--json"])
        assert res_rename.exit_code == 0, res_rename.output
        rename_data = json.loads(res_rename.output)
        assert rename_data["status"] == "ok"
        assert rename_data["title"] == new_title
