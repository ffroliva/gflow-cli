"""`gflow scene` — compose Flow Scenes (Add Clip). Credit-free REST."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click
import structlog
from rich.console import Console

from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile, run_with_handlers
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
    if not (start < end):
        msg = f"bad trim in clipRef {token!r}: start must be < end"
        raise ValueError(msg)
    return ClipRef(wf, start, end)


@click.group()
def scene() -> None:
    """Compose ordered, trimmable video clips into a Flow Scene (Add Clip)."""


@scene.command("create")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.argument("clip_refs", nargs=-1, required=True)
@click.option(
    "--output",
    "-o",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Render the composed scene into a single extended .mp4 at this path "
    "(server-side, credit-free; takes up to ~3 min). Without it, only the "
    "Flow scene is composed (no local file).",
)
@click.option("--force", is_flag=True, default=False, help="Overwrite --output if it exists.")
@click.option("--profile", default=None, help="Profile name (overrides default).")
def create(
    project_id: str,
    clip_refs: tuple[str, ...],
    output: Path | None,
    force: bool,
    profile: str | None,
) -> None:
    """Compose a new scene from CLIP_REFS (each: workflowId[:start-end]).

    With --output, also renders the scene into one extended .mp4 via Flow's
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
            console.print(f"[yellow]--output has no .mp4 suffix; using[/yellow] {output}.mp4")
            output = output.with_suffix(".mp4")
        if output.exists() and not force:
            msg = f"{output} already exists; pass --force to overwrite."
            raise click.UsageError(msg)
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_create(
            profile_name=profile_name,
            profile_dir=pdir,
            headless=settings.headless,
            project_id=project_id,
            refs=refs,
            output=output,
        ),
        cli_command="scene create",
    )


@scene.command("show")
@click.option("--scene", "scene_id", required=True, help="Scene id to read back.")
@click.option("--project", "project_id", required=True, help="Flow project id.")
@click.option("--profile", default=None, help="Profile name (overrides default).")
def show(scene_id: str, project_id: str, profile: str | None) -> None:
    """Read back a scene's clip order and trims."""
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_show(
            profile_dir=pdir,
            headless=settings.headless,
            scene_id=scene_id,
            project_id=project_id,
        ),
        cli_command="scene show",
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
) -> None:
    recorder = OperationRecorder.open(get_settings())
    try:
        async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
            source_ids = [r.workflow_id for r in refs]
            scene_obj = await client.create_scene(project_id=project_id, workflow_ids=source_ids)
            scene_obj = await _apply_trims(client, scene_obj, project_id, refs)
            _render(scene_obj)
            if output is not None:
                # Server-side concat into ONE extended .mp4 (credit-free, no ffmpeg).
                inputs = list(scene_obj.to_concat_inputs())
                with console.status("[bold]Rendering extended video…[/bold] (up to ~3 min)"):
                    target = await client.concatenate_scene(inputs, out_path=output)
                console.print(f"[bold green]Wrote extended video:[/bold green] {target}")
            try:
                recorder.record_scene(
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
                console.print(f"[yellow]Scene created but not recorded locally:[/yellow] {exc}")
    finally:
        recorder.close()


async def _run_show(*, profile_dir: Path, headless: bool, scene_id: str, project_id: str) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        _render(await client.get_scene_workflows(scene_id, project_id=project_id))
