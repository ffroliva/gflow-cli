"""`gflow movie` — produce a multi-scene AI movie from a movie.toml project file.

Orchestrates three phases in a single browser session:

  1. **Characters** — create any :class:`CharacterDef` not yet present in the
     run-state file (``<manifest>-state.json``), reusing existing ones on resume.
  2. **Scenes** — generate each :class:`SceneDef` using the appropriate mode
     (t2v / r2v / i2v).  For ``r2v`` scenes the character's face + body images
     are auto-injected as ``--ref`` inputs.
  3. **Assembly hint** — print a ready-to-run ``gflow scene create`` command to
     stitch all completed clips, or execute it automatically when
     ``[assemble] output = "..."`` is set in the manifest.

A dry-run (``--dry-run``) prints the plan and credit estimate without spending.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import structlog
from rich.console import Console

from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile, run_with_handlers
from gflow_cli.api.character import CharacterImageRequest
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel, VideoStarted
from gflow_cli.config import get_settings
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.errors import ConfigurationError, DataStoreError
from gflow_cli.movie_manifest import (
    CharacterDef,
    CharacterState,
    MovieManifest,
    MovieState,
    SceneDef,
    SceneState,
)
from gflow_cli.paths import resolve_batch_output_dir
from gflow_cli.services.character_create import character_create
from gflow_cli.storage import cloud_info_from_path

console = Console()
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

_TEMPLATE = """\
# movie.toml — gflow movie project
# Run with: gflow movie run movie.toml
title = "My Short Film"
project = "YOUR_FLOW_PROJECT_ID"
output_dir = "./out/my-movie"  # optional

[[characters]]
name = "Alice"
face_prompt = "Young woman with curly red hair, green eyes, warm smile"
body_prompt = "Athletic build, casual jeans and light jacket"  # optional
model = "nano2"  # nano2 (default) or nanopro

[[scenes]]
title = "Establishing Shot"
type = "t2v"
prompt = "Futuristic city skyline at golden hour, cinematic wide shot"
aspect = "16:9"
duration = 8
model = "veo-lite"

[[scenes]]
title = "Character Arrives"
type = "r2v"
prompt = "Alice walks through a busy futuristic plaza, looking around with wonder"
characters = ["Alice"]
aspect = "16:9"
duration = 8

[[scenes]]
title = "Close-Up"
type = "r2v"
prompt = "Close-up of Alice's face as she smiles with excitement"
characters = ["Alice"]
aspect = "16:9"
duration = 6
model = "veo-quality"

[assemble]
output = "./out/my-movie/final.mp4"
"""

# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group()
def movie() -> None:
    """Produce a multi-scene AI movie from a movie.toml project file."""


# ---------------------------------------------------------------------------
# template
# ---------------------------------------------------------------------------


@movie.command("template")
@click.argument("output", default="movie.toml", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--force", is_flag=True, default=False, help="Overwrite OUTPUT if it already exists.")
def template(output: Path, force: bool) -> None:
    """Write a starter movie.toml to OUTPUT (default: ./movie.toml).

    Edit the file to define your characters and scenes, then run:

    \b
      gflow movie run movie.toml
    """
    output = output.expanduser()
    if output.exists() and not force:
        console.print(f"[red]{output}[/red] already exists. Use [bold]--force[/bold] to overwrite.")
        sys.exit(1)
    output.write_text(_TEMPLATE, encoding="utf-8")
    console.print(f"[bold green]Created:[/bold green] {output}")
    console.print("  Edit the file, then run [bold]gflow movie run movie.toml[/bold]")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@movie.command("run")
@click.argument(
    "manifest_path", metavar="MANIFEST", type=click.Path(dir_okay=False, path_type=Path)
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--out-dir",
    "out_dir_override",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Override output directory from the manifest.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print plan and credit estimate; make no API calls.",
)
@click.option(
    "--continue-on-error/--fail-fast",
    "continue_on_error",
    default=True,
    show_default=True,
    help=(
        "On scene failure: --continue-on-error attempts remaining scenes (default); "
        "--fail-fast stops immediately."
    ),
)
def run(
    manifest_path: Path,
    profile: str | None,
    out_dir_override: Path | None,
    dry_run: bool,
    continue_on_error: bool,
) -> None:
    """Execute a movie.toml — create characters and generate all scenes.

    On the first run every character and scene is created from scratch.
    On subsequent runs the sibling <manifest>-state.json is consulted and
    already-completed steps are skipped, making the command safe to re-run
    after a crash or interruption.

    \b
    Examples:
      gflow movie run movie.toml
      gflow movie run movie.toml --dry-run
      gflow movie run movie.toml --fail-fast --out-dir ./out/film1
    """
    manifest_path = manifest_path.expanduser().resolve()
    try:
        manifest = MovieManifest.from_toml_path(manifest_path)
    except ConfigurationError as exc:
        console.print(f"[red]Manifest error:[/red] {exc}")
        sys.exit(11)

    settings = get_settings()
    out_dir = resolve_batch_output_dir(
        cli_override=out_dir_override,
        config_value=manifest.output_dir,
        output_root=settings.output_dir,
    )

    state_path = MovieState.state_path_for(manifest_path)
    state = MovieState.load(state_path, title=manifest.title, project=manifest.project)

    _print_header(manifest, out_dir=out_dir, dry_run=dry_run)

    if dry_run:
        _print_plan(manifest, state)
        return

    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_movie(
            manifest=manifest,
            state=state,
            state_path=state_path,
            profile_name=profile_name,
            profile_dir=pdir,
            out_dir=out_dir,
            continue_on_error=continue_on_error,
        ),
        cli_command="movie run",
    )


# ---------------------------------------------------------------------------
# Dry-run plan printer
# ---------------------------------------------------------------------------


def _print_header(manifest: MovieManifest, *, out_dir: Path, dry_run: bool) -> None:
    tag = " [dim](dry-run)[/dim]" if dry_run else ""
    console.print(f"\n[bold]gflow movie run[/bold]{tag} · [bold]{manifest.title}[/bold]")
    console.print(f"  project:    {manifest.project}")
    console.print(f"  output_dir: [dim]{out_dir}[/dim]")
    console.print(f"  characters: {len(manifest.characters)}  scenes: {len(manifest.scenes)}")


def _print_plan(manifest: MovieManifest, state: MovieState) -> None:
    console.print("\n[bold]Plan:[/bold]")

    # Characters
    if manifest.characters:
        console.print("\n  Characters:")
        char_credits = 0
        for c in manifest.characters:
            done = c.name in state.characters
            slots = 2 if c.body_prompt else 1
            char_credits += 0 if done else slots
            status = "[dim]skip (exists)[/dim]" if done else f"{slots} credit(s)"
            console.print(f"    {c.name!r}  {status}")
    else:
        console.print("\n  Characters: [dim]none[/dim]")
        char_credits = 0

    # Scenes
    console.print("\n  Scenes:")
    scene_credits = 0
    for s in manifest.scenes:
        done_state = state.scenes.get(s.title)
        done = done_state is not None and done_state.status == "completed"
        cost = 0 if done else s.count
        scene_credits += cost
        refs = f"  refs=[{', '.join(s.characters)}]" if s.characters else ""
        status = "[dim]skip (done)[/dim]" if done else f"{cost} credit(s)"
        model_tag = f"  {s.model}" if s.model else ""
        dur_tag = f"  {s.duration}s" if s.duration else ""
        console.print(f"    [{s.type}] {s.title!r}{model_tag}{dur_tag}{refs}  {status}")

    total = char_credits + scene_credits
    console.print(f"\n  Estimated credits: ~{total}")

    if manifest.assemble and manifest.assemble.output:
        console.print(f"\n  Assembly → {manifest.assemble.output}")


# ---------------------------------------------------------------------------
# Core async orchestrator
# ---------------------------------------------------------------------------


async def _run_movie(
    *,
    manifest: MovieManifest,
    state: MovieState,
    state_path: Path,
    profile_name: str,
    profile_dir: Path,
    out_dir: Path,
    continue_on_error: bool,
) -> None:
    settings = get_settings()
    recorder = OperationRecorder.open(settings)

    try:
        async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:
            # ------------------------------------------------------------------
            # Phase 1: Characters
            # ------------------------------------------------------------------
            if manifest.characters:
                console.print("\n[bold]Phase 1 — Characters[/bold]")
            for char_def in manifest.characters:
                if char_def.name in state.characters:
                    console.print(
                        f"  [dim]Character {char_def.name!r} — already created, skipping.[/dim]"
                    )
                    continue
                console.print(f"\n  Creating character [bold]{char_def.name}[/bold]…")
                await _create_character(
                    client=client,
                    recorder=recorder,
                    char_def=char_def,
                    project_id=manifest.project,
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    state=state,
                    state_path=state_path,
                )

            # ------------------------------------------------------------------
            # Phase 2: Scenes
            # ------------------------------------------------------------------
            console.print("\n[bold]Phase 2 — Scenes[/bold]")
            completed_scene_ids: list[str] = []
            completed_local_paths: list[Path] = []

            for scene_def in manifest.scenes:
                scene_state = state.scenes.get(scene_def.title)
                if scene_state is not None and scene_state.status == "completed":
                    console.print(
                        f"  [dim]Scene {scene_def.title!r} — already generated, skipping.[/dim]"
                    )
                    if scene_state.flow_operation_id:
                        completed_scene_ids.append(scene_state.flow_operation_id)
                    if scene_state.local_path:
                        completed_local_paths.append(Path(scene_state.local_path))
                    continue

                console.print(
                    f"\n  Generating scene [bold]{scene_def.title!r}[/bold] ({scene_def.type})…"
                )
                refs = _collect_refs(scene_def, state)

                try:
                    video_result = await _generate_scene(
                        client=client,
                        recorder=recorder,
                        scene_def=scene_def,
                        refs=refs,
                        profile_name=profile_name,
                        profile_dir=profile_dir,
                        out_dir=out_dir,
                    )
                    state.scenes[scene_def.title] = SceneState(
                        media_id=video_result.status.media_id,
                        flow_operation_id=video_result.flow_operation_id,
                        local_path=(
                            str(video_result.local_path)
                            if video_result.local_path is not None
                            else None
                        ),
                        status="completed",
                    )
                    state.save(state_path)
                    console.print(f"    saved: {video_result.local_path}")
                    if video_result.flow_operation_id:
                        completed_scene_ids.append(video_result.flow_operation_id)
                    if video_result.local_path:
                        completed_local_paths.append(video_result.local_path)

                except Exception as exc:
                    log.error(
                        "movie.scene_failed",
                        title=scene_def.title,
                        error=str(exc),
                        exc_info=True,
                    )
                    state.scenes[scene_def.title] = SceneState(
                        media_id="",
                        flow_operation_id=None,
                        local_path=None,
                        status="failed",
                    )
                    state.save(state_path)
                    if continue_on_error:
                        console.print(f"    [red]Scene failed:[/red] {exc}")
                    else:
                        raise

    finally:
        recorder.close()

    _print_summary(
        manifest=manifest,
        completed_scene_ids=completed_scene_ids,
        completed_local_paths=completed_local_paths,
    )


# ---------------------------------------------------------------------------
# Character creation helper
# ---------------------------------------------------------------------------


async def _create_character(
    *,
    client: FlowApiClient,
    recorder: OperationRecorder,
    char_def: CharacterDef,
    project_id: str,
    profile_name: str,
    profile_dir: Path,
    state: MovieState,
    state_path: Path,
) -> None:
    face = CharacterImageRequest(
        prompt=char_def.face_prompt,
        model=char_def.model,
        image_reference_index=0,
    )
    body: CharacterImageRequest | None = None
    if char_def.body_prompt is not None:
        body = CharacterImageRequest(
            prompt=char_def.body_prompt,
            model=char_def.model,
            image_reference_index=1,
        )

    result = await character_create(
        client,
        recorder,
        profile_name=profile_name,
        profile_dir=profile_dir,
        project_id=project_id,
        name=char_def.name,
        face=face,
        body=body,
    )
    image_paths: list[str | None] = [str(p) if p is not None else None for p in result.image_paths]
    state.characters[char_def.name] = CharacterState(
        entity_id=result.entity_id,
        image_paths=image_paths,
    )
    state.save(state_path)
    console.print(f"    entity_id: {result.entity_id}")
    for slot, p in enumerate(image_paths):
        label = "face" if slot == 0 else "body" if slot == 1 else f"slot{slot}"
        console.print(f"    {label}: {p or '(unavailable)'}")


# ---------------------------------------------------------------------------
# Scene generation helper
# ---------------------------------------------------------------------------


def _collect_refs(scene_def: SceneDef, state: MovieState) -> list[str]:
    """Return face + body image paths for all characters named in *scene_def*."""
    refs: list[str] = []
    if scene_def.type != "r2v":
        return refs
    for char_name in scene_def.characters:
        char_info = state.characters.get(char_name)
        if char_info is None:
            log.warning("movie.character_not_in_state", name=char_name)
            continue
        for p in char_info.image_paths:
            if p is not None:
                refs.append(p)
    return refs


async def _generate_scene(
    *,
    client: FlowApiClient,
    recorder: OperationRecorder,
    scene_def: SceneDef,
    refs: list[str],
    profile_name: str,
    profile_dir: Path,
    out_dir: Path,
) -> Any:
    mode = Mode(scene_def.type)
    aspect = Aspect.from_cli(scene_def.aspect)
    model = VideoModel.from_cli(scene_def.model)

    kwargs: dict[str, Any] = {
        "prompt": scene_def.prompt,
        "mode": mode,
        "aspect": aspect,
        "model": model,
        "duration": scene_def.duration,
        "count": scene_def.count,
    }
    if mode == Mode.R2V and refs:
        kwargs["reference_images"] = tuple(Path(r) for r in refs)
    elif mode == Mode.I2V and scene_def.initial_frame:
        kwargs["start_image"] = Path(scene_def.initial_frame)
        if scene_def.end_frame:
            kwargs["end_image"] = Path(scene_def.end_frame)

    request = GenerateVideoRequest(**kwargs)

    def on_started(started: VideoStarted) -> None:
        try:
            recorder.record_started_video(
                profile_name=profile_name,
                profile_dir=profile_dir,
                request=request,
                started=started,
            )
        except DataStoreError as exc:
            log.warning("movie.persistence_failed_on_start", error=str(exc))

    result = await client.generate_video(
        req=request,
        out_dir=out_dir,
        download=True,
        on_started=on_started,
    )

    try:
        recorder.record_completed_video(
            profile_name=profile_name,
            _profile_dir=profile_dir,
            request=request,
            result=result,
            cloud_storage_info=(
                cloud_info_from_path(result.local_path) if result.local_path is not None else None
            ),
        )
    except DataStoreError as exc:
        log.warning("movie.persistence_failed_on_complete", error=str(exc))

    if not result.status.succeeded:
        reasons = (
            ", ".join(result.status.failure_reasons)
            or result.status.error_message
            or "unknown reason"
        )
        msg = f"Video generation failed: {reasons}"
        raise RuntimeError(msg)

    return result


# ---------------------------------------------------------------------------
# Post-run summary
# ---------------------------------------------------------------------------


def _print_summary(
    *,
    manifest: MovieManifest,
    completed_scene_ids: list[str],
    completed_local_paths: list[Path],
) -> None:
    total = len(manifest.scenes)
    done = len(completed_local_paths)
    failed = total - done

    console.print(f"\n[bold green]Movie run complete:[/bold green] {done}/{total} scenes")
    if failed:
        console.print(f"  [yellow]{failed} scene(s) failed[/yellow] — re-run to retry.")

    if completed_local_paths:
        console.print("\n  Completed clips:")
        for p in completed_local_paths:
            console.print(f"    {p}")

    if len(completed_scene_ids) > 1:
        clip_refs = " ".join(completed_scene_ids)
        assemble_cmd = f"gflow scene create --project {manifest.project} {clip_refs}"
        if manifest.assemble and manifest.assemble.output:
            assemble_cmd += f" -o {manifest.assemble.output}"
        console.print("\n  [dim]Stitch all clips into one file:[/dim]")
        console.print(f"  [bold]{assemble_cmd}[/bold]")
    elif len(completed_scene_ids) == 1:
        console.print("\n  [dim]Only one scene — no stitching needed.[/dim]")
    else:
        console.print(
            "\n  [yellow]No workflow IDs captured — "
            "use `gflow scene create` manually with the clip workflow IDs.[/yellow]"
        )
