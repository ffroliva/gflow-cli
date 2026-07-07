"""`gflow movie` — produce a multi-scene AI movie from a movie.toml project file.

A movie.toml describes a film with a global ``[style]``, reusable
``[[characters]]`` (text identity in P1), and an ordered list of ``[[scenes]]``
where **one scene = one clip = one generation**. The runner:

  1. Composes each scene's Veo prompt deterministically from the style +
     characters + scene fields (see :mod:`gflow_cli.composition`).
  2. Generates each clip in order (T2V, or R2V when the scene names characters),
     persisting crash-recoverable state keyed on ``scene.id``.
  3. Writes a versioned ``<stem>-handoff.json`` manifest (a pure projection of
     the run state) that a downstream editor / assembler can consume.

By default the run is **generate-only**. Pass ``--stitch`` for an optional
ffmpeg hard-concat *preview* (no transitions, not a deliverable). A dry-run
(``--dry-run``) prints the plan and credit estimate without spending.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import click
import structlog
from rich.console import Console

from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile, run_with_handlers
from gflow_cli.api.character import CharacterImageRequest
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel, VideoStarted
from gflow_cli.composition import (
    Character,
    Scene,
    StyleSpec,
    build_handoff,
    compose_prompt,
    resume_hash,
)
from gflow_cli.config import get_settings
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.errors import ConfigurationError, DataStoreError, WireFormatError
from gflow_cli.movie_manifest import (
    CharacterState,
    MovieManifest,
    MovieState,
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
# movie.toml — gflow movie project (scene = clip)
# Run with: gflow movie run movie.toml
schema_version = 1
title = "My Short Film"
project = "YOUR_FLOW_PROJECT_ID"
output_dir = "./out/my-movie"  # optional

[style]
look = "cinematic, shallow depth of field"
palette = "warm golden tones"
negative = "no text, no watermark"
suffix = "Black and white cinematic street photography, photorealistic, film grain."

  [style.variants.warm]
  suffix = "Warm golden-hour cinematic grade."

[[characters]]
name = "Alice"
appearance = "Young woman with curly red hair, green eyes, warm smile"
voice = "alnilam"  # optional voice preset (P2)
  [characters.variants]
  silhouette = "shown only in silhouette"

[[scenes]]
id = "establishing"
action = "A futuristic city skyline at golden hour"
framing = "establishing"
aspect = "16:9"
duration = 8
model = "veo-lite"

[[scenes]]
id = "arrival"
action = "walks through a busy futuristic plaza, looking around with wonder"
framing = "wide"
characters = ["Alice"]
style_variant = "warm"  # select [style.variants.warm]; omit for base style
aspect = "16:9"
duration = 8

[[scenes]]
id = "close-up"
action = "smiles with excitement"
framing = "close-up"
characters = ["Alice"]
speaker = "Alice"
line = "We made it!"
style_suffix = "golden hour light"  # appended after the variant/base suffix
aspect = "16:9"
duration = 6
model = "veo-quality"
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

    Edit the file to define your style, characters, and scenes, then run:

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
@click.option(
    "--stitch",
    is_flag=True,
    default=False,
    help=(
        "After generating, hard-concat all clips into one PREVIEW mp4 "
        "(ffmpeg, no transitions). Not a deliverable."
    ),
)
def run(
    manifest_path: Path,
    profile: str | None,
    out_dir_override: Path | None,
    dry_run: bool,
    continue_on_error: bool,
    stitch: bool,
) -> None:
    """Execute a movie.toml — compose prompts and generate every scene.

    On the first run every scene is generated from scratch. On subsequent runs
    the sibling <manifest>-state.json is consulted (keyed on scene.id) and
    already-completed scenes are skipped, making the command safe to re-run
    after a crash or interruption. A versioned handoff manifest is always
    written at the end.

    \b
    Examples:
      gflow movie run movie.toml
      gflow movie run movie.toml --dry-run
      gflow movie run movie.toml --fail-fast --out-dir ./out/film1
      gflow movie run movie.toml --stitch
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
            stitch=stitch,
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


def _style_tag(s: Scene, style: StyleSpec) -> str:
    """Return the resolved-style fragment for the plan line, or ''."""
    bits: list[str] = []
    if s.style_variant:
        bits.append(f"style={s.style_variant}")
    elif style.suffix:
        bits.append("style=base")
    if s.style_suffix:
        bits.append("+style_suffix")
    return ("  " + " ".join(bits)) if bits else ""


def _format_scene_line(s: Scene, style: StyleSpec, *, done: bool, stale: bool, cost: int) -> str:
    """Return the single-line plan summary for one scene."""
    mode = "r2v" if s.characters else "t2v"
    refs = f"  refs=[{', '.join(s.characters)}]" if s.characters else ""
    framing_tag = f"  {s.framing}" if s.framing else ""
    model_tag = f"  {s.model}" if s.model else ""
    dur_tag = f"  {s.duration}s" if s.duration else ""
    if done:
        status = "[dim]skip (done)[/dim]"
    elif stale:
        status = f"re-run (style changed)  {cost} credit(s)"
    else:
        status = f"{cost} credit(s)"
    style_tag = _style_tag(s, style)
    return f"    [{mode}] {s.id!r}{framing_tag}{model_tag}{dur_tag}{refs}{style_tag}  {status}"


def _print_plan(manifest: MovieManifest, state: MovieState) -> None:
    console.print("\n[bold]Plan:[/bold]")

    # Characters (text identity in P1 — no credits)
    if manifest.characters:
        console.print("\n  Characters:")
        for name, c in manifest.characters.items():
            console.print(f"    {name!r}  [dim]({c.identity})[/dim]")
    else:
        console.print("\n  Characters: [dim]none[/dim]")

    # Scenes
    console.print("\n  Scenes:")
    scene_credits = 0
    for s in manifest.scenes:
        done_state = state.scenes.get(s.id)
        done = False
        stale = False
        if done_state is not None and done_state.status == "completed":
            stale = done_state.is_stale_for(compose_prompt(manifest.style, s, manifest.characters))
            done = not stale
        cost = 0 if done else 1
        scene_credits += cost
        console.print(_format_scene_line(s, manifest.style, done=done, stale=stale, cost=cost))

    console.print(f"\n  Estimated credits: ~{scene_credits}")


# ---------------------------------------------------------------------------
# Core async orchestrator
# ---------------------------------------------------------------------------


async def _run_one_scene(
    *,
    client: FlowApiClient,
    recorder: OperationRecorder,
    scene: Scene,
    manifest: MovieManifest,
    state: MovieState,
    state_path: Path,
    profile_name: str,
    profile_dir: Path,
    out_dir: Path,
    generated_any: bool,
    continue_on_error: bool,
) -> Path | None:
    """Generate one scene clip and persist its state.

    Returns the local video path on success, ``None`` on a handled failure
    (continue_on_error).  Re-raises on an unhandled failure (fail-fast).
    """
    prompt = compose_prompt(manifest.style, scene, manifest.characters)

    # reference_entities / reference_entity_names are resolved inside
    # the try so a pre-flight ConfigurationError (missing entity_id)
    # is handled per-scene by the continue-on-error / fail-fast
    # policy like any other failure.
    reference_entities: tuple[str, ...] = ()

    try:
        # Resolve native (entity-identity) characters to their created
        # entity ids. FAIL LOUD when an entity character has no
        # entity_id in state — never silently drop the reference
        # (council C4). reference_audio stays None: embedded-voice-on-
        # entity is the preferred path (the entity carries the voice).
        reference_entities = _resolve_entities(scene, manifest, state)

        # Build the parallel display-name list so the UI picker can
        # select tiles by name instead of UUID (live e2e fix).
        reference_entity_names = _resolve_entity_names(scene, manifest)

        # reCAPTCHA cooldown between scenes — inside the try so any
        # failure here is handled per-scene and never aborts the run.
        if generated_any:
            await asyncio.sleep(5)

        video_result = await _generate_scene(
            client=client,
            recorder=recorder,
            scene=scene,
            prompt=prompt,
            reference_entities=reference_entities,
            reference_entity_names=reference_entity_names,
            reference_audio=None,
            profile_name=profile_name,
            profile_dir=profile_dir,
            out_dir=out_dir,
            project_id=manifest.project,
        )
        local_path_str = (
            str(video_result.local_path) if video_result.local_path is not None else None
        )
        state.scenes[scene.id] = SceneState(
            media_id=video_result.status.media_id,
            flow_operation_id=video_result.flow_operation_id,
            local_path=local_path_str,
            status="completed",
            prompt=prompt,
            consistency_method="entity" if reference_entities else "text",
            style_hash=resume_hash(prompt),
        )
        state.save(state_path)
        console.print(f"    saved: {video_result.local_path}")
        return video_result.local_path

    except Exception as exc:
        log.error(
            "movie.scene_failed",
            scene_id=scene.id,
            error=str(exc),
            exc_info=True,
        )
        # An entity scene whose wire backstop (WireFormatError) tripped
        # but the run continued is recorded as "degraded" (entity path
        # attempted, identity not honored); everything else is "text".
        if reference_entities and isinstance(exc, WireFormatError):
            failed_method = "degraded"
        else:
            failed_method = "text"
        state.scenes[scene.id] = SceneState(
            media_id="",
            flow_operation_id=None,
            local_path=None,
            status="failed",
            prompt=prompt,
            consistency_method=failed_method,
            style_hash=resume_hash(prompt),
        )
        state.save(state_path)
        if continue_on_error:
            console.print(f"    [red]Scene failed:[/red] {exc}")
            return None
        raise


def _write_handoff(
    manifest: MovieManifest,
    state: MovieState,
    state_path: Path,
    out_dir: Path,
    include_prompts: bool,
) -> None:
    """Write the versioned handoff manifest alongside the state file."""
    handoff = build_handoff(manifest, state, out_dir=out_dir, include_prompts=include_prompts)
    handoff_path = state_path.with_name(state_path.stem.replace("-state", "") + "-handoff.json")
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps(handoff, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"  handoff: {handoff_path}")


async def _run_movie(
    *,
    manifest: MovieManifest,
    state: MovieState,
    state_path: Path,
    profile_name: str,
    profile_dir: Path,
    out_dir: Path,
    continue_on_error: bool,
    stitch: bool = False,
) -> None:
    settings = get_settings()
    recorder = OperationRecorder.open(settings)
    include_prompts = getattr(settings, "history_prompts", "store") == "store"

    completed_local_paths: list[Path] = []

    try:
        async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:
            # ------------------------------------------------------------------
            # Phase 1: Characters — create native (entity-identity) characters.
            # text-identity characters need no creation; they are folded into
            # the composed prompt. entity-identity characters become a Flow
            # CHARACTER entity (face + optional body + optional embedded voice)
            # whose id is later attached as a reference to R2V scenes.
            # ------------------------------------------------------------------
            entity_chars = [
                c
                for c in manifest.characters.values()
                if c.identity == "entity" and c.name not in state.characters
            ]
            if entity_chars:
                console.print("\n[bold]Characters[/bold]")
            for char in entity_chars:
                console.print(f"\n  Creating character [bold]{char.name}[/bold]…")
                await _create_character(
                    client=client,
                    recorder=recorder,
                    char=char,
                    project_id=manifest.project,
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    state=state,
                    state_path=state_path,
                )

            # ------------------------------------------------------------------
            # Scenes — one scene = one clip = one generation
            # ------------------------------------------------------------------
            console.print("\n[bold]Scenes[/bold]")
            generated_any = False

            for scene in manifest.scenes:
                scene_state = state.scenes.get(scene.id)
                if scene_state is not None and scene_state.status == "completed":
                    prompt_now = compose_prompt(manifest.style, scene, manifest.characters)
                    if scene_state.is_stale_for(prompt_now):
                        console.print(
                            f"  Scene {scene.id!r} — prompt/style changed since last "
                            "run; regenerating."
                        )
                    else:
                        console.print(
                            f"  [dim]Scene {scene.id!r} — already generated, skipping.[/dim]"
                        )
                        if scene_state.local_path:
                            completed_local_paths.append(Path(scene_state.local_path))
                        generated_any = True
                        continue

                console.print(f"\n  Generating scene [bold]{scene.id!r}[/bold]…")
                local_path = await _run_one_scene(
                    client=client,
                    recorder=recorder,
                    scene=scene,
                    manifest=manifest,
                    state=state,
                    state_path=state_path,
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    out_dir=out_dir,
                    generated_any=generated_any,
                    continue_on_error=continue_on_error,
                )
                if local_path is not None:
                    completed_local_paths.append(local_path)
                generated_any = True

    finally:
        recorder.close()
        # Always write the handoff manifest (pure projection of run state).
        _write_handoff(manifest, state, state_path, out_dir, include_prompts)

    _print_summary(manifest=manifest, completed_local_paths=completed_local_paths)

    if stitch:
        completed = [
            Path(s.local_path)
            for s in state.scenes.values()
            if s.status == "completed" and s.local_path
        ]
        if len(completed) >= 2:
            preview = out_dir / "preview.mp4"
            _ffmpeg_concat(completed, preview)
            if preview.exists():
                console.print(f"  [dim]stitch preview:[/dim] {preview}")
        else:
            console.print("  [dim]stitch skipped — need ≥2 completed clips.[/dim]")


# ---------------------------------------------------------------------------
# Character creation helper (entity identity)
# ---------------------------------------------------------------------------


async def _create_character(
    *,
    client: FlowApiClient,
    recorder: OperationRecorder,
    char: Character,
    project_id: str,
    profile_name: str,
    profile_dir: Path,
    state: MovieState,
    state_path: Path,
) -> None:
    """Create one native (entity-identity) character and persist its state.

    Builds the face (slot 0) and optional body (slot 1) image requests, runs
    the persist-before-spend ``character_create`` saga (forwarding the
    manifest character's ``voice`` so it is embedded on the entity — the
    preferred identity-carrying path, council C2), then records the resulting
    ``entity_id`` + downloaded image paths to the crash-recoverable run state
    immediately (persist-before-spend discipline).
    """
    # identity == "entity" guarantees a face_prompt (validated in the manifest
    # parser), so this is never None for the characters we create here.
    face = CharacterImageRequest(
        prompt=char.face_prompt or "",
        model=char.model,
        image_reference_index=0,
    )
    body: CharacterImageRequest | None = None
    if char.body_prompt:
        body = CharacterImageRequest(
            prompt=char.body_prompt,
            model=char.model,
            image_reference_index=1,
        )

    result = await character_create(
        client,
        recorder,
        profile_name=profile_name,
        profile_dir=profile_dir,
        project_id=project_id,
        name=char.name,
        face=face,
        body=body,
        voice=char.voice,
    )

    image_paths: list[str | None] = []
    for p in result.image_paths:
        if p is None:
            image_paths.append(None)
        elif hasattr(p, "as_posix"):
            image_paths.append(p.as_posix())  # type: ignore[union-attr]
        else:
            image_paths.append(str(p))

    state.characters[char.name] = CharacterState(
        entity_id=result.entity_id,
        image_paths=image_paths,
    )
    state.save(state_path)
    console.print(f"    entity_id: {result.entity_id}")
    for slot, p in enumerate(image_paths):
        if slot == 0:
            label = "face"
        elif slot == 1:
            label = "body"
        else:
            label = f"slot{slot}"
        path_display = p or "(unavailable)"
        console.print(f"    {label}: {path_display}")


def _resolve_entities(
    scene: Scene,
    manifest: MovieManifest,
    state: MovieState,
) -> tuple[str, ...]:
    """Resolve a scene's entity-identity characters to their created entity ids.

    For each character named by *scene* whose manifest identity is ``"entity"``,
    look up its ``entity_id`` in the run state.  Raises :class:`ConfigurationError`
    (pre-flight fail-loud) if an entity-identity character was never created —
    the reference is never silently dropped (council C4).  text-identity
    characters contribute no entities (they are folded into the prompt).
    """
    entities: list[str] = []
    for name in scene.characters:
        char = manifest.characters.get(name)
        if char is None or char.identity != "entity":
            continue
        cstate = state.characters.get(name)
        if cstate is None or not cstate.entity_id:
            msg = (
                f"Scene {scene.id!r} references entity-identity character {name!r} "
                "but no entity was created for it (missing entity_id in run state)."
            )
            raise ConfigurationError(msg)
        entities.append(cstate.entity_id)
    return tuple(entities)


def _resolve_entity_names(
    scene: Scene,
    manifest: MovieManifest,
) -> tuple[str, ...]:
    """Return the display names of entity-identity characters named by *scene*.

    Parallel to :func:`_resolve_entities` — produces the same characters in the
    same order, but returns the character *name* (display name) rather than the
    entity id.  These names are passed to the UI picker so it can select tiles
    by their visible label instead of a UUID (which the picker never displays).
    text-identity characters are excluded (they produce no entity reference).
    """
    names: list[str] = []
    for name in scene.characters:
        char = manifest.characters.get(name)
        if char is None or char.identity != "entity":
            continue
        names.append(name)
    return tuple(names)


# ---------------------------------------------------------------------------
# Scene generation helper
# ---------------------------------------------------------------------------


async def _generate_scene(
    *,
    client: FlowApiClient,
    recorder: OperationRecorder,
    scene: Scene,
    prompt: str,
    reference_entities: tuple[str, ...] = (),
    reference_entity_names: tuple[str, ...] = (),
    reference_audio: str | None = None,
    profile_name: str,
    profile_dir: Path,
    out_dir: Path,
    project_id: str | None = None,
) -> Any:
    """Generate one clip for *scene* using the pre-composed *prompt*.

    Signature is pinned (P2 fills ``reference_entities`` / ``reference_audio``).
    Mode is R2V when the scene names characters AND references are present;
    in P1 identity is text (no references), so character scenes still submit as
    T2V with the character description embedded in the composed prompt. P2 adds
    ``reference_entities`` which flips these scenes to R2V.
    """
    del reference_audio  # reserved for P2 (native identity / voice)

    # R2V needs at least one reference (image or, in P2, entity). P1 has none,
    # so a named-character scene degrades to T2V (text identity in the prompt).
    use_r2v = bool(scene.characters) and bool(reference_entities)
    mode = Mode.R2V if use_r2v else Mode.T2V
    aspect = Aspect.from_cli(scene.aspect)
    model = VideoModel.from_cli(scene.model)

    request = GenerateVideoRequest(
        prompt=prompt,
        mode=mode,
        aspect=aspect,
        model=model,
        duration=scene.duration,
        count=scene.count,
        reference_entities=reference_entities,
        reference_entity_names=reference_entity_names,
    )

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
        project_id=project_id,
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
# Stitch preview (ffmpeg hard-concat — NOT a deliverable)
# ---------------------------------------------------------------------------


def _ffmpeg_concat(clips: list[Path], out: Path) -> None:
    """Hard-concat *clips* into *out* via the ffmpeg concat demuxer (stream copy).

    Skips with a warning if ffmpeg is not on PATH — the preview is non-essential.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.warning("movie.stitch_skipped_no_ffmpeg")
        console.print("  [yellow]stitch skipped — ffmpeg not found on PATH.[/yellow]")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{c.as_posix()}'\n")
        listfile = f.name
    try:
        # ffmpeg path comes from shutil.which and all args are fixed literals —
        # no shell, no user-controlled tokens.
        subprocess.run(  # noqa: S603  # NOSONAR
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                listfile,
                "-c",
                "copy",
                str(out),
            ],
            check=True,
        )
    finally:
        try:
            Path(listfile).unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Post-run summary
# ---------------------------------------------------------------------------


def _print_summary(
    *,
    manifest: MovieManifest,
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
