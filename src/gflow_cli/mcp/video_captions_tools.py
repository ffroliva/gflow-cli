"""Direct, credit-free MCP tools for local video transcription and captions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from gflow_cli.api.video import is_media_uuid
from gflow_cli.config import get_settings
from gflow_cli.mcp import tools as common
from gflow_cli.mcp.out_file import OutFileParam
from gflow_cli.mcp.server import server
from gflow_cli.services.video_captions import caption_video, transcribe_video


def _profile(file: str, profile: str | None) -> str | None | dict[str, Any]:
    if not is_media_uuid(file) or profile is None:
        return None
    return common._resolve_and_validate_profile(profile)  # pyright: ignore[reportPrivateUsage]


def _output(out_file: str | None, default_name: str) -> str | dict[str, Any]:
    path = common._resolve_out_file(out_file, local_only=True)  # pyright: ignore[reportPrivateUsage]
    if isinstance(path, dict):
        return path
    if path is not None:
        return path
    settings = get_settings()
    return str((settings.output_dir / default_name).resolve())


def _result(result: Any, *, transcribing: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "ok",
        "source_file": str(result.source_file),
        "srt_file": str(result.srt_file),
    }
    if transcribing:
        payload.update(
            language=result.language,
            duration_seconds=result.duration_seconds,
            segments_count=len(result.segments),
            segments=[
                {
                    "index": item.index,
                    "start_s": item.start_s,
                    "end_s": item.end_s,
                    "text": item.text,
                }
                for item in result.segments
            ],
        )
    else:
        payload.update(
            output_file=str(result.output_file),
            duration_seconds=result.duration_seconds,
            total_frames=result.total_frames,
            frames_burned=result.frames_burned,
            bytes=result.bytes,
        )
    return payload


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
    return _result(result, transcribing=True)


@server.tool(
    name="gflow_video_captions",
    description=(
        "Burn an authored SRT file into a local video or transcribe first when srt is omitted. "
        "Credit-free; generated .mp4 and automatic .srt outputs stay inside the configured "
        "output directory."
    ),
)
@common._guarded  # pyright: ignore[reportPrivateUsage]
async def gflow_video_captions(
    file: str,
    srt: str | None = None,
    language: str | None = None,
    out_file: OutFileParam = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Burn SRT captions into FILE, optionally transcribing first."""
    resolved_profile = _profile(file, profile)
    if isinstance(resolved_profile, dict):
        return resolved_profile
    output = _output(out_file, f"{Path(file).stem}-captions.mp4")
    if isinstance(output, dict):
        return output
    settings = get_settings()
    srt_output = None if srt else (settings.output_dir / f"{Path(file).stem}.srt").resolve()
    result = await asyncio.to_thread(
        caption_video,
        file,
        srt_path=Path(srt) if srt else None,
        srt_out_file=srt_output,
        out_file=Path(output),
        language=language,
        profile=resolved_profile,
    )
    return _result(result, transcribing=False)
