"""E2E test verifying asset tagging (@ mentions) resolver end-to-end.

Requires the master opt-in:
    GFLOW_CLI_E2E_PROFILE=<profile-name> uv run pytest -m e2e -v \
        tests/e2e/test_asset_tagging_e2e.py

Spends ~2 Imagen credits: one to create a TAGGABLE character (a bare, image-less
entity cannot stage as a referenceEntity), one for the t2i generation that
applies the tag.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient

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

    # 2. Create a TAGGABLE character "Zoro" via `character create` — this runs a
    # real face image-gen that binds workflow_ids (reference images) to the
    # entity. A bare, image-less entity cannot stage as a referenceEntity, so
    # the tag would never ride the wire. Mirrors test_character_create_e2e.py.
    create = subprocess.run(
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "character",
            "create",
            "--project",
            project_id,
            "--name",
            "Zoro",
            "--face-prompt",
            "portrait of a calm man with short dark hair, soft studio lighting, "
            "neutral background, photorealistic",
            "--profile",
            name,
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env=os.environ,
        check=False,
    )
    assert create.returncode == 0, f"character create failed: stderr={create.stderr}"
    # --json emits one (pretty-printed) JSON document, possibly after a line of
    # human-readable log noise ("  Project: ... (existing)"). Slice the outer
    # object rather than assuming it is on a single line.
    _out = create.stdout
    payload = json.loads(_out[_out.index("{") : _out.rindex("}") + 1])
    character = payload["character"]
    entity_id = str(character["entity_id"])
    assert character.get("workflow_ids"), f"character has no reference images: {character}"

    # 2b. Wait for read-after-write propagation. The mention resolver's
    # AssetIndex reads Google's flow.projectInitialData, which reflects the
    # just-created (and patched) entity only after a short backend lag. Without
    # this wait the CLI subprocess below races the create and the mention
    # resolves to "Available assets: <none>" (ConfigurationError, exit 11). This
    # lag is a test-only artifact — real users create characters long before
    # they generate, so no product-side wait is warranted.
    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        for _ in range(30):
            chars = await client.list_characters(project_id)
            if any(c.entity_id == entity_id and c.workflow_ids for c in chars):
                break
            await asyncio.sleep(1.0)
        else:
            pytest.fail(
                f"taggable entity {entity_id} not visible via list_characters after 30s "
                "(projectInitialData propagation exceeded budget)"
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

    # Assert CLI command succeeded. Exit 0 is itself proof that the character
    # rode the wire: the runtime integrity guard raises WireFormatError (exit 7)
    # if the submit's referenceEntities is missing the requested entity.
    assert result.returncode == 0, f"CLI command failed: stderr={result.stderr}"

    # 4. Verify that an image was produced
    images = list(out_dir.rglob("*.png")) + list(out_dir.rglob("*.jpg"))
    assert images, f"No image produced; out_dir={out_dir}"
    assert images[0].stat().st_size > 1024

    # 5. Verify the DB recorded the DE-TAGGED prompt ("@Zoro" -> "Zoro"). There
    # is no operation_inputs table to read back the entity ref from — exit 0
    # above is the authoritative proof that the entity rode the wire.
    from gflow_cli.config import get_settings
    from gflow_cli.data.queries import list_images

    db_path = get_settings().resolved_db_path()
    imgs = list_images(db_path=db_path, profile=name, limit=20, offset=0)
    assert any(img.prompt == "a photo of Zoro walking" for img in imgs), (
        f"de-tagged prompt not recorded; prompts={[img.prompt for img in imgs]}"
    )
