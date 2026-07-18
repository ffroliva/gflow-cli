"""E2E test verifying asset tagging (@ mentions) resolver end-to-end.

Requires the master opt-in:
    GFLOW_CLI_E2E_PROFILE=<profile-name> uv run pytest -m e2e -v \
        tests/e2e/test_asset_tagging_e2e.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
@pytest.mark.e2e_image
async def test_e2e_asset_tagging_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Reset CLI settings to avoid isolation overrides
    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.delenv("GFLOW_CLI_DB_PATH", raising=False)
    from gflow_cli.config import reset_settings

    reset_settings()

    from gflow_cli.auth import profile_dir as _resolve_profile_dir

    # Check profile environment
    name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not name:
        pytest.skip("set GFLOW_CLI_E2E_PROFILE to a logged-in profile name")
    e2e_profile_dir = _resolve_profile_dir(name)
    if not e2e_profile_dir.exists():
        pytest.skip(f"profile dir not found: {e2e_profile_dir}")

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        # 1. Create a fresh project
        project = await client.create_project(title="e2e-asset-tagging-test")
        project_id = project.project_id

        # 2. Create a character entity (Zoro)
        entity_id = await client.create_entity(project_id)
        await client.patch_entity(
            project_id=project_id,
            entity_id=entity_id,
            display_name="Zoro",
            workflow_ids=[],
        )

    # 3. Invoke the CLI via subprocess using this project and a mention
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "image",
            "t2i",
            "a photo of @Zoro walking",
            "--project",
            project_id,
            "--model",
            "nano2",
            "--profile",
            name,
            "--out",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=os.environ,
        check=False,
    )

    # Assert CLI command succeeded
    assert result.returncode == 0, f"CLI command failed: stderr={result.stderr}"

    # 4. Verify that an image was produced
    images = list(out_dir.rglob("*.png")) + list(out_dir.rglob("*.jpg"))
    assert images, f"No image produced; out_dir={out_dir}"
    assert images[0].stat().st_size > 1024

    # 5. Query the database to verify the operation recorded the de-tagged prompt
    # and reference entity staging.
    from gflow_cli.config import get_settings

    db_path = get_settings().resolved_db_path()
    store = DataStore(db_path)
    repo = DataRepository(store)

    # Find the last recorded operation
    ops = repo.list_operations(limit=10)
    assert ops, "No operations recorded in database"

    # Find our t2i operation
    t2i_op = next((op for op in ops if op.get("operation_kind") == "t2i"), None)
    assert t2i_op is not None, "t2i operation not found in database"

    # Verify de-tagged prompt was recorded
    assert t2i_op["prompt"] == "a photo of Zoro walking"

    # Retrieve inputs for this operation to verify reference entity staging
    inputs = repo.get_operation_inputs(t2i_op["operation_id"])
    # Zoro entity_id should be in the inputs
    ref_ids = [inp["ref_id"] for inp in inputs if inp["ref_type"] == "character"]
    assert entity_id in ref_ids, f"Expected character ref_id {entity_id} in {ref_ids}"
