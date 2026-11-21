"""Local speech synthesis commands."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    OutputFormat,
    output_format_option,
    run_with_handlers,
    spends_credits,
)
from gflow_cli.services.audio_narration import NarrationResult, Provider, narrate


def _payload(result: NarrationResult) -> dict[str, Any]:
    return {
        "out_file": str(result.out_file),
        "provider": result.provider,
        "media_type": result.media_type,
    }


@click.group()
def audio() -> None:
    """Create local narration audio from text."""


@spends_credits(False)
@audio.command("narrate")
@click.argument("text", required=False)
@click.option(
    "--text-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Read narration text from a UTF-8 file instead of TEXT.",
)
@click.option(
    "--out-file",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the synthesized audio here; existing files are never overwritten.",
)
@click.option(
    "--provider",
    type=click.Choice(("edge", "gemini"), case_sensitive=False),
    default="edge",
    show_default=True,
    help=(
        "Speech provider. Narration text is sent to the selected third-party provider; "
        "no Flow credits are used."
    ),
)
@click.option("--voice", help="Provider voice ID.")
@click.option(
    "--rate", default="+0%", show_default=True, help="Speech rate adjustment (edge only)."
)
@click.option(
    "--pitch", default="+0Hz", show_default=True, help="Speech pitch adjustment (edge only)."
)
@output_format_option(has_out_file=True)
def narrate_command(
    text: str | None,
    text_file: Path | None,
    out_file: Path,
    provider: Provider,
    voice: str | None,
    rate: str,
    pitch: str,
    as_json: bool,
    output_format: str = "table",
) -> None:
    """Synthesize TEXT (or --text-file) into a local audio file.

    Narration text is sent to the selected third-party provider. This does not use Flow credits.
    """
    if text is not None and text_file is not None:
        raise click.UsageError("TEXT and --text-file are mutually exclusive.")
    if text is None and text_file is None:
        raise click.UsageError("Provide TEXT or --text-file.")
    is_json = as_json or output_format == OutputFormat.JSON.value

    async def execute() -> None:
        result = await asyncio.to_thread(
            narrate,
            text=text,
            text_file=text_file,
            out_file=out_file,
            provider=provider,
            voice=voice,
            rate=rate,
            pitch=pitch,
        )
        if is_json:
            json_output.emit(_payload(result))
        else:
            click.echo(f"Narration saved to {result.out_file} ({result.provider}).")

    run_with_handlers(
        execute, cli_command="audio narrate", as_json=is_json, output_format=output_format
    )
