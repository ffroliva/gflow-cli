"""Direct, credit-free MCP tools for Flow media trash and restore (#1363)."""

from __future__ import annotations

from typing import Any

from gflow_cli.mcp import tools as common
from gflow_cli.mcp.server import server
from gflow_cli.services.media_trash import restore_media_service, trash_media_service


@server.tool(
    name="gflow_media_trash",
    description=(
        "Move a generated media item (image or video) to Flow's Trash over the wire (RPC pGCYOe). "
        "Credit-free (0 credits). Trashed items are hidden from project views but can be recovered "
        "using gflow_media_restore. Requires explicit confirm=True to prevent accidental deletion."
    ),
)
@common._guarded  # pyright: ignore[reportPrivateUsage]
async def gflow_media_trash(
    media_id: str,
    project_id: str | None = None,
    profile: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Move media to Flow's Trash. Requires confirm=True."""
    if not media_id or not media_id.strip():
        return common._bad_param(  # pyright: ignore[reportPrivateUsage]
            "Invalid Media ID", "media_id must not be empty or blank."
        )
    if not confirm:
        return {
            "status": "error",
            "error": {
                "type": "https://gflow-cli.dev/errors/confirmation-required",
                "title": "Confirmation required",
                "status": 400,
                "detail": (
                    f"Moving media {media_id!r} to Flow's Trash hides it from project views. "
                    "Call with confirm=True to confirm trashing this item."
                ),
                "remediation_hint": (
                    "Call gflow_media_trash with confirm=True to confirm trashing this item."
                ),
                **common.outcome_members("no", "fix_input"),  # pyright: ignore[reportPrivateUsage]
            },
        }

    lock_profile = "default"
    if profile is not None:
        resolved = common._resolve_and_validate_profile(profile)  # pyright: ignore[reportPrivateUsage]
        if isinstance(resolved, dict):
            return resolved
        lock_profile = resolved

    async with common._profile_lock(lock_profile):  # pyright: ignore[reportPrivateUsage]
        res = await trash_media_service(
            media_id, profile=profile, project_id=project_id, route="mcp.media_trash"
        )
        return res.as_json()


@server.tool(
    name="gflow_media_restore",
    description=(
        "Restore a trashed media item back to its Flow project over the wire (RPC pGCYOe). "
        "Credit-free (0 credits). Returns the restored item status and ID."
    ),
)
@common._guarded  # pyright: ignore[reportPrivateUsage]
async def gflow_media_restore(
    media_id: str,
    project_id: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Restore media from Flow's Trash."""
    if not media_id or not media_id.strip():
        return common._bad_param(  # pyright: ignore[reportPrivateUsage]
            "Invalid Media ID", "media_id must not be empty or blank."
        )

    lock_profile = "default"
    if profile is not None:
        resolved = common._resolve_and_validate_profile(profile)  # pyright: ignore[reportPrivateUsage]
        if isinstance(resolved, dict):
            return resolved
        lock_profile = resolved

    async with common._profile_lock(lock_profile):  # pyright: ignore[reportPrivateUsage]
        res = await restore_media_service(
            media_id, profile=profile, project_id=project_id, route="mcp.media_restore"
        )
        return res.as_json()
