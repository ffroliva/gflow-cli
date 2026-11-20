"""MCP adapter for credit-free local video assembly."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from gflow_cli.mcp.server import server
from gflow_cli.services.video_assembly import (
    assemble_videos,
    assembly_result_payload,
    resolve_assembly_input,
)


def _guarded(fn: Any) -> Any:
    """Import the shared error funnel only after MCP tool registration completes."""
    from functools import wraps

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        from gflow_cli.mcp.tools import _guarded as guarded  # pyright: ignore[reportPrivateUsage]

        return await guarded(fn)(*args, **kwargs)

    return wrapper


@server.tool(
    name="gflow_video_assemble",
    description=(
        "Join local video files or locally available catalog video IDs with optional music "
        "and voiceover. This is local, credit-free processing; out_file must be inside the "
        "configured output directory. Geometry and frame rate follow the first clip."
    ),
)
@_guarded
async def gflow_video_assemble(
    clips: list[str],
    out_file: str,
    music: str | None = None,
    voiceover: str | None = None,
    clip_volume: float = 1.0,
    music_volume: float = 0.3,
    voiceover_volume: float = 1.0,
    duck: bool = True,
    profile: str | None = None,
) -> dict[str, Any]:
    """Join local clips without Flow calls or credits.

    Args:
        clips: Local video paths or catalog UUIDs with verified local copies.
        out_file: Required MP4 destination, relative to the configured output directory.
        music: Optional local audio file path.
        voiceover: Optional local audio file path.
        clip_volume: Clip audio gain, from 0 to 1 (default 1).
        music_volume: Music gain, from 0 to 1 (default 0.3).
        voiceover_volume: Voiceover gain, from 0 to 1 (default 1).
        duck: Reduce music while voiceover is present (default true).
        profile: Optional catalog profile to disambiguate media IDs.
    """
    from gflow_cli.mcp.tools import (
        _bad_param,  # pyright: ignore[reportPrivateUsage]
        _resolve_out_file,  # pyright: ignore[reportPrivateUsage]
    )

    destination = _resolve_out_file(out_file)
    if isinstance(destination, dict):
        return destination
    if destination is None:
        return _bad_param("Missing Output", "'out_file' is required.")
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)

    def assemble() -> Any:
        paths = [resolve_assembly_input(value, profile=profile) for value in clips]
        return assemble_videos(
            paths,
            output,
            music=Path(music).expanduser() if music is not None else None,
            voiceover=Path(voiceover).expanduser() if voiceover is not None else None,
            clip_volume=clip_volume,
            music_volume=music_volume,
            voiceover_volume=voiceover_volume,
            duck=duck,
        )

    result = await asyncio.to_thread(assemble)
    return assembly_result_payload(result)
