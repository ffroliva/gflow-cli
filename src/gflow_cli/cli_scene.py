"""`gflow scene` — compose Flow Scenes (Add Clip). Credit-free REST."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click
import structlog
from rich.console import Console
from rich.markup import escape

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    OutputFormat,
    _make_provider_dir,
    _resolve_profile,
    out_file_option,
    output_format_option,
    run_with_handlers,
    spends_credits,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.scene import Scene, SceneWorkflow, SceneWorkflowMetadata
from gflow_cli.config import get_settings
from gflow_cli.data.models import OperationKind
from gflow_cli.data.recorder import OperationRecorder

console = Console()
log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ClipRef:
    workflow_id: str
    start: float | None
    end: float | None


def _validate_trim(*, start: float, end: float, total: float) -> None:
    if not (0.0 <= start < end <= total):
        msg = (
            f"trim out of range: need 0 <= start < end <= total({total}); "
            f"got start={start} end={end}"
        )
        raise ValueError(msg)


def _parse_clip_ref(token: str) -> ClipRef:
    """Parse `workflowId[:start-end]` (seconds). start<end enforced here; the
    range-vs-total check happens later once totalDuration is known."""
    if ":" not in token:
        return ClipRef(token, None, None)
    wf, _, trim = token.partition(":")
    try:
        start_s, _, end_s = trim.partition("-")
        start, end = float(start_s), float(end_s)
    except ValueError as e:
        msg = f"bad trim in clipRef {token!r}: expected <start>-<end> in seconds"
        raise ValueError(msg) from e
    if start >= end:
        msg = f"bad trim in clipRef {token!r}: start must be < end"
        raise ValueError(msg)
    return ClipRef(wf, start, end)


@click.group()
def scene() -> None:
    """Compose ordered, trimmable video clips into a Flow Scene (Add Clip)."""


@spends_credits(False)
@scene.command("create")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.argument("clip_refs", nargs=-1, required=True)
@out_file_option(
    "output",
    help="Render the composed scene into a single extended .mp4 at this path "
    "(server-side, credit-free; takes up to ~3 min). Without it, only the "
    "Flow scene is composed (no local file).",
)
@output_format_option(has_out_file=True)
@click.option("--force", is_flag=True, default=False, help="Overwrite --out-file if it exists.")
@click.option("--profile", default=None, help="Profile name (overrides default).")
def create(
    project_id: str,
    clip_refs: tuple[str, ...],
    output: Path | None,
    force: bool,
    profile: str | None,
    as_json: bool,
    output_format: str = "table",
) -> None:
    """Compose a new scene from CLIP_REFS (each: workflowId[:start-end]).

    With --out-file, also renders the scene into one extended .mp4 via Flow's
    server-side concatenation (credit-free, no ffmpeg).
    """
    try:
        refs = [_parse_clip_ref(t) for t in clip_refs]
    except ValueError as e:
        # Surface malformed clipRefs as a Click usage error (exit 2) instead of
        # an uncaught traceback, matching the rest of the CLI's argument errors.
        raise click.BadParameter(str(e), param_hint="CLIP_REFS") from e
    if output is not None:
        output = output.expanduser()
        if output.suffix.lower() != ".mp4":
            console.print(f"[yellow]--out-file has no .mp4 suffix; using[/yellow] {output}.mp4")
            output = output.with_suffix(".mp4")
        if output.exists() and not force:
            msg = f"{output} already exists; pass --force to overwrite."
            raise click.UsageError(msg)
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    is_json = as_json or output_format == OutputFormat.JSON.value
    run_with_handlers(
        lambda: _run_create(
            profile_name=profile_name,
            profile_dir=pdir,
            headless=settings.headless,
            project_id=project_id,
            refs=refs,
            output=output,
            as_json=as_json,
            output_format=output_format,
        ),
        cli_command="scene create",
        as_json=is_json,
        output_format=output_format,
    )


@spends_credits(False)
@scene.command("show")
@click.option("--scene", "scene_id", required=True, help="Scene id to read back.")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.option("--profile", default=None, help="Profile name (overrides default).")
@output_format_option(help="Output format (json|table|id).")
def show(
    scene_id: str,
    project_id: str,
    profile: str | None,
    as_json: bool,
    output_format: str = "table",
) -> None:
    """Read back a scene's clip order and trims."""
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    is_json = as_json or output_format == OutputFormat.JSON.value
    run_with_handlers(
        lambda: _run_show(
            profile_dir=pdir,
            headless=settings.headless,
            scene_id=scene_id,
            project_id=project_id,
            as_json=as_json,
            output_format=output_format,
        ),
        cli_command="scene show",
        as_json=is_json,
        output_format=output_format,
    )


def _render(scene_obj: Scene) -> None:
    console.print(f"[bold green]Scene:[/bold green] [bold]{scene_obj.scene_id}[/bold]")
    composed = sum(w.metadata.end_time - w.metadata.start_time for w in scene_obj.workflows)
    for w in scene_obj.workflows:
        m = w.metadata
        console.print(
            f"  [{m.position}] {w.workflow_id}  trim {m.start_time:g}-{m.end_time:g}s "
            f"(of {m.total_duration:g}s)"
        )
    console.print(
        f"[dim]Composed duration:[/dim] {composed:g}s  [dim]Clips:[/dim] {len(scene_obj.workflows)}"
    )


async def _apply_trims(
    client: FlowApiClient, scene_obj: Scene, project_id: str, refs: list[ClipRef]
) -> Scene:
    """Map trims from refs onto created instances by position, validate vs each
    instance's totalDuration, update, then read back."""
    updated: list[SceneWorkflow] = []
    # create returns one instance per submitted workflowId in submission order;
    # _parse_workflows sorts by position (== submission order). strict=True fails
    # loudly if that ever breaks.
    for w, ref in zip(scene_obj.workflows, refs, strict=True):
        total = w.metadata.total_duration
        start = ref.start if ref.start is not None else 0.0
        end = ref.end if ref.end is not None else total
        _validate_trim(start=start, end=end, total=total)
        updated.append(
            SceneWorkflow(
                w.workflow_id,
                SceneWorkflowMetadata(w.metadata.position, start, end, total),
            )
        )
    if any(r.start is not None for r in refs):
        await client.update_scene_workflows(
            scene_id=scene_obj.scene_id, project_id=project_id, workflows=updated
        )
    return await client.get_scene_workflows(scene_obj.scene_id, project_id=project_id)


async def _run_create(
    *,
    profile_name: str,
    profile_dir: Path,
    headless: bool,
    project_id: str,
    refs: list[ClipRef],
    output: Path | None = None,
    as_json: bool = False,
    output_format: str = "table",
) -> None:
    recorder = OperationRecorder.open(get_settings())
    is_json = as_json or output_format == OutputFormat.JSON.value
    quiet_output = is_json or output_format == OutputFormat.ID.value
    try:
        async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
            source_ids = [r.workflow_id for r in refs]
            scene_obj = await client.create_scene(project_id=project_id, workflow_ids=source_ids)
            scene_obj = await _apply_trims(client, scene_obj, project_id, refs)
            if not quiet_output:
                _render(scene_obj)
            # Persist the compose FIRST (non-blocking) so a render failure below
            # is recoverable: the clips' media ids + trims are in the catalog and
            # the extended video can be re-rendered without re-composing.
            scene_row_id: str | None = None
            try:
                scene_row_id = recorder.record_scene(
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    scene=scene_obj,
                    operation_kind=OperationKind.SCENE_CREATE,
                    source_workflow_ids=source_ids,
                )
            except Exception as exc:  # noqa: BLE001 — never fail a completed free op
                log.warning(
                    "scene.persist_failed_after_success",
                    error=str(exc),
                    scene_id=scene_obj.scene_id,
                )
                if not quiet_output:
                    console.print(
                        "[yellow]Scene created but not recorded locally:[/yellow] "
                        f"{escape(str(exc))}"
                    )
            target: Path | None = None
            if output is not None:
                # Server-side concat into ONE extended .mp4 (credit-free, no ffmpeg).
                # If this raises, the compose above is already persisted.
                inputs = list(scene_obj.to_concat_inputs())
                if quiet_output:
                    target = await client.concatenate_scene(inputs, out_path=output)
                else:
                    with console.status("[bold]Rendering extended video…[/bold] (up to ~3 min)"):
                        target = await client.concatenate_scene(inputs, out_path=output)
                    console.print(f"[bold green]Wrote extended video:[/bold green] {target}")
                if scene_row_id is not None:
                    try:
                        recorder.record_scene_output(
                            scene_row_id=scene_row_id, output_path=str(target)
                        )
                    except Exception as exc:  # noqa: BLE001 — never fail a completed render
                        log.warning(
                            "scene.persist_output_failed",
                            error=str(exc),
                            scene_id=scene_obj.scene_id,
                        )
            if output_format == OutputFormat.ID.value:
                click.echo(scene_obj.scene_id)
                return
            if is_json:
                json_output.emit(
                    {
                        "status": "ok",
                        "scene_id": scene_obj.scene_id,
                        "project_id": project_id,
                        "output": str(target) if target is not None else None,
                    }
                )
    finally:
        recorder.close()


async def _run_show(
    *,
    profile_dir: Path,
    headless: bool,
    scene_id: str,
    project_id: str,
    as_json: bool = False,
    output_format: str = "table",
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        scene_obj = await client.get_scene_workflows(scene_id, project_id=project_id)
        if output_format == OutputFormat.ID.value:
            click.echo(scene_id)
            return
        if as_json or output_format == OutputFormat.JSON.value:
            json_output.emit(
                {
                    "status": "ok",
                    "scene_id": scene_obj.scene_id,
                    "workflows": [
                        {
                            "workflow_id": w.workflow_id,
                            "position": w.metadata.position,
                            "start_time": w.metadata.start_time,
                            "end_time": w.metadata.end_time,
                            "total_duration": w.metadata.total_duration,
                        }
                        for w in scene_obj.workflows
                    ],
                }
            )
            return
        _render(scene_obj)
