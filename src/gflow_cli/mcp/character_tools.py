"""MCP twin of ``gflow character create --image``: a character built from local files (#639).

Credit-free: the images are uploaded through Flow's character editor and nothing is
generated, so the tool is not one of the spending tools ``GFLOW_MCP_NO_SPEND`` removes.
The prompt-generated create stays CLI-only (#691).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from gflow_cli.api.character import canonical_voice
from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import get_settings
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.mcp import tools as common
from gflow_cli.mcp.server import server
from gflow_cli.services.character_create import character_create_from_files

log = structlog.get_logger(__name__)


def _check_inputs(
    project: str, name: str, image: str, body_image: str | None
) -> tuple[dict[str, Any] | None, Path, Path | None]:
    """The refusal for a bad project id, blank name or missing image file, else the paths.

    Checked before any browser opens, so a bad argument costs nothing."""
    none = (Path(), None)
    if (bad := common._validate_project(project)) is not None:  # pyright: ignore[reportPrivateUsage]
        return bad, *none
    if not name.strip():
        return common._bad_param("Blank Name", "name must not be blank."), *none  # pyright: ignore[reportPrivateUsage]
    portrait, bad = common._resolve_image_path(image, title="Invalid Image", label="image")  # pyright: ignore[reportPrivateUsage]
    if bad is not None or portrait is None:
        return bad, *none
    if body_image is None:
        return None, Path(portrait), None
    body, bad = common._resolve_image_path(  # pyright: ignore[reportPrivateUsage]
        body_image, title="Invalid Body Image", label="body_image"
    )
    if bad is not None or body is None:
        return bad, *none
    return None, Path(portrait), Path(body)


@server.tool(
    name="gflow_character_create",
    description=(
        "Create a Flow CHARACTER from local image files: `image` is the portrait, "
        "`body_image` an optional body image, both paths on the machine running this MCP "
        "server. Nothing is generated: spends no credits and no image quota. Drives the "
        "character editor on flow.google.com in a browser, then sets the name, personality "
        "and voice (see gflow_character_voices). A failure after the portrait upload names "
        "the character's entity_id in remediation_hint when known, otherwise says one may "
        "exist, and is not retryable: check gflow_character_list before calling again. "
        "gflow deletes nothing. Generating the "
        "face from a prompt is CLI-only (`gflow character create --face-prompt`)."
    ),
)
@common._guarded  # pyright: ignore[reportPrivateUsage]
async def gflow_character_create(
    project: str,
    name: str,
    image: str,
    body_image: str | None = None,
    voice: str | None = None,
    personality: str | None = None,
    profile: str = "default",
) -> dict[str, Any]:
    """Create a character from local image files (mirrors ``gflow character create --image``).

    Args:
        project: Flow project to create it in.
        name: Display name.
        image: Portrait image path, on the server's filesystem.
        body_image: Optional body image path, on the server's filesystem.
        voice: Preset voice name, case-insensitive (see gflow_character_voices).
        personality: Personality notes.
        profile: gflow-cli profile name; resolves like the CLI when left as "default".

    Returns:
        ``{"status": "ok", "project": ..., "character": {entity_id, name, voice,
        workflow_ids, primary_media_ids}}``.
    """
    refusal, portrait, body = _check_inputs(project, name, image, body_image)
    if refusal is not None:
        return refusal
    voice_name = canonical_voice(voice) if voice is not None else None
    if voice is not None and voice_name is None:
        return common._bad_param(  # pyright: ignore[reportPrivateUsage]
            "Unknown Voice",
            f"unknown voice: {voice!r}; call gflow_character_voices for valid names.",
        )
    log.info("mcp.tool.character_create", project=project, body=body_image is not None)
    resolved = common._resolve_and_validate_profile(profile)  # pyright: ignore[reportPrivateUsage]
    if isinstance(resolved, dict):
        return resolved
    settings = get_settings()
    profile_dir = settings.profile_subdir(resolved)
    async with (
        common._profile_lock(resolved),  # pyright: ignore[reportPrivateUsage]
        FlowApiClient(profile_dir=profile_dir) as client,
    ):
        recorder = OperationRecorder.open(settings)
        try:
            result = await character_create_from_files(
                client,
                recorder,
                profile_name=resolved,
                profile_dir=profile_dir,
                project_id=project,
                name=name,
                portrait=portrait,
                body=body,
                voice=voice_name,
                personality=personality,
            )
        finally:
            recorder.close()
    return {
        "status": "ok",
        "project": project,
        "character": {
            "entity_id": result.entity_id,
            "name": result.name,
            "voice": result.voice,
            "workflow_ids": list(result.workflow_ids),
            "primary_media_ids": list(result.primary_media_ids),
        },
    }
