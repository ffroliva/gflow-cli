"""`gflow character` — manage Flow Character entities (list / show / voices)."""

from __future__ import annotations

from pathlib import Path

import click
import structlog
from rich.console import Console

from gflow_cli import json_output
from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile, run_with_handlers
from gflow_cli.api.character import VOICES, Character
from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import get_settings

console = Console()
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


@click.group()
def character() -> None:
    """Manage Flow Character entities for a project."""


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@character.command("list")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit JSON output.")
@click.option("--profile", default=None, help="Profile name (overrides default).")
def list_cmd(project_id: str, as_json: bool, profile: str | None) -> None:
    """List all Character entities in a project."""
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_list(
            profile_dir=pdir,
            headless=settings.headless,
            project_id=project_id,
            as_json=as_json,
        ),
        cli_command="character list",
        as_json=as_json,
    )


async def _run_list(*, profile_dir: Path, headless: bool, project_id: str, as_json: bool) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        chars = await client.list_characters(project_id)
    if as_json:
        json_output.emit({"status": "ok", "characters": [_char_to_dict(c) for c in chars]})
    else:
        if not chars:
            console.print("[dim]No characters found.[/dim]")
            return
        for c in chars:
            _render_character_line(c)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@character.command("show")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.option("--id", "entity_id", default=None, help="Character entity id.")
@click.option("--name", "name", default=None, help="Character display name (exact match).")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit JSON output.")
@click.option("--profile", default=None, help="Profile name (overrides default).")
def show(
    project_id: str,
    entity_id: str | None,
    name: str | None,
    as_json: bool,
    profile: str | None,
) -> None:
    """Show a single Character by --id or --name.

    Exactly one of --id or --name must be supplied.
    An ambiguous name (multiple characters share it) exits with code 11.
    """
    if entity_id is None and name is None:
        raise click.UsageError("Provide either --id or --name.")
    if entity_id is not None and name is not None:
        raise click.UsageError("--id and --name are mutually exclusive.")
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_show(
            profile_dir=pdir,
            headless=settings.headless,
            project_id=project_id,
            entity_id=entity_id,
            name=name,
            as_json=as_json,
        ),
        cli_command="character show",
        as_json=as_json,
    )


async def _run_show(
    *,
    profile_dir: Path,
    headless: bool,
    project_id: str,
    entity_id: str | None,
    name: str | None,
    as_json: bool,
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        char = await client.get_character(project_id, entity_id=entity_id, name=name)
    if as_json:
        json_output.emit({"status": "ok", "character": _char_to_dict(char)})
    else:
        _render_character_detail(char)


# ---------------------------------------------------------------------------
# voices
# ---------------------------------------------------------------------------


@character.command("voices")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit JSON output.")
def voices(as_json: bool) -> None:
    """List preset voice ids available for Character TTS."""
    if as_json:
        json_output.emit({"status": "ok", "voices": list(VOICES)})
    else:
        for v in VOICES:
            console.print(v)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _char_to_dict(c: Character) -> dict[str, object]:
    return {
        "entity_id": c.entity_id,
        "display_name": c.display_name,
        "project_id": c.project_id,
        "workflow_ids": list(c.workflow_ids),
        "voice": c.voice,
        "personality": c.personality,
        "thumbnail_media_id": c.thumbnail_media_id,
    }


def _render_character_line(c: Character) -> None:
    wf_count = len(c.workflow_ids)
    voice_str = c.voice or "-"
    console.print(
        f"[bold]{c.display_name}[/bold]  "
        f"[dim]{c.entity_id}[/dim]  "
        f"voice={voice_str}  "
        f"refs={wf_count}"
    )


def _render_character_detail(c: Character) -> None:
    console.print(f"[bold green]Character:[/bold green] [bold]{c.display_name}[/bold]")
    console.print(f"  entity_id:  {c.entity_id}")
    console.print(f"  project_id: {c.project_id}")
    console.print(f"  voice:      {c.voice or '-'}")
    console.print(f"  personality:{c.personality or '-'}")
    console.print(f"  refs ({len(c.workflow_ids)}):")
    for wf in c.workflow_ids:
        console.print(f"    {wf}")
