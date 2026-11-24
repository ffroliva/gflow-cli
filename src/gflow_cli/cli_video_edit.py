"""`gflow video edit` command — video-to-video editing on ``flow.google.com``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import structlog
from rich.console import Console
from rich.markup import escape

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    OutputFormat,
    _make_provider_dir,
    _resolve_profile,
    _validate_project_id,
    apply_tool_option,
    default_project,
    out_file_option,
    output_format_option,
    run_with_handlers,
    spends_credits,
    tool_option,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.credit_cap import resolve_max_credits as _resolve_max_credits
from gflow_cli.api.video import (
    MAX_EDIT_REFERENCE_IMAGES,
    Aspect,
    EditVideoRequest,
    VideoModel,
    VideoStarted,
    is_media_uuid,
)
from gflow_cli.cli_image import lookup_project_in_catalog
from gflow_cli.cli_video_reporting import _relocate_and_record_completed_video
from gflow_cli.config import get_settings
from gflow_cli.data.models import OperationKind
from gflow_cli.data.recorder import OperationRecorder, record_failed_operation_safe
from gflow_cli.errors import DataStoreError
from gflow_cli.storage import refuse_cloud_storage_for_local_file

if TYPE_CHECKING:
    from gflow_cli.api.video import VideoResult

console = Console()
logger = structlog.get_logger(__name__)


def _set_no_max_credits(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if value:
        ctx.meta["no_max_credits"] = True


def _resolve_edit_max_credits(cli_value: int | None, configured: int | None) -> int | None:
    ctx = click.get_current_context(silent=True)
    if ctx is not None and ctx.meta.get("no_max_credits"):
        configured = None
    return _resolve_max_credits(cli_value, configured)


def _classify_source(source: str) -> tuple[Path | None, str | None]:
    if is_media_uuid(source):
        return None, source
    path = Path(source).expanduser()
    if not path.is_file():
        raise click.BadParameter(
            f"SOURCE {source!r} is neither a Flow media UUID nor an existing local video file.",
            param_hint="SOURCE",
        )
    return path.resolve(), None


def _emit_edit_result(
    *,
    request: EditVideoRequest,
    result: VideoResult,
    output_format: str,
    is_json: bool,
) -> None:
    if output_format == OutputFormat.ID.value:
        if not result.status.succeeded:
            reasons = (
                ", ".join(result.status.failure_reasons)
                or result.status.error_message
                or "unknown reason"
            )
            Console(stderr=True).print(f"[red]Video edit failed:[/red] {escape(reasons)}")
            raise SystemExit(1)
        if result.status.media_id:
            click.echo(result.status.media_id)
        return
    if is_json or output_format == OutputFormat.JSON.value:
        json_output.emit(
            json_output.edit_video_result(command="video edit", request=request, result=result)
        )
        if not result.status.succeeded:
            raise SystemExit(1)
        return
    if not result.status.succeeded:
        reasons = (
            ", ".join(result.status.failure_reasons)
            or result.status.error_message
            or "unknown reason"
        )
        console.print(f"[red]Video edit failed:[/red] {escape(reasons)}")
        raise SystemExit(1)
    console.print(f"[bold green]Saved:[/bold green] {result.local_path}")


async def _edit_and_report(
    request: EditVideoRequest,
    *,
    profile_name: str,
    profile_dir: Path,
    out_dir: Path | None,
    output_file: Path | None,
    as_json: bool,
    output_format: str,
    project_id: str | None,
    project_name: str | None,
) -> None:
    settings = get_settings()
    is_json = as_json or output_format == OutputFormat.JSON.value
    quiet_output = is_json or output_format == OutputFormat.ID.value
    if output_file is not None:
        refuse_cloud_storage_for_local_file(settings.storage_uri, "--out-file")
    if not quiet_output:
        console.print("[dim]Editing video — this takes ~2 minutes…[/dim]")
    recorder = OperationRecorder.open(settings)
    started_media_ids: list[str] = []
    try:
        async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:
            if project_id is None and project_name is not None:
                p_info = await client.create_project(title=project_name)
                project_id = p_info.project_id

            def on_started(started: VideoStarted) -> None:
                started_media_ids.append(started.media_id)
                try:
                    recorder.record_started_video(
                        profile_name=profile_name,
                        profile_dir=profile_dir,
                        request=request,
                        started=started,
                    )
                except DataStoreError as exc:
                    logger.warning("data.persistence_failed_after_success", detail=str(exc))

            result = await client.edit_video(
                req=request,
                project_id=project_id,
                out_dir=out_dir,
                download=True,
                on_started=on_started,
            )

        result = _relocate_and_record_completed_video(
            recorder,
            profile_name=profile_name,
            profile_dir=profile_dir,
            request=request,
            result=result,
            output_file=output_file,
            on_error=lambda exc, _res: logger.warning(
                "data.persistence_failed_after_success", detail=str(exc)
            ),
        )
    except Exception as exc:
        record_failed_operation_safe(
            recorder,
            logger=logger,
            profile_name=profile_name,
            profile_dir=profile_dir,
            command="video edit",
            mode=OperationKind.EDIT,
            exc=exc,
            request=request,
            flow_project_id=project_id,
            flow_media_ids=started_media_ids,
        )
        raise
    finally:
        recorder.close()

    _emit_edit_result(
        request=request,
        result=result,
        output_format=output_format,
        is_json=is_json,
    )


@spends_credits(True)
@click.command("edit")
@click.argument("source")
@click.argument("prompt")
@click.option(
    "--ref",
    "refs",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help=f"Reference image file for editing (repeatable, up to {MAX_EDIT_REFERENCE_IMAGES}).",
)
@click.option(
    "--model",
    default="omni-1.2",
    show_default=True,
    type=click.Choice(["omni-1.2", "omni-1.2-flash", "omni-flash"]),
    help="Video edit model (Omni models only; default omni-1.2).",
)
@click.option(
    "--aspect",
    default=None,
    type=click.Choice(["9:16", "16:9"]),
    help="Output aspect ratio (local video files only; omit for Flow's default).",
)
@click.option(
    "--resolution",
    default=None,
    type=click.Choice(["360p", "720p"], case_sensitive=False),
    help="Output resolution ('360p' or '720p'; 360p requires a local video file).",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--max-credits",
    type=click.IntRange(min=0),
    default=None,
    help="Refuse before submit if Flow's quote exceeds this cap (default: GFLOW_CLI_MAX_CREDITS).",
)
@click.option(
    "--no-max-credits",
    is_flag=True,
    expose_value=False,
    callback=_set_no_max_credits,
    help="Ignore GFLOW_CLI_MAX_CREDITS for this command.",
)
@tool_option
@click.option(
    "--project",
    "project_id",
    default=None,
    callback=_validate_project_id,
    help="Existing Flow project id (required when SOURCE is an uncatalogued media UUID).",
)
@click.option(
    "--project-name",
    "--project-title",
    "project_name",
    default=None,
    envvar="GFLOW_CLI_PROJECT_NAME",
    type=str,
    help="Human-readable project title to use when creating a fresh Flow project.",
)
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the edited mp4. Defaults to tmp/.",
)
@out_file_option(help="Explicit output file path for the edited video.")
@output_format_option(has_out_file=True)
def edit(
    source: str,
    prompt: str,
    refs: tuple[Path, ...],
    model: str,
    aspect: str | None,
    resolution: str | None,
    profile: str | None,
    max_credits: int | None,
    tool_specs: tuple[str, ...],
    project_id: str | None,
    project_name: str | None,
    out_dir: Path | None,
    output_file: Path | None,
    as_json: bool,
    output_format: str = "table",
) -> None:
    """Edit a local video file or Flow clip UUID with PROMPT and optional --ref images."""
    if len(refs) > MAX_EDIT_REFERENCE_IMAGES:
        raise click.UsageError(
            f"video edit accepts at most {MAX_EDIT_REFERENCE_IMAGES} --ref images "
            f"(got {len(refs)}); Flow silently drops extras past {MAX_EDIT_REFERENCE_IMAGES}."
        )
    source_video, source_media_id = _classify_source(source)
    profile_name = _resolve_profile(profile)
    project_id = default_project(project_id, project_name, profile_name)
    if source_media_id is not None and project_id is None:
        project_id = lookup_project_in_catalog(media_id=source_media_id, profile_name=profile_name)
        if project_id is None:
            raise click.UsageError(
                "Editing an existing Flow media UUID requires --project <id> "
                "(not found in the local catalog)."
            )
    is_json = as_json or output_format == OutputFormat.JSON.value
    quiet_output = is_json or output_format == OutputFormat.ID.value
    prompt_to_send, original_prompt, applied_tool = apply_tool_option(
        prompt, tool_specs, category="video", quiet=quiet_output
    )
    settings = get_settings()
    effective_max = _resolve_edit_max_credits(max_credits, settings.max_credits)
    request = EditVideoRequest(
        prompt=prompt_to_send,
        source_video=source_video,
        source_media_id=source_media_id,
        reference_images=tuple(p.resolve() for p in refs),
        model=VideoModel.from_cli(model),
        aspect=Aspect.from_cli(aspect) if aspect is not None else None,
        resolution=resolution.lower() if resolution is not None else None,
        max_credits=effective_max,
        original_prompt=original_prompt,
        tool=applied_tool,
    )
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _edit_and_report(
            request,
            profile_name=profile_name,
            profile_dir=provider_dir,
            out_dir=out_dir,
            output_file=output_file,
            as_json=as_json,
            output_format=output_format,
            project_id=project_id,
            project_name=project_name,
        ),
        cli_command="video edit",
        as_json=is_json,
        output_format=output_format,
    )


__all__: list[Any] = ["edit"]
