"""`gflow video` command group.

`t2v` and `i2v` drive `UiAutomationTransport.generate_video` with auto-download.
`batch` remains stubbed pending a manifest-driven runner.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import structlog
from rich.console import Console

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    _validate_project_id,
    apply_tool_option,
    run_with_handlers,
    tool_option,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.video import VideoModel, is_media_uuid, reference_cap_for
from gflow_cli.config import get_settings
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.errors import DataStoreError
from gflow_cli.storage import cloud_info_from_path

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gflow_cli.chain import ChainLinkSpec
    from gflow_cli.tools.invocation import AppliedTool

console = Console()
logger = structlog.get_logger(__name__)

_project_option = click.option(
    "--project",
    "project_id",
    default=None,
    callback=_validate_project_id,
    help=("Generate in this existing Flow project id instead of creating a scratch project."),
)


def _warn_persistence_failed_after_success(
    *,
    exc: Exception,
    flow_media_id: str | None,
    local_path: Path | None,
) -> None:
    logger.warning(
        "data.persistence_failed_after_success",
        error_class=type(exc).__name__,
        flow_media_id=flow_media_id,
        local_path=str(local_path) if local_path is not None else None,
    )


_BATCH_UNAVAILABLE = (
    "[yellow]`gflow video batch` is not yet available.[/yellow]\n"
    "Batch video on UiAutomationTransport lands in a later release."
)


async def _generate_and_report(
    request: Any,
    *,
    profile_name: str,
    profile_dir: Path,
    out_dir: Path | None,
    command: str = "video",
    as_json: bool = False,
    project_id: str | None = None,
) -> None:
    """Drive FlowApiClient for a single GenerateVideoRequest and print the
    result (or fail with a non-zero exit). Shared by t2v, i2v, and r2v.

    Tool provenance (``original_prompt`` / ``tool``) travels on ``request``, so
    the recorder reads it directly — no separate kwarg to drift out of sync.

    With ``as_json`` the result is emitted as a JSON object (carrying the same
    ok/fail status as the exit code) instead of the Rich lines; a failed
    generation still emits its JSON payload and then exits 1.
    """
    from gflow_cli.api.video import VideoStarted

    if not as_json:
        console.print("[dim]Generating video — this takes ~2 minutes…[/dim]")
    settings = get_settings()
    recorder = OperationRecorder.open(settings)
    try:
        async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:

            def on_started(started: VideoStarted) -> None:
                try:
                    recorder.record_started_video(
                        profile_name=profile_name,
                        profile_dir=profile_dir,
                        request=request,
                        started=started,
                    )
                except DataStoreError as exc:
                    _warn_persistence_failed_after_success(
                        exc=exc,
                        flow_media_id=started.media_id,
                        local_path=None,
                    )

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
                    cloud_info_from_path(result.local_path)
                    if result.local_path is not None
                    else None
                ),
            )
        except DataStoreError as exc:
            _warn_persistence_failed_after_success(
                exc=exc,
                flow_media_id=result.status.media_id,
                local_path=result.local_path,
            )
    finally:
        recorder.close()

    if as_json:
        json_output.emit(json_output.video_result(command=command, request=request, result=result))
        if not result.status.succeeded:
            raise SystemExit(1)
        return

    if not result.status.succeeded:
        reasons = (
            ", ".join(result.status.failure_reasons)
            or result.status.error_message
            or "unknown reason"
        )
        console.print(f"[red]Video generation failed:[/red] {reasons}")
        raise SystemExit(1)

    console.print(f"[bold green]Saved:[/bold green] {result.local_path}")


async def _run_t2v(
    *,
    profile_name: str,
    profile_dir: Path,
    prompt: str,
    aspect: str,
    out_dir: Path | None,
    model: str | None = None,
    duration: int | None = None,
    count: int = 1,
    as_json: bool = False,
    original_prompt: str | None = None,
    tool: AppliedTool | None = None,
    project_id: str | None = None,
) -> None:
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

    request = GenerateVideoRequest(
        prompt=prompt,
        mode=Mode.T2V,
        aspect=Aspect.from_cli(aspect),
        model=VideoModel.from_cli(model),
        duration=duration,
        count=count,
        original_prompt=original_prompt,
        tool=tool,
    )
    await _generate_and_report(
        request,
        profile_name=profile_name,
        profile_dir=profile_dir,
        out_dir=out_dir,
        command="video t2v",
        as_json=as_json,
        project_id=project_id,
    )


@dataclass(frozen=True)
class _I2VParams:
    """Bundles image-to-video generation options for :func:`_run_i2v`.

    Separating these from the profile/output/count fields keeps the function
    signature below Sonar's 13-parameter limit (S107) while preserving every
    CLI option. Mirrors `cli_image.py`'s `_I2IParams`.
    """

    # Exactly one of image/image_ref_id (and end_frame/end_frame_ref_id) per
    # slot — split by _classify_frame, enforced by the GenerateVideoRequest DTO.
    image: str | None
    prompt: str
    aspect: str
    image_ref_id: str | None = None  # in-project asset media UUID (#287)
    end_frame: str | None = None
    end_frame_ref_id: str | None = None  # in-project asset media UUID (#287)
    model: str | None = None
    duration: int | None = None
    original_prompt: str | None = None
    tool: AppliedTool | None = None
    # Picker project-menu display-name override (#287): the media picker's
    # library is per-project and its project menu lists NAMES, not ids.
    project_name: str | None = None
    # Picker search hints (#287 round 6): the catalog-recorded prompts of the
    # UUID frame refs, first words — Flow's media search indexes prompt text
    # (tile alt), not UUIDs.
    search_hints: tuple[str, ...] = ()


# First words of a recorded prompt used as a picker search term (#287 round
# 6) — long enough to be distinctive, short enough to survive alt-text
# truncation in Flow's search index.
_SEARCH_HINT_WORDS = 6


def _media_search_hints(media_ids: Sequence[str | None]) -> tuple[str, ...]:
    """Best-effort picker search hints for media-UUID frame refs (#287 round
    6): Flow's media search does not index UUIDs, but each picker tile's alt
    text carries the generation PROMPT — resolve each ref's recorded prompt
    from the local catalog and use its first words as a search term.
    Layering: the CLI resolves (it owns catalog access); the transport only
    consumes. Never raises — a missing catalog, unknown asset, or absent
    prompt just yields no hint."""
    hints: list[str] = []
    for media_id in media_ids:
        if not media_id:
            continue
        prompt: str | None = None
        try:
            from gflow_cli.config import get_settings as _get_settings
            from gflow_cli.data import queries

            prompt = queries.get_asset_prompt(
                db_path=_get_settings().resolved_db_path(), media_id=media_id
            )
        except Exception:  # noqa: BLE001 - hints are best-effort, never fatal
            prompt = None
        if prompt:
            hint = " ".join(prompt.split()[:_SEARCH_HINT_WORDS])
            if hint and hint not in hints:
                hints.append(hint)
    return tuple(hints)


async def _run_i2v(
    *,
    profile_name: str,
    profile_dir: Path,
    params: _I2VParams,
    out_dir: Path | None,
    count: int = 1,
    as_json: bool = False,
    project_id: str | None = None,
) -> None:
    from gflow_cli.api.video import (
        I2V_DEFAULT_MODEL,
        Aspect,
        GenerateVideoRequest,
        Mode,
        VideoModel,
    )
    from gflow_cli.errors import ModelModeIncompatibilityError

    # Resolve the model with i2v-specific defaulting + validation. The Click
    # Choice already excludes omni-flash, but a stale `--config` JSON or a
    # direct programmatic call can still smuggle it in. omni-flash silently
    # drops the start/end frames at submit and routes to T2V (issue #125), so
    # reject it here before any paid call.
    resolved_model = VideoModel.from_cli(params.model)
    if resolved_model is None:
        resolved_model = I2V_DEFAULT_MODEL
    elif not resolved_model.supports_i2v_interpolation():
        msg = (
            f"{resolved_model.value!r} does not support image-to-video "
            f"interpolation; Flow silently drops the start/end frames and "
            f"produces a text-only video (issue #125)."
        )
        raise ModelModeIncompatibilityError(detail=msg)

    request = GenerateVideoRequest(
        prompt=params.prompt,
        mode=Mode.I2V,
        aspect=Aspect.from_cli(params.aspect),
        model=resolved_model,
        duration=params.duration,
        count=count,
        start_image=Path(params.image) if params.image else None,
        start_image_ref_id=params.image_ref_id,
        end_image=Path(params.end_frame) if params.end_frame else None,
        end_image_ref_id=params.end_frame_ref_id,
        project_name=params.project_name,
        search_hints=params.search_hints,
        original_prompt=params.original_prompt,
        tool=params.tool,
    )
    await _generate_and_report(
        request,
        profile_name=profile_name,
        profile_dir=profile_dir,
        out_dir=out_dir,
        command="video i2v",
        as_json=as_json,
        project_id=project_id,
    )


async def _run_r2v(
    *,
    profile_name: str,
    profile_dir: Path,
    prompt: str,
    refs: tuple[str, ...],
    aspect: str,
    out_dir: Path | None,
    model: str | None = None,
    duration: int | None = None,
    count: int = 1,
    as_json: bool = False,
    original_prompt: str | None = None,
    tool: AppliedTool | None = None,
    project_id: str | None = None,
) -> None:
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

    request = GenerateVideoRequest(
        prompt=prompt,
        mode=Mode.R2V,
        aspect=Aspect.from_cli(aspect),
        model=VideoModel.from_cli(model),
        duration=duration,
        count=count,
        reference_images=tuple(Path(r) for r in refs),
        original_prompt=original_prompt,
        tool=tool,
    )
    await _generate_and_report(
        request,
        profile_name=profile_name,
        profile_dir=profile_dir,
        out_dir=out_dir,
        command="video r2v",
        as_json=as_json,
        project_id=project_id,
    )


async def _run_batch(**kwargs: Any) -> None:  # pragma: no cover  # NOSONAR
    console.print(_BATCH_UNAVAILABLE)
    raise SystemExit(1)


def _resolve_chain_model(model: str | None) -> VideoModel:
    """Resolve + validate the chain-level ``--model`` BEFORE any spend.

    A chain renders link 0 as T2V and every later link as I2V seeded by the
    previous clip's last frame, so the model MUST support i2v interpolation.
    ``omni-flash`` silently drops the start frame at submit and routes to T2V
    (issue #125), which would break continuity AND burn a credit per link — so
    reject it at the CLI boundary with ``ModelModeIncompatibilityError`` (exit
    17). The Click ``Choice`` already excludes ``omni-flash``; this guard also
    covers a direct programmatic call or a future alias.
    """
    from gflow_cli.api.video import I2V_DEFAULT_MODEL
    from gflow_cli.errors import ModelModeIncompatibilityError

    resolved = VideoModel.from_cli(model)
    if resolved is None:
        return I2V_DEFAULT_MODEL
    if not resolved.supports_i2v_interpolation():
        msg = (
            f"{resolved.value!r} does not support image-to-video interpolation; "
            f"a chain seeds every link after the first with the previous clip's "
            f"last frame, and Flow silently drops that frame for this model "
            f"(issue #125). Use a Veo 3.1 model."
        )
        raise ModelModeIncompatibilityError(detail=msg)
    return resolved


def _print_chain_plan(
    *,
    links: Any,
    model: VideoModel,
    aspect: str,
    skipped: int,
    chain_id: str,
) -> None:
    """Render the resolved plan (used by --dry-run and the pre-spend summary)."""

    typed_links: list[ChainLinkSpec] = list(links)
    remaining = len(typed_links) - skipped
    console.print(f"[bold]Chain plan[/bold] ([dim]{chain_id}[/dim])")
    console.print(
        f"  {len(typed_links)} link(s), aspect {aspect}, model {model.value}"
        + (f" — {skipped} already completed, {remaining} to generate" if skipped else "")
    )
    console.print(f"  [yellow]Estimated cost: {remaining} credit(s)[/yellow] (one per link)")
    for idx, spec in enumerate(typed_links):
        mode = "t2v" if idx == 0 else "i2v"
        link_model = spec.model.value if spec.model is not None else model.value
        status = " [dim](done)[/dim]" if idx < skipped else ""
        console.print(f"  [{idx}] {mode} · {link_model} · {spec.prompt!r}{status}")


def _resolve_chain_resume(
    resume_from: str | None,
    links: list[Any],
    *,
    settings: Any,
    profile_name: str,
    profile_dir: Path,
) -> tuple[str, int]:
    """Resolve the chain_id and number of already-completed links.

    For a fresh run mints a new UUID.  For a resume, opens the chain recorder
    to count completed links and returns early (raises SystemExit via
    console.print + return sentinel) when the chain is already done.
    Returns ``(chain_id, skipped)`` — callers check ``skipped >= len(links)``
    themselves via the returned value; this helper raises nothing.
    """
    import uuid

    from gflow_cli.data.chain_repo import ChainLinkRecorder

    if resume_from is None:
        return str(uuid.uuid4()), 0

    chain_id = resume_from
    probe = ChainLinkRecorder.open(
        settings,
        profile_name=profile_name,
        profile_dir=profile_dir,
        chain_id=chain_id,
    )
    try:
        skipped = len(probe.completed_links())
    finally:
        probe.close()
    return chain_id, skipped


@dataclass(frozen=True)
class _ChainExecConfig:
    """Bundled context for :func:`_execute_chain_links` (keeps its arg count sane)."""

    resolved_out_dir: Path
    resolved_model: Any
    recorder: Any
    catalog_recorder: OperationRecorder
    profile_name: str
    profile_dir: Path
    aspect_enum: Any
    seed_offset: int
    jitter: float
    chain_id: str
    as_json: bool


async def _execute_chain_links(
    *,
    chain_mod: Any,
    client: Any,
    remaining_links: list[Any],
    cfg: _ChainExecConfig,
) -> tuple[list[Any], bool, list[Path]]:
    """Run the chain links, handling partial failures.

    Returns ``(results, partial, completed_paths)``.
    On a JSON partial failure exits the process directly (to avoid a double
    JSON document on stdout).
    """
    resolved_out_dir = cfg.resolved_out_dir
    resolved_model = cfg.resolved_model
    recorder = cfg.recorder
    catalog_recorder = cfg.catalog_recorder
    profile_name = cfg.profile_name
    profile_dir = cfg.profile_dir
    aspect_enum = cfg.aspect_enum
    seed_offset = cfg.seed_offset
    jitter = cfg.jitter
    chain_id = cfg.chain_id
    as_json = cfg.as_json

    from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStarted
    from gflow_cli.errors import ChainPartialError

    def _on_link_started(request: GenerateVideoRequest) -> Any:
        """Build the per-link ``on_started`` forwarded into ``generate_video``."""

        def on_started(started: VideoStarted) -> None:
            try:
                catalog_recorder.record_started_video(
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    request=request,
                    started=started,
                )
            except DataStoreError as exc:
                _warn_persistence_failed_after_success(
                    exc=exc,
                    flow_media_id=started.media_id,
                    local_path=None,
                )

        return on_started

    def _on_link_completed(request: GenerateVideoRequest, result: VideoResult) -> None:
        """Finalize the catalog row for a downloaded link."""
        try:
            catalog_recorder.record_completed_video(
                profile_name=profile_name,
                _profile_dir=profile_dir,
                request=request,
                result=result,
                cloud_storage_info=(
                    cloud_info_from_path(result.local_path)
                    if result.local_path is not None
                    else None
                ),
            )
        except DataStoreError as exc:
            _warn_persistence_failed_after_success(
                exc=exc,
                flow_media_id=result.status.media_id,
                local_path=result.local_path,
            )

    total_links = len(remaining_links)
    results: list[Any] = []
    completed_paths: list[Path] = []

    try:
        results = await chain_mod.run_chain(
            client=client,
            links=remaining_links,
            out_dir=resolved_out_dir,
            model=resolved_model,
            recorder=recorder,
            on_link_started=_on_link_started,
            on_link_completed=_on_link_completed,
            aspect=aspect_enum,
            seed_offset_ms=seed_offset,
            jitter=jitter,
        )
        return results, False, completed_paths
    except ChainPartialError as exc:
        completed_paths = list(exc.partial_results)
        logger.warning(
            "chain_link_failed",
            chain_id=chain_id,
            total_links=total_links,
            completed=len(completed_paths),
        )
        if as_json:
            # Emit the chain-shaped payload (carries the partial flag +
            # completed clip paths) and exit directly with the mapped
            # code. Re-raising here would let run_with_handlers emit a
            # SECOND, error-shaped JSON document on stdout — two
            # concatenated objects no json.loads can parse.
            import sys as _sys

            json_output.emit(
                _chain_json(
                    chain_id=chain_id,
                    results=results,
                    partial=True,
                    completed_paths=completed_paths,
                )
            )
            _sys.exit(json_output.exit_code_for(exc))
        # Non-json: re-raise so the shared handler maps
        # ChainPartialError -> exit 21 and prints the resume hint.
        raise


def _apply_tools_to_chain_links(
    links: list[ChainLinkSpec],
    tool_specs: tuple[str, ...],
) -> list[ChainLinkSpec]:
    """Apply ``--tool`` to each chain link's prompt (sequential, never-fatal).

    Returns new ``ChainLinkSpec`` objects carrying the rewritten ``prompt`` plus
    ``original_prompt`` / ``tool`` provenance. An unknown tool/style raises
    ``click.UsageError`` (pre-network) so the chain fails fast.
    """
    from dataclasses import replace

    applied: list[ChainLinkSpec] = []
    for link in links:
        sent, original, tool = apply_tool_option(
            link.prompt, tool_specs, category="video", quiet=True
        )
        applied.append(replace(link, prompt=sent, original_prompt=original, tool=tool))
    return applied


async def _run_chain(
    *,
    profile_name: str,
    profile_dir: Path,
    manifest: str,
    model: str | None,
    aspect: str,
    out_dir: Path | None,
    max_links: int | None,
    resume_from: str | None,
    jitter: float,
    seed_offset: int,
    yes: bool,
    dry_run: bool,
    as_json: bool,
    tool_specs: tuple[str, ...] = (),
) -> None:
    """Drive a sequential last-frame I2V chain from a JSONL manifest.

    The cost gate (``--yes`` / confirm), ``--max-links`` cap, ``--dry-run``
    short-circuit, and ``--resume-from`` skip-paid-links logic all run BEFORE a
    client is created so a rejected/dry run spends nothing and opens no browser.
    """
    from pathlib import Path as _Path

    from gflow_cli import chain as chain_mod
    from gflow_cli.api.video import Aspect
    from gflow_cli.chain_manifest import parse_chain_manifest
    from gflow_cli.data.chain_repo import ChainLinkRecorder
    from gflow_cli.errors import ChainManifestError

    resolved_model = _resolve_chain_model(model)
    aspect_enum = Aspect.from_cli(aspect)

    links: list[ChainLinkSpec] = parse_chain_manifest(_Path(manifest))

    if max_links is not None and len(links) > max_links:
        msg = (
            f"chain manifest has {len(links)} link(s) but --max-links is "
            f"{max_links}; raise the cap or trim the manifest before spending."
        )
        raise ChainManifestError(msg)

    settings = get_settings()

    # Resume: bind the prior chain_id, query already-paid links, skip them so
    # they are NOT regenerated (no re-billing). A fresh run mints a new id.
    chain_id, skipped = _resolve_chain_resume(
        resume_from,
        links,
        settings=settings,
        profile_name=profile_name,
        profile_dir=profile_dir,
    )
    if skipped >= len(links):
        console.print(
            f"[green]Chain {chain_id} already complete[/green] "
            f"({skipped}/{len(links)} links); nothing to do."
        )
        return

    remaining_links = links[skipped:]
    cost = len(remaining_links)

    if dry_run:
        _print_chain_plan(
            links=links,
            model=resolved_model,
            aspect=aspect,
            skipped=skipped,
            chain_id=chain_id,
        )
        console.print("[dim]--dry-run: no credits spent, no clips generated.[/dim]")
        return

    if not as_json:
        _print_chain_plan(
            links=links,
            model=resolved_model,
            aspect=aspect,
            skipped=skipped,
            chain_id=chain_id,
        )

    if not yes:
        click.confirm(
            f"Generate {cost} chain link(s) for ~{cost} credit(s)?",
            abort=True,
        )

    # Apply --tool per link AFTER the dry-run/confirm gate so a rejected or
    # dry run spends nothing and makes no Gemini calls. Each link's prompt is
    # rewritten in place; provenance rides ChainLinkSpec into the per-link
    # GenerateVideoRequest for metadata_json.tool recording (never-fatal).
    if tool_specs:
        remaining_links = _apply_tools_to_chain_links(remaining_links, tool_specs)

    resolved_out_dir = out_dir if out_dir is not None else settings.output_dir
    resolved_out_dir.mkdir(parents=True, exist_ok=True)

    recorder = ChainLinkRecorder.open(
        settings,
        profile_name=profile_name,
        profile_dir=profile_dir,
        chain_id=chain_id,
    )
    # Catalog recorder: records each chain link into the `videos` catalog
    # (parity with t2v/i2v), so `gflow data list videos` / `gflow data media`
    # surface chain clips. Distinct from the chain-correlation `recorder` above.
    catalog_recorder = OperationRecorder.open(settings)

    partial = False
    results: list[Any] = []
    try:
        async with FlowApiClient(profile_dir=profile_dir, out_dir=resolved_out_dir) as client:
            results, partial, _ = await _execute_chain_links(
                chain_mod=chain_mod,
                client=client,
                remaining_links=remaining_links,
                cfg=_ChainExecConfig(
                    resolved_out_dir=resolved_out_dir,
                    resolved_model=resolved_model,
                    recorder=recorder,
                    catalog_recorder=catalog_recorder,
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    aspect_enum=aspect_enum,
                    seed_offset=seed_offset,
                    jitter=jitter,
                    chain_id=chain_id,
                    as_json=as_json,
                ),
            )
    finally:
        recorder.close()
        catalog_recorder.close()

    if as_json:
        json_output.emit(
            _chain_json(
                chain_id=chain_id,
                results=results,
                partial=partial,
                completed_paths=[r.local_path for r in results],
            )
        )
        return

    console.print(f"[bold green]Chain complete:[/bold green] {len(results)} link(s)")
    for r in results:
        console.print(f"  [{r.index}] {r.local_path}")
    console.print(f"[dim]Stitch into one file with `gflow scene` (chain_id {chain_id}).[/dim]")


def _chain_json(
    *,
    chain_id: str,
    results: list[Any],
    partial: bool,
    completed_paths: list[Path],
) -> dict[str, Any]:
    """Machine-readable chain result payload."""
    return {
        "status": "fail" if partial else "ok",
        "command": "video chain",
        "chain_id": chain_id,
        "partial": partial,
        "links": [
            {
                "index": r.index,
                "media_id": r.media_id,
                "local_path": str(r.local_path),
            }
            for r in results
        ],
        "completed_paths": [str(p) for p in completed_paths],
    }


@click.group()
def video() -> None:
    """Generate and manage videos via Google Flow Veo."""


@video.command(
    "t2v",
    short_help="Generate a video from a text prompt.",
    help=(
        "Generate a video from a text prompt using Google Flow's Veo model.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video t2v "a golden sunset over mountains"\n'
        '  gflow video t2v "timelapse of a city" --aspect 16:9\n'
        '  gflow video t2v "portrait of a dancer" --out-dir ./videos\n'
    ),
)
@click.argument("prompt")
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(["9:16", "16:9"]),
    help="Video aspect ratio (portrait 9:16 or landscape 16:9).",
)
@click.option(
    "--model",
    default=None,
    type=click.Choice(["omni-flash", "veo-lite", "veo-fast", "veo-quality", "veo-lite-lp"]),
    help="Veo model. Omit to use Flow's current default. Only omni-flash supports --duration 10.",
)
@click.option(
    "--duration",
    default=None,
    type=click.Choice(["4", "6", "8", "10"]),
    help="Clip length in seconds. 10 requires --model omni-flash. Omit for Flow's default.",
)
@click.option(
    "--count",
    default=1,
    show_default=True,
    type=click.IntRange(1, 4),
    help="How many videos to generate (1-4). >1 multiplies credit cost.",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "-t",
    "--tool",
    "tool_specs",
    multiple=True,
    help=(
        "Apply a prompt tool before generating (e.g. creative-director or "
        "creative-director:style=cinematic). Requires GFLOW_CLI_GEMINI_API_KEY; "
        "falls back to the original prompt if unset or on error."
    ),
)
@_project_option
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the generated mp4. Defaults to tmp/.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a machine-readable JSON result instead of Rich output.",
)
def t2v(
    prompt: str,
    aspect: str,
    model: str | None,
    duration: str | None,
    count: int,
    profile: str | None,
    tool_specs: tuple[str, ...],
    project_id: str | None,
    out_dir: Path | None,
    as_json: bool,
) -> None:
    """Generate a video from PROMPT."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    prompt_to_send, original_prompt, applied_tool = apply_tool_option(
        prompt, tool_specs, category="video", quiet=as_json
    )
    run_with_handlers(
        lambda: _run_t2v(
            profile_name=profile_name,
            profile_dir=provider_dir,
            prompt=prompt_to_send,
            aspect=aspect,
            out_dir=out_dir,
            model=model,
            duration=int(duration) if duration is not None else None,
            count=count,
            as_json=as_json,
            original_prompt=original_prompt,
            tool=applied_tool,
            project_id=project_id,
        ),
        cli_command="video t2v",
        as_json=as_json,
    )


def _resolve_i2v_args(
    image: str | None,
    prompt: str | None,
    initial_frame: str | None,
) -> tuple[str, str]:
    """Resolve the (frame, prompt) pair from i2v's positional/flag arguments.

    Click fills positional arguments left-to-right (greedy). When --initial-frame
    is used without a positional IMAGE, the sole remaining positional (the PROMPT
    text) lands in the ``image`` slot and ``prompt`` is None. This helper detects
    the swap and returns ``(resolved_frame, resolved_prompt)``; the frame value is
    validated (existing file OR media UUID, #287) by :func:`_classify_frame`.
    """
    if initial_frame is not None and prompt is None and image is not None:
        return initial_frame, image

    if prompt is not None:
        resolved_image = initial_frame or image
        if resolved_image is None:
            raise click.UsageError(
                "Provide an initial frame via --initial-frame or as the first positional argument."
            )
        return resolved_image, prompt

    raise click.UsageError(
        "Missing arguments. Provide PROMPT and an initial frame"
        " (via --initial-frame or as a positional argument)."
    )


def _classify_frame(value: str | None, param_hint: str) -> tuple[str | None, str | None]:
    """Split a frame argument into ``(local_path, media_uuid)`` — #287.

    A value shaped like a Flow media UUID references an existing in-project
    asset (no upload); anything else must be an existing local image file
    (resolved, so symlinks can't launder an arbitrary read — mirrors
    cli_image's ``_classify_ref``).
    """
    if value is None:
        return None, None
    if is_media_uuid(value):
        return None, value
    path = Path(value)
    if not path.is_file():
        raise click.BadParameter(
            f"'{value}' is neither an existing image file nor a media UUID "
            "(32-char hex with hyphens, from the Flow project library).",
            param_hint=param_hint,
        )
    return str(path.resolve()), None


@video.command(
    "i2v",
    short_help="Generate a video from an initial frame + motion prompt.",
    help=(
        "Image-to-video: animate an initial frame with a motion prompt (Veo).\n\n"
        "Only the Veo 3.1 models support i2v interpolation; omni-flash is NOT "
        "accepted here because Flow silently drops the initial and end frames and "
        "falls back to text-to-video (issue #125). Omit --model to use "
        "veo-lite (the default i2v model).\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video i2v --initial-frame hero.png "slow cinematic push-in"\n'
        '  gflow video i2v --initial-frame hero.png --end-frame last.png "pan left" --aspect 16:9\n'
        '  gflow video i2v hero.png "it leaps" --model veo-quality --duration 8\n'
        '  gflow video i2v --initial-frame d6f1927a-3eae-4626-bc90-9a6ea7637bab "pan" '
        "--project f6caf027-...\n"
    ),
)
@click.argument("image", required=False, default=None)
@click.argument("prompt", required=False, default=None)
@click.option(
    "--initial-frame",
    "initial_frame",
    default=None,
    type=str,
    help=(
        "Initial frame to animate: a local image path, or the media UUID of an "
        "existing in-project asset (#287 — no duplicate upload; pair with --project)."
    ),
)
@click.option(
    "--end-frame",
    "end_frame",
    default=None,
    type=str,
    help=(
        "Optional end frame (local path or in-project media UUID) — Flow "
        "interpolates initial frame -> end frame."
    ),
)
@click.option(
    "--end-image",
    "end_image_deprecated",
    default=None,
    hidden=True,
    type=str,
    help="Deprecated: use --end-frame.",
)
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(["9:16", "16:9"]),
    help="Video aspect ratio.",
)
@click.option(
    "--model",
    default=None,
    # omni-flash is intentionally absent: it does not support i2v
    # interpolation and silently routes to T2V (issue #125). Use a Veo 3.1
    # model. Omitting --model resolves to veo-lite (I2V_DEFAULT_MODEL).
    type=click.Choice(["veo-lite", "veo-fast", "veo-quality", "veo-lite-lp"]),
    help="Veo 3.1 model. Omit to use veo-lite (the default i2v model).",
)
@click.option(
    "--duration",
    default=None,
    type=click.Choice(["4", "6", "8"]),
    help="Clip length in seconds (i2v supports 4/6/8; 10 is omni-flash-only).",
)
@click.option(
    "--count",
    default=1,
    show_default=True,
    type=click.IntRange(1, 4),
    help="How many videos to generate (1-4).",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@tool_option
@_project_option
@click.option(
    "--project-name",
    "project_display_name",
    default=None,
    envvar="GFLOW_CLI_PROJECT_NAME",
    type=str,
    help=(
        "Display name of the --project project, used to select it in the media "
        "picker's project menu when attaching a media-UUID frame (#287 — the "
        "menu lists projects by NAME; unnamed projects show only creation "
        "timestamps). Escape hatch when automatic name derivation fails. "
        "Env: GFLOW_CLI_PROJECT_NAME."
    ),
)
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the generated mp4. Defaults to tmp/.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a machine-readable JSON result instead of Rich output.",
)
def i2v(  # NOSONAR
    image: str | None,
    prompt: str | None,
    initial_frame: str | None,
    end_frame: str | None,
    end_image_deprecated: str | None,
    aspect: str,
    model: str | None,
    duration: str | None,
    count: int,
    profile: str | None,
    tool_specs: tuple[str, ...],
    project_id: str | None,
    project_display_name: str | None,
    out_dir: Path | None,
    as_json: bool,
) -> None:
    """Generate a video from an initial frame + motion PROMPT."""
    resolved_image, resolved_prompt = _resolve_i2v_args(image, prompt, initial_frame)

    end_hint = "'--end-frame'"
    if end_image_deprecated is not None:
        warnings.warn(
            "--end-image is deprecated and will be removed in a future release;"
            " use --end-frame instead.",
            DeprecationWarning,
            stacklevel=1,
        )
        if end_frame is None:
            end_frame = end_image_deprecated
            end_hint = "'--end-image'"  # name the flag the user actually typed

    start_path, start_ref_id = _classify_frame(resolved_image, "'IMAGE' / '--initial-frame'")
    end_path, end_ref_id = _classify_frame(end_frame, end_hint)

    # #287 round 6: for UUID frame refs, resolve the assets' recorded prompts
    # into picker search hints (Flow's media search indexes prompt text, not
    # UUIDs). Best-effort — no catalog, no hints.
    search_hints: tuple[str, ...] = ()
    if start_ref_id or end_ref_id:
        search_hints = _media_search_hints([start_ref_id, end_ref_id])

    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    prompt_to_send, original_prompt, applied_tool = apply_tool_option(
        resolved_prompt, tool_specs, category="video", quiet=as_json
    )
    i2v_params = _I2VParams(
        image=start_path,
        prompt=prompt_to_send,
        aspect=aspect,
        image_ref_id=start_ref_id,
        end_frame=end_path,
        end_frame_ref_id=end_ref_id,
        model=model,
        duration=int(duration) if duration is not None else None,
        original_prompt=original_prompt,
        tool=applied_tool,
        project_name=project_display_name,
        search_hints=search_hints,
    )
    run_with_handlers(
        lambda: _run_i2v(
            profile_name=profile_name,
            profile_dir=provider_dir,
            params=i2v_params,
            count=count,
            out_dir=out_dir,
            as_json=as_json,
            project_id=project_id,
        ),
        cli_command="video i2v",
        as_json=as_json,
    )


@video.command(
    "r2v",
    short_help="Generate a video from reference images + prompt (ingredients).",
    help=(
        "Reference-to-video: condition a generation on reference images "
        "(Flow's 'ingredients' / Elementos). Per-model cap: omni-flash accepts "
        "up to 7, veo-lite/veo-fast/veo-lite-lp accept up to 3, veo-quality "
        "does not support R2V at all.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video r2v "knight walks forward" --ref armor.png --model omni-flash\n'
        '  gflow video r2v "they meet" --ref a.png --ref b.png --model veo-fast\n'
    ),
)
@click.argument("prompt")
@click.option(
    "--ref",
    "refs",
    multiple=True,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    help=(
        "Reference image (repeat per ref). Per-model cap enforced by --model: "
        "omni-flash=7, veo-lite/veo-fast/veo-lite-lp=3, veo-quality rejects R2V."
    ),
)
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(["9:16", "16:9"]),
    help="Video aspect ratio.",
)
@click.option(
    "--model",
    default=None,
    type=click.Choice(["omni-flash", "veo-lite", "veo-fast", "veo-quality", "veo-lite-lp"]),
    help="Veo model. Omit to use Flow's current default.",
)
@click.option(
    "--duration",
    default=None,
    type=click.Choice(["4", "6", "8", "10"]),
    help="Clip length in seconds. 10 requires --model omni-flash.",
)
@click.option(
    "--count",
    default=1,
    show_default=True,
    type=click.IntRange(1, 4),
    help="How many videos to generate (1-4).",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@tool_option
@_project_option
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the generated mp4. Defaults to tmp/.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a machine-readable JSON result instead of Rich output.",
)
def r2v(
    prompt: str,
    refs: tuple[str, ...],
    aspect: str,
    model: str | None,
    duration: str | None,
    count: int,
    profile: str | None,
    tool_specs: tuple[str, ...],
    project_id: str | None,
    out_dir: Path | None,
    as_json: bool,
) -> None:
    """Generate a video from reference images (--ref) + PROMPT."""
    # Reject over-cap ref counts (and the unsupported model+R2V combo) at the
    # CLI boundary with a clear message (exit 2) rather than letting the domain
    # ValueError surface as a generic error. GenerateVideoRequest.__post_init__
    # enforces the same caps as an invariant. Mirrors the i2i pattern.
    if model is not None:
        model_enum = VideoModel.from_cli(model)
        assert model_enum is not None  # narrows for type-checkers; from_cli only
        # returns None for input None — we just guarded against that.
        cap = reference_cap_for(model_enum)
        if cap == 0:
            msg = f"{model} does not support R2V (reference-to-video)."
            raise click.UsageError(msg)
        if len(refs) > cap:
            msg = f"{model} allows at most {cap} reference image(s); got {len(refs)}."
            raise click.UsageError(
                msg,
            )

    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    prompt_to_send, original_prompt, applied_tool = apply_tool_option(
        prompt, tool_specs, category="video", quiet=as_json
    )
    run_with_handlers(
        lambda: _run_r2v(
            profile_name=profile_name,
            profile_dir=provider_dir,
            prompt=prompt_to_send,
            refs=refs,
            aspect=aspect,
            model=model,
            duration=int(duration) if duration is not None else None,
            count=count,
            out_dir=out_dir,
            as_json=as_json,
            original_prompt=original_prompt,
            tool=applied_tool,
            project_id=project_id,
        ),
        cli_command="video r2v",
        as_json=as_json,
    )


@video.command(
    "chain",
    short_help="Render a manifest of links into one continuous I2V chain.",
    help=(
        "Sequential last-frame chain: link 0 is text-to-video, every later link "
        "is image-to-video seeded by the previous clip's last frame, giving "
        "visual continuity with no server-side stitching.\n\n"
        "COSTS N CREDITS — one per link in the manifest (minus links already "
        "completed when you --resume-from). Use --dry-run first to print the "
        "plan and the credit estimate without spending anything.\n\n"
        "Only Veo 3.1 models are accepted (omni-flash silently drops the seed "
        "frame and routes to text-to-video, issue #125). The MANIFEST is a JSONL "
        'file: one JSON object per line, each with a required "prompt" and '
        'optional "model"/"duration"/"aspect" overrides.\n\n'
        "Each link is saved as its own mp4. Stitching the clips into a single "
        "file is a follow-up step — use `gflow scene`.\n\n"
        "\b\n"
        "Examples:\n"
        "  gflow video chain story.jsonl --dry-run\n"
        "  gflow video chain story.jsonl --model veo-fast --yes\n"
        "  gflow video chain story.jsonl --resume-from <chain-id>\n"
    ),
)
@click.argument("manifest")
@click.option(
    "--model",
    default="veo-lite",
    show_default=True,
    # omni-flash is intentionally absent: it does not support i2v interpolation
    # and silently routes to T2V (issue #125), which would break every seeded
    # link in the chain. Use a Veo 3.1 model.
    type=click.Choice(["veo-lite", "veo-fast", "veo-quality", "veo-lite-lp"]),
    help="Veo 3.1 model for every link. omni-flash is rejected (no i2v seeding).",
)
@click.option(
    "--max-links",
    "max_links",
    default=None,
    type=click.IntRange(1, None),
    help="Cap the number of links; error (exit 11) if the manifest has more.",
)
@click.option(
    "--yes",
    "-y",
    "yes",
    is_flag=True,
    help="Skip the cost confirmation prompt (each link costs one credit).",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Resolve the manifest and print the plan + credit cost; spend nothing.",
)
@click.option(
    "--resume-from",
    "resume_from",
    default=None,
    help="Resume a prior chain by its chain id; already-paid links are skipped.",
)
@click.option(
    "--jitter",
    default=0.0,
    show_default=True,
    type=click.FloatRange(0.0, None),
    help="Random 0..JITTER second pause between links (anti-bot cadence).",
)
@click.option(
    "--seed-offset",
    "seed_offset",
    default=0,
    show_default=True,
    type=click.IntRange(0, None),
    help="Extract the seed frame this many ms before EOF (fade-to-black guard).",
)
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(["9:16", "16:9"]),
    help="Uniform aspect ratio for every link (continuity requirement).",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@tool_option
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the link mp4s + seed frames. Defaults to the output dir.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a machine-readable JSON result instead of Rich output.",
)
def chain(
    manifest: str,
    model: str | None,
    max_links: int | None,
    yes: bool,
    dry_run: bool,
    resume_from: str | None,
    jitter: float,
    seed_offset: int,
    aspect: str,
    profile: str | None,
    tool_specs: tuple[str, ...],
    out_dir: Path | None,
    as_json: bool,
) -> None:
    """Render the chain MANIFEST (one continuous last-frame I2V sequence)."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_chain(
            profile_name=profile_name,
            profile_dir=provider_dir,
            manifest=manifest,
            model=model,
            aspect=aspect,
            out_dir=out_dir,
            max_links=max_links,
            resume_from=resume_from,
            jitter=jitter,
            seed_offset=seed_offset,
            yes=yes,
            dry_run=dry_run,
            as_json=as_json,
            tool_specs=tool_specs,
        ),
        cli_command="video chain",
        as_json=as_json,
    )


@video.command("batch")
@click.argument("manifest", required=False)
def batch(manifest: str | None) -> None:
    """Run a manifest of video generations (not yet available)."""
    profile_name = _resolve_profile(None)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_batch(manifest=manifest, provider_dir=provider_dir),
        cli_command="video batch",
    )
