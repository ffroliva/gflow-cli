"""Credit-free e2e for `gflow scene` — composes an existing clip into a scene.

Opt-in: ``-m e2e_scene`` + ``GFLOW_CLI_E2E_PROFILE`` (a logged-in profile) +
``GFLOW_CLI_E2E_SCENE_WORKFLOW_ID`` (an existing video clip's workflowId).
Scene ops carry NO reCAPTCHA and spend NO credits — this asserts that invariant
by spying that ZERO ``batchAsyncGenerate*`` calls fire. ``asyncio_mode=auto`` is
set in pyproject, so no ``@pytest.mark.asyncio`` is needed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.client import FlowApiClient

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_scene]


async def test_scene_compose_is_credit_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Opt out of the autouse home-redirect so the REAL logged-in profile resolves,
    # but keep a throwaway DB so we never pollute the real catalog.
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_HOME", raising=False)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path / "e2e.db"))
    reset_settings()

    name = os.environ.get("GFLOW_CLI_E2E_PROFILE", "")
    wf = os.environ.get("GFLOW_CLI_E2E_SCENE_WORKFLOW_ID", "")
    if not name or not wf:
        pytest.skip(
            "set GFLOW_CLI_E2E_PROFILE + GFLOW_CLI_E2E_SCENE_WORKFLOW_ID, then -m e2e_scene"
        )
    from gflow_cli.auth import profile_dir as _resolve_profile_dir

    profile = _resolve_profile_dir(name)
    if not profile.exists():
        pytest.skip(f"profile not found: {profile} — run `gflow auth login --profile {name}`")

    generate_calls: list[str] = []
    async with FlowApiClient(profile_dir=profile) as client:
        orig_post = client._post_json

        async def _spy(url: str, body: dict, **kw: Any) -> Any:
            if "batchAsyncGenerate" in url or "batchGenerate" in url:
                generate_calls.append(url)
            return await orig_post(url, body, **kw)

        monkeypatch.setattr(client, "_post_json", _spy)

        project = await client.create_project(title="scene e2e")
        scene = await client.create_scene(project_id=project.project_id, workflow_ids=[wf, wf])
        read_back = await client.get_scene_workflows(scene.scene_id, project_id=project.project_id)

    assert scene.scene_id, "create_scene returned a sceneId"
    assert len(read_back.workflows) == 2, "two clip instances (duplicate of one source)"
    assert generate_calls == [], f"scene compose must spend ZERO credits; saw {generate_calls}"
