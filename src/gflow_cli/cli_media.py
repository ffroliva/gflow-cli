"""`gflow media` subcommand group — move media to Flow Trash and restore (#1363)."""

from __future__ import annotations

import sys

import click

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    OutputFormat,
    _resolve_profile,
    output_format_option,
    run_with_handlers,
    spends_credits,
)
from gflow_cli.errors import ProblemDetails
from gflow_cli.services.media_trash import (
    MediaTrashResult,
    resolve_media_trash_target,
    restore_media_service,
    trash_media_service,
)


def _is_interactive() -> bool:
    return sys.stdin.isatty()


@spends_credits(False)
@click.group("media")
def media() -> None:
    """Manage Flow media items (trash, restore)."""


def _emit_result(result: MediaTrashResult, output_format: str, is_json: bool) -> None:
    if is_json or output_format == OutputFormat.JSON.value:
        json_output.emit(result.as_json())
    elif output_format == OutputFormat.ID.value:
        click.echo(result.media_id)
    else:
        if result.status == "trashed":
            msg = f"Moved media {result.media_id} to Flow's Trash (project: {result.project_id})."
            if result.restore_hint:
                msg += f"\n{result.restore_hint}"
            click.echo(msg)
        else:
            click.echo(
                f"Restored media {result.media_id} from Flow's Trash "
                f"(project: {result.project_id})."
            )


@spends_credits(False)
@media.command("trash")
@click.argument("media_id", metavar="ID")
@click.option(
    "--project",
    "project_id",
    default=None,
    help="Flow project ID; the ID is still resolved through the local catalog.",
)
@click.option(
    "--profile",
    default=None,
    help="Profile name to disambiguate or scope the lookup.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    default=False,
    help="Confirm trashing media without an interactive prompt.",
)
@output_format_option(default="table", help="Output format (json|table|id).")
def trash_subcommand(
    media_id: str,
    project_id: str | None,
    profile: str | None,
    yes: bool,
    output_format: str = "table",
    as_json: bool = False,
) -> None:
    """Move a generated media item to Flow's Trash over the wire.

    Requires confirmation (--yes in scripts/CI, or interactive [y/N] prompt).
    """
    if not media_id or not media_id.strip():
        raise click.BadParameter("ID must not be empty or blank.", param_hint="ID")
    is_json = as_json or output_format == OutputFormat.JSON.value

    async def _run() -> None:
        if project_id:
            resolved_media_id = media_id.strip()
            resolved_project_id = project_id.strip()
            resolved_profile = _resolve_profile(profile)
            kind = "media"
        else:
            asset = resolve_media_trash_target(media_id, profile=profile, route="media trash")
            resolved_media_id = asset.flow_media_id
            resolved_project_id = asset.flow_project_id
            resolved_profile = asset.profile_name
            kind = asset.kind.value

        if not yes:
            if not _is_interactive():
                if is_json:
                    problem = ProblemDetails(
                        type="https://gflow-cli.dev/errors/confirmation-required",
                        title="Confirmation required",
                        status=400,
                        detail="--yes is required when running non-interactively to trash media.",
                        charged="no",
                    )
                    json_output.emit(dict(problem))
                    sys.exit(2)
                click.echo(
                    "Error: Invalid value for '--yes': "
                    "--yes is required when running non-interactively to trash media.",
                    err=True,
                )
                sys.exit(2)
            prompt = (
                f"Move {kind} {resolved_media_id} in project {resolved_project_id} "
                f"(profile: {resolved_profile}) to Flow's Trash?"
            )
            if not click.confirm(prompt, default=False):
                click.echo("Aborted.")
                sys.exit(0)

        result = await trash_media_service(
            media_id, profile=profile, project_id=project_id, route="media trash"
        )
        _emit_result(result, output_format, is_json)

    run_with_handlers(
        _run,
        cli_command="media trash",
        as_json=is_json,
        output_format=output_format,
    )


@spends_credits(False)
@media.command("restore")
@click.argument("media_id", metavar="ID")
@click.option(
    "--project",
    "project_id",
    default=None,
    help="Flow project ID; the ID is still resolved through the local catalog.",
)
@click.option(
    "--profile",
    default=None,
    help="Profile name to disambiguate or scope the lookup.",
)
@output_format_option(default="table", help="Output format (json|table|id).")
def restore_subcommand(
    media_id: str,
    project_id: str | None,
    profile: str | None,
    output_format: str = "table",
    as_json: bool = False,
) -> None:
    """Restore a trashed media item from Flow's Trash over the wire."""
    if not media_id or not media_id.strip():
        raise click.BadParameter("ID must not be empty or blank.", param_hint="ID")
    is_json = as_json or output_format == OutputFormat.JSON.value

    async def _run() -> None:
        result = await restore_media_service(
            media_id, profile=profile, project_id=project_id, route="media restore"
        )
        _emit_result(result, output_format, is_json)

    run_with_handlers(
        _run,
        cli_command="media restore",
        as_json=is_json,
        output_format=output_format,
    )
