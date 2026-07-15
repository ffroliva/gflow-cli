"""E2E test verifying that poisoned or unrequested character reference entities
are successfully intercepted and stripped from outgoing generation requests.

Requires the master opt-in:
    GFLOW_CLI_E2E_PROFILE=<profile-name> uv run pytest -m e2e -v \\
        tests/e2e/test_entity_smuggling_e2e.py
"""

from __future__ import annotations

import os

import pytest
import structlog

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import GenerateImageRequest, Model

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
@pytest.mark.e2e_image
async def test_e2e_entity_smuggling_interception(
    monkeypatch: pytest.MonkeyPatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Creates a character entity in a project, then runs an image generation
    without requested reference entities. Verifies that the interceptor
    strips the smuggled entity and the request body goes to the server cleanly
    without referenceEntities.
    """
    # Undo setting isolation to resolve real platformdirs
    from gflow_cli.auth import profile_dir as _resolve_profile_dir
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.delenv("GFLOW_CLI_DB_PATH", raising=False)
    reset_settings()

    name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not name:
        pytest.skip("set GFLOW_CLI_E2E_PROFILE to a logged-in profile name")
    e2e_profile_dir = _resolve_profile_dir(name)
    if not e2e_profile_dir.exists():
        pytest.skip(f"profile dir not found: {e2e_profile_dir}")

    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        # 1. Create a fresh project
        project = await client.create_project(title="e2e-entity-smuggling-test")

        # 2. Create a character entity (this is a free REST call)
        entity_id = await client.create_entity(project.project_id)
        assert entity_id, "Failed to create character entity on project"

        # 3. Generate an image in that project WITHOUT requesting the character
        req = GenerateImageRequest(
            prompt="a vibrant red apple on a clean white table",
            model=Model.NARWHAL,
        )
        image = await client.generate_image(project_id=project.project_id, req=req)

    # Verify generation succeeded
    assert image.media_name, "Image generation failed"
    assert image.fife_url.startswith("https://")

    # 4. Check structural logs for interception and request shape
    # We look for "ui_automation.batch_request_body" which summarizes the outgoing payload
    bodies = [
        e for e in install_log_capture.entries if e["event"] == "ui_automation.batch_request_body"
    ]
    assert bodies, "No batch_request_body logs captured during generation"

    # Confirm that referenceEntities was NOT sent in the outgoing body
    for entry in bodies:
        summary = entry.get("summary")
        assert isinstance(summary, dict)
        assert not summary.get("mentions_reference_entities"), (
            f"referenceEntities were leaked to the server in outgoing request: {summary}"
        )

    # Optional: Verify if the UI did attempt to smuggle and was modified
    modified = [
        e
        for e in install_log_capture.entries
        if e["event"] == "ui_automation.batch_request_modified"
    ]
    if modified:
        print(
            "Note: Outgoing payload was successfully intercepted and modified "
            "to strip smuggled entities."
        )
