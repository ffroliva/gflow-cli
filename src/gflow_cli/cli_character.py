"""`gflow character` — manage Flow Character entities (list / show / voices)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import click
import structlog
from rich.console import Console

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    output_format_option,
    run_with_handlers,
    spends_credits,
)
from gflow_cli.api.character import (
    VOICES,
    Character,
    CharacterCreateResult,
    CharacterImageRequest,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import get_settings
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.services.character_create import character_create, character_create_from_files

console = Console()
log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Voice validation
# ---------------------------------------------------------------------------


def _normalize_voice(value: str | None) -> str | None:
    """Validate ``value`` against the voice catalog case-insensitively.

    Returns the canonical Capitalized voice name (so ``charon`` and ``Charon``
    both normalize to ``"Charon"``), or ``None`` when ``value`` is ``None``.

    Raises:
        click.BadParameter: when ``value`` is not a known voice. The message is
            language-agnostic and points at ``gflow character voices``.
    """
    if value is None:
        return None
    by_lower = {v.name.lower(): v.name for v in VOICES}
    canonical = by_lower.get(value.strip().lower())
    if canonical is None:
        raise click.BadParameter(
            f"unknown voice: {value!r} — run `gflow character voices` for valid names",
            param_hint="--voice",
        )
    return canonical


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


@click.group()
def character() -> None:
    """Manage Flow Character entities for a project."""


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@spends_credits(True)
@character.command("create")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.option("--name", required=True, help="Display name for the new character.")
@click.option(
    "--face-prompt",
    default=None,
    help="Prompt for the face reference image. Required unless --image is given.",
)
@click.option(
    "--image",
    "image",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Build the character from this local image as its portrait instead of generating "
        "one (flow.google.com). Nothing is generated: no credits, no image quota."
    ),
)
@click.option(
    "--body-image",
    "body_image",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Local image for the body slot; only with --image.",
)
@click.option(
    "--body-prompt",
    default=None,
    help=(
        "Body/clothing DESCRIPTION (optional). gflow wraps it in a "
        "self-contained front/side/back triptych instruction and seeds it with "
        "the generated face, so one generation yields all three angles. This is "
        "a description of the body and outfit, not a full prompt."
    ),
)
@click.option(
    "--voice",
    default=None,
    help="Preset voice name (e.g. Charon); case-insensitive. See `gflow character voices`.",
)
@click.option("--personality", default=None, help="Personality notes for the character.")
@click.option(
    "--model",
    type=click.Choice(["nano2", "nanopro"], case_sensitive=False),
    default="nano2",
    show_default=True,
    help="Character model: nano2 (Nano Banana 2, default) or nanopro (Nano Banana Pro).",
)
@click.option(
    "--format-prompt",
    is_flag=True,
    default=False,
    help=(
        "Click Flow's in-editor Format button and wait for Flow to rewrite the "
        "prompt into its character prompt-engineering shape before submitting. "
        "Flow rewrites server-side, so this adds a few seconds, and the reshaped "
        "prompt is longer and more detailed — which makes the generation itself "
        "slower too (measured: roughly double). Best-effort: if the button is "
        "missing, or the rewrite does not arrive in time, the prompt is submitted "
        "as typed and a warning names which of the two happened."
    ),
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--locale",
    default=None,
    help="BCP-47 locale for the character editor URL. Defaults to the ACCOUNT's own "
    "locale, resolved live from Flow (#580) — a hardcoded default sends non-en "
    "accounts to the wrong route and Flow bounces them back mid-prompt.",
)
@output_format_option()
def create(  # NOSONAR - one parameter per Click option
    project_id: str,
    name: str,
    face_prompt: str | None,
    image: Path | None,
    body_image: Path | None,
    body_prompt: str | None,
    voice: str | None,
    personality: str | None,
    model: str,
    format_prompt: bool,
    profile: str | None,
    locale: str | None,
    output_format: str,
    as_json: bool,
) -> None:
    """Create a new Character entity in a project.

    Generates face (and optionally body) reference images, then persists and
    patches the entity.  Uses the persist-before-spend saga so a crashed run
    is recoverable.

    With --image (and optionally --body-image) the character is built from local
    files instead: uploaded through Flow's character editor on flow.google.com, then
    named and given the voice and personality. Nothing is generated.
    """
    _check_create_sources(face_prompt, image, body_image, body_prompt, format_prompt, locale)
    voice = _normalize_voice(voice)
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    if image is not None:
        portrait = image
        run_with_handlers(
            lambda: _run_create_from_files(
                profile_name=profile_name,
                profile_dir=pdir,
                headless=settings.headless,
                project_id=project_id,
                name=name,
                portrait=portrait,
                body=body_image,
                voice=voice,
                personality=personality,
                output_format=output_format,
                settings=settings,
            ),
            cli_command="character create",
            as_json=as_json,
            output_format=output_format,
        )
        return
    face = cast("str", face_prompt)  # _check_create_sources: no --image means a face prompt
    run_with_handlers(
        lambda: _run_create(
            profile_name=profile_name,
            profile_dir=pdir,
            headless=settings.headless,
            project_id=project_id,
            name=name,
            face_prompt=face,
            body_prompt=body_prompt,
            voice=voice,
            personality=personality,
            model=model,
            format_prompt=format_prompt,
            locale=locale,
            output_format=output_format,
            as_json=as_json,
            settings=settings,
        ),
        cli_command="character create",
        as_json=as_json,
        output_format=output_format,
    )


async def _run_create(
    *,
    profile_name: str,
    profile_dir: Path,
    headless: bool,
    project_id: str,
    name: str,
    face_prompt: str,
    body_prompt: str | None,
    voice: str | None,
    personality: str | None,
    model: str,
    format_prompt: bool,
    locale: str | None,
    output_format: str = "table",
    as_json: bool = False,
    settings: object,
) -> None:
    face = CharacterImageRequest(
        prompt=face_prompt,
        model=model,
        image_reference_index=0,
    )
    body: CharacterImageRequest | None = None
    if body_prompt is not None:
        body = CharacterImageRequest(
            prompt=body_prompt,
            model=model,
            image_reference_index=1,
        )

    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        recorder = OperationRecorder.open(settings)  # type: ignore[arg-type]
        try:
            result: CharacterCreateResult = await character_create(
                client,
                recorder,
                profile_name=profile_name,
                profile_dir=profile_dir,
                project_id=project_id,
                name=name,
                face=face,
                body=body,
                voice=voice,
                personality=personality,
                locale=locale,
                format_prompt=format_prompt,
            )
        finally:
            recorder.close()
    _emit_created(result, output_format)


def _check_create_sources(
    face_prompt: str | None,
    image: Path | None,
    body_image: Path | None,
    body_prompt: str | None,
    format_prompt: bool,
    locale: str | None,
) -> None:
    """Exactly one portrait source; the body source must match it; generation-only flags
    are refused on a file-built character instead of being silently ignored."""
    if (face_prompt is None) == (image is None):
        raise click.UsageError("give exactly one of --face-prompt or --image")
    if image is None:
        if body_image is not None:
            raise click.UsageError(
                "--body-image needs --image (a generated face takes --body-prompt)"
            )
        return
    if body_prompt is not None:
        raise click.UsageError(
            "a character built from --image takes --body-image, not --body-prompt"
        )
    source = click.get_current_context().get_parameter_source("model")
    if format_prompt or source is click.core.ParameterSource.COMMANDLINE:
        raise click.UsageError(
            "--format-prompt and --model apply to generated images; --image generates nothing"
        )
    if locale is not None:
        raise click.UsageError(
            "--locale applies to the generated-face editor; --image does not use it"
        )


async def _run_create_from_files(
    *,
    profile_name: str,
    profile_dir: Path,
    headless: bool,
    project_id: str,
    name: str,
    portrait: Path,
    body: Path | None,
    voice: str | None,
    personality: str | None,
    output_format: str,
    settings: object,
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        recorder = OperationRecorder.open(settings)  # type: ignore[arg-type]
        try:
            result = await character_create_from_files(
                client,
                recorder,
                profile_name=profile_name,
                profile_dir=profile_dir,
                project_id=project_id,
                name=name,
                portrait=portrait,
                body=body,
                voice=voice,
                personality=personality,
            )
        finally:
            recorder.close()
    _emit_created(result, output_format)


def _emit_created(result: CharacterCreateResult, output_format: str) -> None:
    if output_format == "json":
        json_output.emit(
            {
                "status": "ok",
                "character": {
                    "entity_id": result.entity_id,
                    "project_id": result.project_id,
                    "name": result.name,
                    "workflow_ids": list(result.workflow_ids),
                    "primary_media_ids": list(result.primary_media_ids),
                    "voice": result.voice,
                    "image_paths": [str(p) if p is not None else None for p in result.image_paths],
                },
            }
        )
    elif output_format == "id":
        click.echo(result.entity_id)
    else:
        console.print(f"[bold green]Character created:[/bold green] {result.entity_id}")
        for wf_id in result.workflow_ids:
            console.print(f"  workflow: {wf_id}")
        # Human-readable saved-image lines (slot 0 = face, slot 1 = body).
        slot_labels = ("face", "body")
        for slot, path in enumerate(result.image_paths):
            label = slot_labels[slot] if slot < len(slot_labels) else f"slot{slot}"
            console.print(f"  {label}: {path if path is not None else '(not saved)'}")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@spends_credits(False)
@character.command("list")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@output_format_option()
@click.option("--profile", default=None, help="Profile name (overrides default).")
def list_cmd(project_id: str, output_format: str, as_json: bool, profile: str | None) -> None:
    """List all Character entities in a project."""
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_list(
            profile_dir=pdir,
            headless=settings.headless,
            project_id=project_id,
            output_format=output_format,
            as_json=as_json,
        ),
        cli_command="character list",
        as_json=as_json,
        output_format=output_format,
    )


async def _run_list(
    *,
    profile_dir: Path,
    headless: bool,
    project_id: str,
    output_format: str = "table",
    as_json: bool = False,
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        chars = await client.list_characters(project_id)
    if output_format == "json":
        json_output.emit({"status": "ok", "characters": [_char_to_dict(c) for c in chars]})
    elif output_format == "id":
        for c in chars:
            click.echo(c.entity_id)
    else:
        if not chars:
            console.print("[dim]No characters found.[/dim]")
            return
        for c in chars:
            _render_character_line(c)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@spends_credits(False)
@character.command("show")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.option("--id", "entity_id", default=None, help="Character entity id.")
@click.option("--name", "name", default=None, help="Character display name (exact match).")
@output_format_option()
@click.option("--profile", default=None, help="Profile name (overrides default).")
def show(
    project_id: str,
    entity_id: str | None,
    name: str | None,
    output_format: str,
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
            output_format=output_format,
            as_json=as_json,
        ),
        cli_command="character show",
        as_json=as_json,
        output_format=output_format,
    )


async def _run_show(
    *,
    profile_dir: Path,
    headless: bool,
    project_id: str,
    entity_id: str | None,
    name: str | None,
    output_format: str = "table",
    as_json: bool = False,
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        char = await client.get_character(project_id, entity_id=entity_id, name=name)
    if output_format == "json":
        json_output.emit({"status": "ok", "character": _char_to_dict(char)})
    elif output_format == "id":
        click.echo(char.entity_id)
    else:
        _render_character_detail(char)


# ---------------------------------------------------------------------------
# rm
# ---------------------------------------------------------------------------


@spends_credits(False)
@character.command("rm")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.option("--id", "entity_id", default=None, help="Character entity id.")
@click.option("--name", "name", default=None, help="Character display name (exact match).")
@click.option("--yes", "-y", "assume_yes", is_flag=True, default=False, help="Skip confirmation.")
@output_format_option()
@click.option("--profile", default=None, help="Profile name (overrides default).")
def rm(
    project_id: str,
    entity_id: str | None,
    name: str | None,
    assume_yes: bool,
    output_format: str,
    as_json: bool,
    profile: str | None,
) -> None:
    """Delete a Character by --id or --name.

    Exactly one of --id or --name must be supplied.  An ambiguous name (multiple
    characters share it) exits with code 11; use --id to disambiguate.  FREE —
    no reCAPTCHA, no credit.
    """
    if entity_id is None and name is None:
        raise click.UsageError("Provide either --id or --name.")
    if entity_id is not None and name is not None:
        raise click.UsageError("--id and --name are mutually exclusive.")
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_rm(
            profile_dir=pdir,
            headless=settings.headless,
            project_id=project_id,
            entity_id=entity_id,
            name=name,
            assume_yes=assume_yes,
            output_format=output_format,
            as_json=as_json,
        ),
        cli_command="character rm",
        as_json=as_json,
        output_format=output_format,
    )


async def _run_rm(
    *,
    profile_dir: Path,
    headless: bool,
    project_id: str,
    entity_id: str | None,
    name: str | None,
    assume_yes: bool,
    output_format: str = "table",
    as_json: bool = False,
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        char = await client.get_character(project_id, entity_id=entity_id, name=name)
        if not assume_yes and not as_json and output_format != "id":
            click.confirm(
                f"Delete character {char.display_name!r} ({char.entity_id})?",
                abort=True,
            )
        await client.delete_characters(project_id, [char.entity_id])
    if output_format == "json":
        json_output.emit(
            {
                "status": "ok",
                "deleted": {
                    "entity_id": char.entity_id,
                    "display_name": char.display_name,
                },
            }
        )
    elif output_format == "id":
        click.echo(char.entity_id)
    else:
        click.echo(f"Deleted character {char.display_name!r} ({char.entity_id}).")


# ---------------------------------------------------------------------------
# voices
# ---------------------------------------------------------------------------


@spends_credits(False)
@character.command("voices")
@output_format_option()
def voices(output_format: str, as_json: bool) -> None:
    """List preset voices available for Character TTS."""
    if output_format == "json":
        json_output.emit(
            {
                "status": "ok",
                "voices": [
                    {"name": v.name, "description": v.description, "sample_url": v.sample_url}
                    for v in VOICES
                ],
            }
        )
    elif output_format == "id":
        for v in VOICES:
            click.echo(v.name)
    else:
        width = max(len(v.name) for v in VOICES)
        for v in VOICES:
            desc = v.description or "-"
            console.print(f"{v.name:<{width}}  {desc}  (sample: {v.sample_url})")


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
