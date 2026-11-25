"""`gflow video edit` command — video-to-video editing on ``flow.google.com``."""

from __future__ import annotations

from pathlib import Path

import click
import structlog
from rich.console import Console

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    OutputFormat,
    _make_provider_dir,
    _resolve_profile,
    _validate_project_id,
    apply_tool_option,
    no_max_credits_option,
    out_file_option,
    output_format_option,
    resolve_max_credits,
    run_with_handlers,
    slugify_project_name,
    spends_credits,
    tool_option,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports._common import migrated_route
from gflow_cli.api.transports.migrated_extend import require_migrated_host
from gflow_cli.api.transports.migrated_video_edit import validate_edit_inputs
from gflow_cli.api.video import (
    EDIT_DEFAULT_MODEL,
    MAX_EDIT_REFERENCE_IMAGES,
    Aspect,
    EditVideoRequest,
    VideoModel,
    VideoStarted,
    is_media_uuid,
)
from gflow_cli.cli_video_extend import refuse_unported_extend, resolve_extend_project
from gflow_cli.cli_video_reporting import (
    _emit_video_result,
    _relocate_and_record_completed_video,
)
from gflow_cli.config import get_settings
from gflow_cli.data.models import OperationKind
from gflow_cli.data.recorder import OperationRecorder, record_failed_operation_safe
from gflow_cli.errors import ConfigurationError, DataStoreError
from gflow_cli.storage import refuse_cloud_storage_for_local_file

console = Console()
logger = structlog.get_logger(__name__)


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
    # Before any project: a refused host or input leaves no empty project behind.
    require_migrated_host(
        migrated_route(None, settings.flow_host, prefer_migrated=True), feature="video edit"
    )
    await validate_edit_inputs(request)
    if not quiet_output:
        console.print("[dim]Editing video — this takes ~2 minutes…[/dim]")
    recorder = OperationRecorder.open(settings)
    started_media_ids: list[str] = []
    try:
        async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:
            if project_id is None:
                created = await client.create_project(
                    title=project_name or slugify_project_name(request.prompt)
                )
                project_id = created.project_id

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

    _emit_video_result(
        "video edit",
        request,
        result,
        output_format,
        is_json,
        console,
        noun="Video edit",
        build=json_output.edit_video_result,
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
@no_max_credits_option
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
    if not prompt.strip():
        raise click.BadParameter("PROMPT must not be empty.", param_hint="PROMPT")
    source_video, source_media_id = _classify_source(source)
    profile_name = _resolve_profile(profile)
    is_json = as_json or output_format == OutputFormat.JSON.value
    try:
        # The MCP twin's resolution: --project, the catalog, then GFLOW_CLI_PROJECT.
        project_id = resolve_extend_project(
            project_id=project_id,
            project_name=project_name,
            profile_name=profile_name,
            media_id=source_media_id,
            command="video edit",
        )
    except ConfigurationError as exc:
        refuse_unported_extend(exc, is_json, output_format, cli_command="video edit")
        return
    quiet_output = is_json or output_format == OutputFormat.ID.value
    prompt_to_send, original_prompt, applied_tool = apply_tool_option(
        prompt, tool_specs, category="video", quiet=quiet_output
    )
    settings = get_settings()
    effective_max = resolve_max_credits(max_credits, settings.max_credits)
    request = EditVideoRequest(
        prompt=prompt_to_send,
        source_video=source_video,
        source_media_id=source_media_id,
        reference_images=tuple(p.resolve() for p in refs),
        model=VideoModel.from_cli(model) or EDIT_DEFAULT_MODEL,
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


__all__ = ["edit"]
