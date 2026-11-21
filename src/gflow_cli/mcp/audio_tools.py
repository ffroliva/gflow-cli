"""Direct, credit-free MCP tool for local text-to-speech narration."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from gflow_cli.mcp import tools as common
from gflow_cli.mcp.server import server
from gflow_cli.services.audio_narration import narrate


@server.tool(
    name="gflow_narrate",
    description=(
        "Synthesize a local narration audio file. Narration text is sent to the selected "
        "provider (Microsoft Edge Read Aloud or Google Gemini); this uses no Flow credits. "
        "Gemini requires GFLOW_CLI_GEMINI_TTS_API_KEY. Output paths stay inside the configured "
        "output directory."
    ),
)
@common._guarded  # pyright: ignore[reportPrivateUsage]
async def gflow_narrate(
    text: str | None = None,
    *,
    out_file: str,
    provider: Literal["edge", "gemini"] = "edge",
    voice: str | None = None,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> dict[str, Any]:
    """Create a narration file from text."""
    output = common._resolve_out_file(out_file)  # pyright: ignore[reportPrivateUsage]
    if isinstance(output, dict):
        return output
    if output is None:
        return common._bad_param("Invalid Output", "out_file is required.")  # pyright: ignore[reportPrivateUsage]
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = await asyncio.to_thread(
        narrate,
        text=text,
        out_file=out_path,
        provider=provider,
        voice=voice,
        rate=rate,
        pitch=pitch,
    )
    return {
        "status": "ok",
        "out_file": str(result.out_file),
        "provider": result.provider,
        "media_type": result.media_type,
    }
