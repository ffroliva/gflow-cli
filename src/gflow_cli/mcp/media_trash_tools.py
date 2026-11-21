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
    profile: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Move media to Flow's Trash. Requires confirm=True."""
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
            },
        }

    res = await trash_media_service(media_id, profile=profile, route="mcp.media_trash")
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
    profile: str | None = None,
) -> dict[str, Any]:
    """Restore media from Flow's Trash."""
    res = await restore_media_service(media_id, profile=profile, route="mcp.media_restore")
    return res.as_json()


# Alias bindings for internal references
media_trash = gflow_media_trash
media_restore = gflow_media_restore
