"""Credit-free local video transcription and caption commands."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click
import structlog
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    OutputFormat,
    out_file_option,
    output_format_option,
    run_with_handlers,
    spends_credits,
)
from gflow_cli.services.video_captions import caption_video, result_payload, transcribe_video

console = Console()
log = structlog.get_logger(__name__)


def _payload(result: Any) -> dict[str, Any]:
    return result_payload(result)


def _emit(result: Any, *, as_json: bool, output_format: str) -> None:
    payload = _payload(result)
    path = result.srt_file if hasattr(result, "segments") else result.output_file
    if output_format == OutputFormat.ID.value:
        click.echo(path)
    elif as_json or output_format == OutputFormat.JSON.value:
        json_output.emit(payload)
    else:
        table = Table(
            title="Video transcription" if hasattr(result, "segments") else "Video captions"
        )
        for key, value in payload.items():
            if key != "segments":
                table.add_row(key.replace("_", " ").title(), escape(str(value)))
        console.print(table)


@spends_credits(False)
@click.command("transcribe")
@click.argument("file")
@out_file_option(help="Explicit output .srt file path (defaults to <name>.srt).")
@click.option("--language", default=None, help="ISO-639-1 language hint for transcription.")
@click.option("--profile", default=None, help="Profile name when FILE is a catalog media UUID.")
@output_format_option(has_out_file=True)
def transcribe(
    file: str,
    output_file: Path | None,
    language: str | None,
    profile: str | None,
    as_json: bool,
    output_format: str = "table",
) -> None:
    """Transcribe a local or catalog video FILE to SubRip (.srt)."""
    is_json = as_json or output_format == OutputFormat.JSON.value

    async def execute() -> None:
        result = await asyncio.to_thread(
            transcribe_video, file, out_file=output_file, language=language, profile=profile
        )
        _emit(result, as_json=is_json, output_format=output_format)

    run_with_handlers(
        execute, cli_command="video transcribe", as_json=is_json, output_format=output_format
    )


@spends_credits(False)
@click.command("captions")
@click.argument("file")
@click.option(
    "--srt",
    "srt",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Existing .srt subtitle file. Omit to transcribe FILE first.",
)
@click.option(
    "--language", default=None, help="ISO-639-1 hint when transcribing (ignored with --srt)."
)
@out_file_option(help="Explicit output .mp4 file path (defaults to <name>-captions.mp4).")
@click.option("--profile", default=None, help="Profile name when FILE is a catalog media UUID.")
@output_format_option(has_out_file=True)
def captions(
    file: str,
    srt: Path | None,
    language: str | None,
    output_file: Path | None,
    profile: str | None,
    as_json: bool,
    output_format: str = "table",
) -> None:
    """Burn captions into a local video FILE (no Flow credits)."""
    is_json = as_json or output_format == OutputFormat.JSON.value

    async def execute() -> None:
        result = await asyncio.to_thread(
            caption_video,
            file,
            srt_path=srt,
            out_file=output_file,
            language=language,
            profile=profile,
        )
        _emit(result, as_json=is_json, output_format=output_format)

    run_with_handlers(
        execute, cli_command="video captions", as_json=is_json, output_format=output_format
    )
