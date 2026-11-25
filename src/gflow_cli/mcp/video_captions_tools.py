"""Direct, credit-free MCP tools for local video transcription and captions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from gflow_cli.api.video import is_media_uuid
from gflow_cli.mcp import tools as common
from gflow_cli.mcp.out_file import OutFileParam
from gflow_cli.mcp.server import server
from gflow_cli.services.video_captions import (
    DEFAULT_CAPTION_STYLE,
    MAX_FONT_SIZE,
    CaptionPosition,
    caption_video,
    reject_explicit_out_file_under_cloud_storage,
    result_payload,
    transcribe_video,
)


def _normalize_caption_position(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


CaptionPositionParam = Annotated[CaptionPosition, BeforeValidator(_normalize_caption_position)]


def _profile(file: str, profile: str | None) -> str | None | dict[str, Any]:
    if not is_media_uuid(file) or profile is None:
        return None
    return common._resolve_and_validate_profile(profile)  # pyright: ignore[reportPrivateUsage]


def _output(out_file: str | None, default_name: str) -> str | dict[str, Any]:
    reject_explicit_out_file_under_cloud_storage(out_file, "out_file")
    path = common._resolve_out_file(out_file, local_only=True)  # pyright: ignore[reportPrivateUsage]
    if path is not None:
        return path
    return common._confined_output_file(default_name)  # pyright: ignore[reportPrivateUsage]


def _result(result: Any) -> dict[str, Any]:
    return result_payload(result)


@server.tool(
    name="gflow_video_transcribe",
    description=(
        "Transcribe a local video or catalog video UUID to an .srt file using the configured "
        "OpenAI-compatible audio transcription endpoint. Credit-free; output stays inside "
        "the configured output directory. Google’s default endpoint is refused before audio "
        "is sent."
    ),
)
@common._guarded  # pyright: ignore[reportPrivateUsage]
async def gflow_video_transcribe(
    file: str,
    out_file: OutFileParam = None,
    language: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Transcribe FILE and return the SRT path and timed segments."""
    resolved_profile = _profile(file, profile)
    if isinstance(resolved_profile, dict):
        return resolved_profile
    output = _output(out_file, f"{Path(file).stem}.srt")
    if isinstance(output, dict):
        return output
    result = await asyncio.to_thread(
        transcribe_video, file, out_file=Path(output), language=language, profile=resolved_profile
    )
    return _result(result)


@server.tool(
    name="gflow_video_captions",
    description=(
        "Burn an authored SRT file into a local video or transcribe first when srt is omitted. "
        "Supports optional position ('bottom', 'top', 'middle'), margin (pixels from edge), "
        "font_size (pixels), and box_opacity (0.0-1.0). Credit-free; generated .mp4 and "
        "automatic .srt outputs stay inside the configured output directory."
    ),
)
@common._guarded  # pyright: ignore[reportPrivateUsage]
async def gflow_video_captions(
    file: str,
    srt: str | None = None,
    language: str | None = None,
    out_file: OutFileParam = None,
    profile: str | None = None,
    position: CaptionPositionParam = DEFAULT_CAPTION_STYLE.position,
    margin: Annotated[int, Field(ge=0)] = DEFAULT_CAPTION_STYLE.margin,
    font_size: Annotated[int | None, Field(ge=1, le=MAX_FONT_SIZE)] = (
        DEFAULT_CAPTION_STYLE.font_size
    ),
    box_opacity: Annotated[float | None, Field(ge=0.0, le=1.0)] = (
        DEFAULT_CAPTION_STYLE.box_opacity
    ),
) -> dict[str, Any]:
    """Burn SRT captions into FILE, optionally transcribing first."""
    resolved_profile = _profile(file, profile)
    if isinstance(resolved_profile, dict):
        return resolved_profile
    output = _output(out_file, f"{Path(file).stem}-captions.mp4")
    if isinstance(output, dict):
        return output
    srt_output: Path | None = None
    if not srt:
        resolved_srt = _output(None, f"{Path(file).stem}.srt")
        if isinstance(resolved_srt, dict):
            return resolved_srt
        srt_output = Path(resolved_srt)
    result = await asyncio.to_thread(
        caption_video,
        file,
        srt_path=Path(srt) if srt else None,
        srt_out_file=srt_output,
        out_file=Path(output),
        language=language,
        profile=resolved_profile,
        position=position,
        margin=margin,
        font_size=font_size,
        box_opacity=box_opacity,
    )
    return _result(result)
