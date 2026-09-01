"""`gflow video` command group.

`t2v` and `i2v` drive `UiAutomationTransport.generate_video` with auto-download.
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
    set_interrupt_context,
    slugify_project_name,
    tool_option,
)
from gflow_cli.api import video_extend
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.extend_chain import run_extend_chain
from gflow_cli.api.scene import ConcatInput
from gflow_cli.api.video import VideoModel, is_media_uuid, reference_cap_for
from gflow_cli.config import UiMode, get_settings, parse_jitter_range
from gflow_cli.data.models import AssetKind, OperationKind
from gflow_cli.data.recorder import OperationRecorder, record_failed_operation_safe
from gflow_cli.data.repository import DataRepository, verified_local_path
from gflow_cli.data.store import DataStore
from gflow_cli.errors import ConfigurationError, DataStoreError
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

_project_name_option = click.option(
    "--project-name",
    "--project-title",
    "project_name",
    default=None,
    envvar="GFLOW_CLI_PROJECT_NAME",
    type=str,
    help="Human-readable project title to use when creating a fresh Flow project.",
)

_reference_entity_option = click.option(
    "--reference-entity",
    "reference_entities",
    multiple=True,
    help="Flow CHARACTER entity id to reference for character consistency (repeatable).",
)
_reference_entity_name_option = click.option(
    "--reference-entity-name",
    "reference_entity_names",
    multiple=True,
    help="Display name paired with --reference-entity.",
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


def _shared_gen_tail_options(f: Any) -> Any:
    """Option tail shared verbatim by ``t2v`` and ``i2v`` (profile → json).

    NOTE: the ``--reference-entity`` pair is deliberately NOT here. It is valid
    on ``t2v``/``r2v`` (both carry ``referenceEntities`` on the wire) but the DTO
    rejects it for ``i2v`` (``_validate_i2v_symmetry``: an i2v request must not
    carry reference entities). Registering it on both meant ``i2v`` advertised a
    flag that always raised — so it is applied per-command instead.
    """
    for opt in reversed(
        (
            click.option("--profile", default=None, help="Profile name (overrides default)."),
            tool_option,
            _project_option,
            _project_name_option,
            click.option(
                "--out-dir",
                "out_dir",
                default=None,
                type=click.Path(file_okay=False, path_type=Path),
                help="Directory to save the generated mp4. Defaults to tmp/.",
            ),
            click.option(
                "-o",
                "--output",
                "output_file",
                default=None,
                type=click.Path(path_type=Path),
                help="Explicit output file path for the generated asset.",
            ),
            click.option(
                "--json",
                "as_json",
                is_flag=True,
                help="Emit a machine-readable JSON result instead of Rich output.",
            ),
        )
    ):
        f = opt(f)
    return f


_ui_mode_option = click.option(
    "--ui-mode",
    "ui_mode",
    type=click.Choice([m.value for m in UiMode], case_sensitive=False),
    default=None,
    help=(
        "Which Flow UI arm to require. Video generation only has a classic "
        "driver: 'classic'/'auto' verify the classic editor pre-submit "
        "(best-effort DOM probe) and abort with exit 28 (no credits spent) "
        "if it is unreachable; 'agentic' is not yet supported for video and "
        "is rejected. Overrides GFLOW_CLI_UI_MODE."
    ),
)


def _reject_agentic_ui_mode(ui_mode: str | None) -> None:
    """#299: no agentic VIDEO driver exists — reject the explicit flag at the
    CLI edge (exit 2) instead of letting exit 28's "retry may land it"
    remediation mislead. An env-sourced agentic degrades to classic with a
    warning at the transport instead."""
    if ui_mode == UiMode.AGENTIC.value:
        msg = (
            "--ui-mode agentic is not supported for video generation yet "
            "(no agentic video driver exists; refs #299). Use classic or auto."
        )
        raise click.UsageError(msg)


def _reject_duration_without_control(model: str | None, duration: str | int | None) -> None:
    """#451/#288: reject ``--duration`` on a model that renders no duration control.

    The DTO guards this too (defence in depth for API callers), but a bare
    ``ValueError`` surfaces through the CLI as "Unexpected error." (exit 1) and
    the explanation is lost. Raising a ``UsageError`` here gives exit 2 and
    prints the reason — the same treatment ``--ui-mode agentic`` gets.
    """
    if duration is None or model is None:
        return
    try:
        resolved = VideoModel.from_cli(model)
    except ValueError:
        # Unknown alias: Click's Choice already rejects it on the CLI path, and
        # a programmatic caller deserves that error, not this guard's. Let the
        # real validation report it rather than dying as "Unexpected error."
        return
    # from_cli returns None only for a None argument, which we returned on above.
    if resolved is not None and not resolved.supports_duration():
        msg = (
            f"--duration is not supported by --model {model} — Flow renders no duration "
            f"control for it (verified live; refs #451/#288). Only omni-flash exposes a "
            f"duration (4/6/8/10s). Drop --duration to accept Flow's default length, or "
            f"use --model omni-flash."
        )
        raise click.UsageError(msg)


def _relocate_single_video(item: Any, target: Path) -> Any:
    from dataclasses import replace
    from typing import cast

    local_p = cast("Path | None", getattr(item, "local_path", None))
    if local_p is None or not local_p.exists():
        return item
    if local_p != target:
        local_p.replace(target)
        return replace(item, local_path=target)
    return item


def _relocate_video_output(result: Any, output_file: Path | None) -> Any:
    if output_file is None:
        return result
    from typing import cast

    output_file.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(result, (list, tuple)):
        items = cast("tuple[Any, ...] | list[Any]", result)
        relocated: list[Any] = []
        is_single = len(items) == 1
        for i, item in enumerate(items, start=1):
            target = (
                output_file
                if is_single
                else output_file.parent / f"{output_file.stem}_{i}{output_file.suffix}"
            )
            relocated.append(_relocate_single_video(item, target))
        return relocated

    return _relocate_single_video(result, output_file)


async def _generate_and_report(
    request: Any,
    *,
    profile_name: str,
    profile_dir: Path,
    out_dir: Path | None,
    output_file: Path | None = None,
    command: str = "video",
    as_json: bool = False,
    project_id: str | None = None,
    project_name: str | None = None,
    tool_specs: tuple[str, ...] = (),
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
    started_media_ids: list[str] = []
    try:
        async with FlowApiClient(profile_dir=profile_dir, out_dir=out_dir) as client:
            if project_id is None and project_name is not None:
                p_info = await client.create_project(title=project_name)
                project_id = p_info.project_id

            # Resolve @-mentions and expand --tool specs (shared helper).
            from gflow_cli.services.mentions import resolve_and_apply

            request = await resolve_and_apply(
                client,
                request,
                path="video",
                project_id=project_id,
                tool_specs=tool_specs,
                quiet=as_json,
            )

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
                    _warn_persistence_failed_after_success(
                        exc=exc,
                        flow_media_id=started.media_id,
                        local_path=None,
                    )

            # #546 rename self-healing: on an i2v frame picker miss the
            # transport re-fetches the project listing for the CURRENT name.
            # Shared bridge with the image path; None when there is no project
            # id (t2v scratch projects) — today's behavior then holds. The
            # kwarg is passed only when a resolver exists, so duck-typed
            # client fakes keep their pre-#546 signatures.
            from gflow_cli.cli_image import wire_refresh_resolver

            name_resolver = wire_refresh_resolver(
                client, profile_name=profile_name, project_id=project_id
            )
            resolver_kw: dict[str, Any] = (
                {} if name_resolver is None else {"name_resolver": name_resolver}
            )
            result = await client.generate_video(
                req=request,
                project_id=project_id,
                out_dir=out_dir,
                download=True,
                on_started=on_started,
                **resolver_kw,
            )

        result = _relocate_video_output(result, output_file)

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
    except Exception as exc:
        # #341: persist the failure before re-raising — the STARTED row (if
        # on_started fired) is updated to FAILED, else a fresh row is inserted.
        record_failed_operation_safe(
            recorder,
            logger=logger,
            profile_name=profile_name,
            profile_dir=profile_dir,
            command=f"video {request.mode.value}",
            mode=OperationKind(request.mode.value),
            exc=exc,
            request=request,
            flow_media_ids=started_media_ids,
        )
        raise
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
    output_file: Path | None = None,
    model: str | None = None,
    duration: int | None = None,
    count: int = 1,
    as_json: bool = False,
    reference_entities: tuple[str, ...] = (),
    reference_entity_names: tuple[str, ...] = (),
    original_prompt: str | None = None,
    tool: AppliedTool | None = None,
    project_id: str | None = None,
    project_name: str | None = None,
    tool_specs: tuple[str, ...] = (),
    ui_mode: UiMode | None = None,
) -> None:
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

    effective_title = project_name or slugify_project_name(prompt, prefix="gflow-t2v")
    request = GenerateVideoRequest(
        prompt=prompt,
        mode=Mode.T2V,
        aspect=Aspect.from_cli(aspect),
        model=VideoModel.from_cli(model),
        duration=duration,
        count=count,
        reference_entities=reference_entities,
        reference_entity_names=reference_entity_names,
        original_prompt=original_prompt,
        tool=tool,
        ui_mode=ui_mode,
    )

    await _generate_and_report(
        request,
        profile_name=profile_name,
        profile_dir=profile_dir,
        out_dir=out_dir,
        output_file=output_file,
        command="video t2v",
        as_json=as_json,
        project_id=project_id,
        project_name=effective_title,
        tool_specs=tool_specs,
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
    # Browser-picker search keys resolved from the catalog. UUID remains the
    # exact tile identity after its Flow display name surfaces candidates.
    image_ref_display_name: str = ""
    end_frame_ref_display_name: str = ""
    image_ref_local_path: Path | None = None
    end_frame_ref_local_path: Path | None = None
    image_ref_local_sha256: str = ""
    end_frame_ref_local_sha256: str = ""
    reference_entities: tuple[str, ...] = ()
    reference_entity_names: tuple[str, ...] = ()
    # Requested Flow UI arm (#299); agentic is rejected at the CLI edge.
    ui_mode: UiMode | None = None


def _media_picker_metadata(
    media_ids: Sequence[str | None], profile_name: str
) -> tuple[dict[str, str], dict[str, tuple[Path, str]]]:
    """Resolve UUID frames to picker names and extant local fallbacks.

    The CLI owns catalog access; the transport receives only the name used to
    filter the browser picker and still verifies the exact UUID in the result
    tile's thumbnail URL. Agentic, redacted-history, and legacy rows may lack a
    name; their exact recorded image bytes are the bounded fallback. Best-effort:
    unknown/non-image assets and an unavailable catalog produce no entry.
    """
    ids = tuple(media_id for media_id in media_ids if media_id)
    if not ids:
        return {}, {}
    display_names: dict[str, str] = {}
    local_files: dict[str, tuple[Path, str]] = {}
    try:
        with DataStore.open(get_settings().resolved_db_path()) as store:
            repo = DataRepository(store)
            for media_id in ids:
                asset = repo.get_asset_by_flow_media_id(profile_name, media_id)
                if asset is None or asset.kind is not AssetKind.IMAGE:
                    continue
                display_name = asset.metadata_json.get("display_name")
                if isinstance(display_name, str) and display_name:
                    display_names[media_id] = display_name
                for local_file in asset.local_files:
                    if (path := verified_local_path(local_file)) is not None:
                        assert local_file.sha256 is not None
                        local_files[media_id] = (path, local_file.sha256)
                        break
    except (DataStoreError, OSError) as exc:
        logger.debug("video.frame_ref_enrich_skipped", error=str(exc)[:120])
    return display_names, local_files


async def _run_i2v(
    *,
    profile_name: str,
    profile_dir: Path,
    params: _I2VParams,
    out_dir: Path | None,
    output_file: Path | None = None,
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

    # Resolve the model with i2v-specific defaulting + validation, BEFORE any
    # paid call. Start-frame i2v is supported by every current model (omni
    # included — re-verified on the wire 2026-08-03, refs #125). The END frame
    # is narrower: Flow lists first+last as "coming soon" for omni-flash and
    # there is no wire proof for it, so reject that combination up front — a
    # stale `--config` JSON or direct programmatic call can smuggle it past
    # the Click Choice.
    resolved_model = VideoModel.from_cli(params.model)
    if resolved_model is None:
        resolved_model = I2V_DEFAULT_MODEL
    has_end_frame = params.end_frame is not None or params.end_frame_ref_id is not None
    if has_end_frame and not resolved_model.supports_i2v_end_frame():
        msg = (
            f"{resolved_model.value!r} does not support an END frame "
            f"(first+last interpolation): Flow's official support matrix lists "
            f"it as 'coming soon' for this model and gflow has no wire-level "
            f"proof of the StartAndEndImage route for it (refs #125). Drop "
            f"--end-frame, or use a Veo 3.1 model (e.g. --model veo-lite)."
        )
        raise ModelModeIncompatibilityError(detail=msg)

    effective_title = params.project_name or slugify_project_name(params.prompt, prefix="gflow-i2v")
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
        start_image_ref_display_name=params.image_ref_display_name,
        end_image_ref_display_name=params.end_frame_ref_display_name,
        start_image_ref_local_path=params.image_ref_local_path,
        end_image_ref_local_path=params.end_frame_ref_local_path,
        start_image_ref_local_sha256=params.image_ref_local_sha256,
        end_image_ref_local_sha256=params.end_frame_ref_local_sha256,
        project_name=params.project_name,
        reference_entities=params.reference_entities,
        reference_entity_names=params.reference_entity_names,
        original_prompt=params.original_prompt,
        tool=params.tool,
        ui_mode=params.ui_mode,
    )
    await _generate_and_report(
        request,
        profile_name=profile_name,
        profile_dir=profile_dir,
        out_dir=out_dir,
        output_file=output_file,
        command="video i2v",
        as_json=as_json,
        project_id=project_id,
        project_name=effective_title,
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
    output_file: Path | None = None,
    as_json: bool = False,
    reference_entities: tuple[str, ...] = (),
    reference_entity_names: tuple[str, ...] = (),
    original_prompt: str | None = None,
    tool: AppliedTool | None = None,
    project_id: str | None = None,
    project_name: str | None = None,
    tool_specs: tuple[str, ...] = (),
) -> None:
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel

    effective_title = project_name or slugify_project_name(prompt, prefix="gflow-r2v")
    request = GenerateVideoRequest(
        prompt=prompt,
        mode=Mode.R2V,
        aspect=Aspect.from_cli(aspect),
        model=VideoModel.from_cli(model),
        duration=duration,
        count=count,
        reference_images=tuple(Path(r) for r in refs),
        reference_entities=reference_entities,
        reference_entity_names=reference_entity_names,
        original_prompt=original_prompt,
        tool=tool,
    )

    await _generate_and_report(
        request,
        profile_name=profile_name,
        profile_dir=profile_dir,
        out_dir=out_dir,
        output_file=output_file,
        command="video r2v",
        as_json=as_json,
        project_id=project_id,
        project_name=effective_title,
        tool_specs=tool_specs,
    )


def _resolve_chain_model(model: str | None) -> VideoModel:
    """Resolve + validate the chain-level ``--model`` BEFORE any spend.

    A chain renders link 0 as T2V and every later link as I2V seeded by the
    previous clip's last frame. Chains stay on the Veo 3.1 models:
    ``omni-flash`` start-frame i2v is wire-verified for a single generation
    (2026-08-03, refs #125) but not at chain scale, so it is still rejected
    at the CLI boundary with ``ModelModeIncompatibilityError`` (exit 17). The
    Click ``Choice`` already excludes ``omni-flash``; this guard also covers a
    direct programmatic call or a future alias.
    """
    from gflow_cli.api.video import I2V_DEFAULT_MODEL
    from gflow_cli.errors import ModelModeIncompatibilityError

    resolved = VideoModel.from_cli(model)
    if resolved is None:
        return I2V_DEFAULT_MODEL
    if resolved is VideoModel.OMNI_FLASH:
        msg = (
            f"{resolved.value!r} is not accepted for chains. Chains render N "
            f"seeded i2v links back-to-back; omni-flash start-frame i2v was "
            f"wire-verified for a single generation on 2026-08-03 (refs #125) "
            f"but has not been proven at chain scale, so chains stay on the "
            f"long-verified Veo 3.1 models (e.g. --model veo-lite). Single "
            f"clips: `gflow video i2v --model omni-flash` is available."
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
    operation_noun = "operation" if remaining == 1 else "operations"
    console.print(f"[bold]Chain plan[/bold] ([dim]{chain_id}[/dim])")
    console.print(
        f"  {len(typed_links)} link(s), aspect {aspect}, model {model.value}"
        + (f" — {skipped} already completed, {remaining} to generate" if skipped else "")
    )
    console.print(f"  [yellow]{remaining} pending video {operation_noun}[/yellow]")
    console.print(
        "  Video operations may consume credits. Current cost varies by model, duration, "
        "account tier, and Flow policy; check Google Flow before submitting."
    )
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

    # (request, media_id) pairs — matched by object IDENTITY in _on_link_failed.
    # A dict keyed by id(request) would hold no reference to the request, so a
    # GC'd earlier link's address could be reused by a later link (review
    # finding); keeping the object itself in the pair removes the hazard.
    started_media_pairs: list[tuple[GenerateVideoRequest, str]] = []

    def _on_link_started(request: GenerateVideoRequest) -> Any:
        """Build the per-link ``on_started`` forwarded into ``generate_video``."""

        def on_started(started: VideoStarted) -> None:
            started_media_pairs.append((request, started.media_id))
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

    def _on_link_failed(request: GenerateVideoRequest, exc: BaseException) -> None:
        """Persist a FAILED catalog row for the aborted link (#341)."""
        record_failed_operation_safe(
            catalog_recorder,
            logger=logger,
            profile_name=profile_name,
            profile_dir=profile_dir,
            command="video chain",
            mode=OperationKind(request.mode.value),
            exc=exc,
            request=request,
            flow_media_ids=[mid for req, mid in started_media_pairs if req is request],
        )

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
            on_link_failed=_on_link_failed,
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
    output_file: Path | None = None,
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

    The submission gate (``--yes`` / confirm), ``--max-links`` cap, ``--dry-run``
    short-circuit, and ``--resume-from`` completed-link filtering all run BEFORE
    a client is created so a rejected/dry run submits nothing and opens no browser.
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

    # Resume: bind the prior chain_id and skip completed links so they are not
    # regenerated. A fresh run mints a new id.
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
    pending_operations = len(remaining_links)

    if dry_run:
        _print_chain_plan(
            links=links,
            model=resolved_model,
            aspect=aspect,
            skipped=skipped,
            chain_id=chain_id,
        )
        console.print("[dim]--dry-run: no video operations submitted, no clips generated.[/dim]")
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
        operation_noun = "operation" if pending_operations == 1 else "operations"
        click.confirm(
            f"Submit {pending_operations} pending video {operation_noun}?",
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
        '  gflow video t2v "portrait of a dancer" --out-dir ./videos\n\n'
        "Tag a saved character by name inline with @Name, or pass its id with "
        "--reference-entity. See docs/REFERENCE_STRATEGIES.md."
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
    help=(
        "Clip length in seconds. REQUIRES --model omni-flash: the Veo 3.1 "
        "models render no duration control in Flow, so no length can be "
        "selected for them (refs #451/#288). Omit for Flow's default length."
    ),
)
@click.option(
    "--count",
    default=1,
    show_default=True,
    type=click.IntRange(1, 4),
    help="How many videos to generate (1-4). >1 multiplies credit cost.",
)
@_ui_mode_option
# Valid on t2v (carries referenceEntities on the wire); NOT on i2v, whose DTO
# guard rejects reference entities — see _shared_gen_tail_options.
@_reference_entity_option
@_reference_entity_name_option
@_shared_gen_tail_options
def t2v(
    prompt: str,
    aspect: str,
    model: str | None,
    duration: str | None,
    count: int,
    ui_mode: str | None,
    profile: str | None,
    tool_specs: tuple[str, ...],
    project_id: str | None,
    project_name: str | None,
    reference_entities: tuple[str, ...],
    reference_entity_names: tuple[str, ...],
    out_dir: Path | None,
    output_file: Path | None,
    as_json: bool,
) -> None:
    """Generate a video from PROMPT."""
    _reject_agentic_ui_mode(ui_mode)
    _reject_duration_without_control(model, duration)
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_t2v(
            profile_name=profile_name,
            profile_dir=provider_dir,
            prompt=prompt,
            aspect=aspect,
            out_dir=out_dir,
            output_file=output_file,
            model=model,
            duration=int(duration) if duration is not None else None,
            count=count,
            as_json=as_json,
            reference_entities=tuple(reference_entities),
            reference_entity_names=tuple(reference_entity_names),
            original_prompt=None,
            tool=None,
            project_id=project_id,
            project_name=project_name,
            tool_specs=tool_specs,
            ui_mode=UiMode(ui_mode) if ui_mode else None,
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
    try:
        resolved = Path(value).resolve(strict=True)
    except FileNotFoundError as exc:
        msg = f"{param_hint} path {value!r} does not exist."
        raise click.UsageError(msg) from exc
    return str(resolved), None


@video.command(
    "i2v",
    short_help="Generate a video from an initial image + motion prompt.",
    help=(
        "Generate a video from an initial image frame and a motion PROMPT using Veo.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video i2v ./hero.png "camera zooms in on the character"\n'
        '  gflow video i2v --initial-frame ./hero.png "slow pan left"\n'
        '  gflow video i2v --initial-frame ./start.png --end-frame ./end.png "morph"\n'
        '  gflow video i2v <MEDIA_UUID> "camera zooms in"  # pre-uploaded asset\n\n'
        "Positional ordering: when --initial-frame is passed, only PROMPT is required."
    ),
)
@click.argument("image", required=False, default=None)
@click.argument("prompt", required=False, default=None)
@click.option(
    "--initial-frame",
    "-i",
    "initial_frame",
    default=None,
    help=(
        "Initial frame to animate: a local file path, or the media UUID of an "
        "existing in-project asset."
    ),
)
@click.option(
    "--end-frame",
    "-e",
    "end_frame",
    default=None,
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
    help="Deprecated alias for --end-frame.",
)
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
    help=(
        "Video model. Defaults to veo-lite (cheapest). omni-flash supports the "
        "start frame only (--end-frame requires a Veo 3.1 model) and unlocks "
        "--duration 10."
    ),
)
@click.option(
    "--duration",
    default=None,
    type=click.Choice(["4", "6", "8", "10"]),
    help=(
        "Clip length in seconds. REQUIRES --model omni-flash: the Veo 3.1 "
        "models render no duration control in Flow, so no length can be "
        "selected for them (refs #451/#288). Omit for Flow's default length."
    ),
)
@click.option(
    "--count",
    default=1,
    show_default=True,
    type=click.IntRange(1, 4),
    help="How many videos to generate (1-4; >1 multiplies credit cost).",
)
@_ui_mode_option
@_shared_gen_tail_options
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
    ui_mode: str | None,
    profile: str | None,
    tool_specs: tuple[str, ...],
    project_id: str | None,
    project_name: str | None,
    out_dir: Path | None,
    output_file: Path | None,
    as_json: bool,
) -> None:
    """Generate a video from an initial frame + motion PROMPT."""
    _reject_agentic_ui_mode(ui_mode)
    _reject_duration_without_control(model, duration)
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

    profile_name = _resolve_profile(profile)
    display_names, local_files = _media_picker_metadata([start_ref_id, end_ref_id], profile_name)
    start_local = local_files.get(start_ref_id or "")
    end_local = local_files.get(end_ref_id or "")
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
        project_name=project_name,
        image_ref_display_name=display_names.get(start_ref_id or "", ""),
        end_frame_ref_display_name=display_names.get(end_ref_id or "", ""),
        image_ref_local_path=start_local[0] if start_local else None,
        end_frame_ref_local_path=end_local[0] if end_local else None,
        image_ref_local_sha256=start_local[1] if start_local else "",
        end_frame_ref_local_sha256=end_local[1] if end_local else "",
        ui_mode=UiMode(ui_mode) if ui_mode else None,
    )
    run_with_handlers(
        lambda: _run_i2v(
            profile_name=profile_name,
            profile_dir=provider_dir,
            params=i2v_params,
            count=count,
            out_dir=out_dir,
            output_file=output_file,
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
        '  gflow video r2v "they meet" --ref a.png --ref b.png --model veo-fast\n\n'
        "Tag a saved character by name inline with @Name (--ref stays for one-off "
        "ingredient images). See docs/REFERENCE_STRATEGIES.md."
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
    help=(
        "Clip length in seconds. REQUIRES --model omni-flash: the Veo 3.1 "
        "models render no duration control in Flow, so no length can be "
        "selected for them (refs #451/#288). Omit for Flow's default length."
    ),
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
@_project_name_option
@_reference_entity_option
@_reference_entity_name_option
@click.option(
    "-o",
    "--output",
    "output_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Explicit output file path for the generated asset.",
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
    project_name: str | None,
    reference_entities: tuple[str, ...],
    reference_entity_names: tuple[str, ...],
    output_file: Path | None,
    out_dir: Path | None,
    as_json: bool,
) -> None:
    """Generate a video from reference images (--ref) + PROMPT."""
    # Reject over-cap ref counts (and the unsupported model+R2V combo) at the
    # CLI boundary with a clear message (exit 2) rather than letting the domain
    # ValueError surface as a generic error. GenerateVideoRequest.__post_init__
    # enforces the same caps as an invariant. Mirrors the i2i pattern.
    # r2v carries --duration too, so it needs the same CLI-edge guard t2v/i2v
    # get; without it the DTO's ValueError surfaces as "Unexpected error." exit 1
    # and the explanation is lost (refs #451/#288).
    _reject_duration_without_control(model, duration)
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
    run_with_handlers(
        lambda: _run_r2v(
            profile_name=profile_name,
            profile_dir=provider_dir,
            prompt=prompt,
            refs=refs,
            aspect=aspect,
            model=model,
            duration=int(duration) if duration is not None else None,
            count=count,
            out_dir=out_dir,
            output_file=output_file,
            as_json=as_json,
            reference_entities=tuple(reference_entities),
            reference_entity_names=tuple(reference_entity_names),
            original_prompt=None,
            tool=None,
            project_id=project_id,
            project_name=project_name,
            tool_specs=tool_specs,
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
        "Each unfinished link is a pending video operation. Current credit use "
        "varies by model, duration, account tier, and Flow policy; check Google "
        "Flow before submitting. Use --dry-run first to print the plan without "
        "submitting anything.\n\n"
        "Only Veo 3.1 models are accepted (omni-flash is single-clip only for "
        "now — not proven at chain scale, refs #125). The MANIFEST is a JSONL "
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
    # omni-flash is intentionally absent: single-clip start-frame i2v was
    # wire-verified 2026-08-03 (refs #125), but chains render N seeded links
    # back-to-back and stay on the long-verified Veo 3.1 models for now.
    type=click.Choice(["veo-lite", "veo-fast", "veo-quality", "veo-lite-lp"]),
    help="Veo 3.1 model for every link. omni-flash is rejected (single-clip i2v only).",
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
    help="Skip the pending video operation confirmation prompt.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Resolve the manifest and print the pending operation plan; submit nothing.",
)
@click.option(
    "--resume-from",
    "resume_from",
    default=None,
    help="Resume a prior chain by its chain id; already-completed links are skipped.",
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
    "-o",
    "--output",
    "output_file",
    type=click.Path(path_type=Path),
    default=None,
    help="Explicit output file path for the generated chain clips.",
)
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
    output_file: Path | None,
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
            output_file=output_file,
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


# --------------------------------------------------------------------------
# video extend
# --------------------------------------------------------------------------

# One extend segment is always 8s — the model's videoLengthSeconds, not a
# request parameter. inputSpec.maxInputV2vVideoDuration is also 8, which is why
# a long video is a chain of 8s continuations rather than one growing clip.
_EXTEND_SEGMENT_SECONDS = 8


def _configured_jitter_max() -> float:
    """Upper bound of GFLOW_CLI_JITTER_RANGE, or a safe default.

    Never 0 by default: an unpaced chain submits on a machine-perfect cadence,
    which is a cleaner bot signature than the rate itself.
    """
    spec = get_settings().jitter_range
    if not spec:
        return 15.0
    try:
        return float(parse_jitter_range(spec)[1])
    except (ValueError, TypeError):
        return 15.0


def _print_extend_plan(*, media_id: str, prompt: str, aspect: str, segments: int) -> None:
    """Show what will be submitted BEFORE a client exists.

    Deliberately printed without opening a browser: `--dry-run` must be instant
    and must not be able to spend. The exact credit cost is tier-dependent and
    comes from the (free) capability listing once the run starts; the balance
    check that uses it aborts before the first submit.
    """
    total = segments * _EXTEND_SEGMENT_SECONDS
    console.print("[bold]Extend plan[/bold]")
    console.print(f"  source clip : {media_id}")
    console.print(f"  prompt      : {prompt}")
    console.print(f"  aspect      : {aspect}")
    console.print(
        f"  segments    : {segments} x {_EXTEND_SEGMENT_SECONDS}s = {total}s of new footage"
    )
    console.print("  cost        : spends credits — exact amount is shown before submitting")


async def _run_extend(  # noqa: PLR0913
    *,
    profile_name: str,
    profile_dir: Path,
    media_id: str,
    prompts: tuple[str, ...],
    segments: int,
    aspect: str,
    jitter: float | None,
    output_file: Path | None,
    project_id: str | None,
    scene_id: str | None,
    seed: int | None,
    as_json: bool,
) -> None:
    """Submit N chained extends, then optionally render them to one file."""
    if not project_id:
        msg = "--project is required: extend must know which project owns MEDIA_ID"
        raise ConfigurationError(msg)

    # `chain` defaults jitter to 0.0, which ships unpaced runs and contradicts
    # ACCOUNT_SAFETY's "submissions are paced". Extend inherits the configured
    # range instead; an explicit --jitter 0 still disables it.
    effective_jitter = jitter if jitter is not None else _configured_jitter_max()

    recorder: OperationRecorder | None = None
    try:
        store = DataStore.open(get_settings().resolved_db_path())
        recorder = OperationRecorder(DataRepository(store), prompt_mode=get_settings().history_prompts)
    except DataStoreError as exc:  # catalog is a convenience, never a gate
        logger.warning("extend_recorder_unavailable", error_class=type(exc).__name__)

    async with FlowApiClient(profile_dir=profile_dir) as client:
        listing = await client.capability_listing(project_id)
        tier = video_extend.account_service_tier(listing)
        model_key = video_extend.resolve_extend_model(listing, service_tier=tier, aspect=aspect)
        unit = video_extend.model_unit_cost(listing, model_key, tier)
        balance = video_extend.account_credits(listing)

        # Pre-flight balance check for the WHOLE run. `chain` never knew prices
        # so it could not do this; stopping here beats stopping at segment 6
        # holding a half-length video and a spent balance.
        if unit is not None and balance is not None and balance < unit * segments:
            msg = (
                f"insufficient credits: {segments} segment(s) cost {unit * segments}, "
                f"balance is {balance}. Nothing was submitted."
            )
            raise ConfigurationError(msg)
        if not as_json:
            cost = "unknown" if unit is None else str(unit * segments)
            have = "unknown" if balance is None else str(balance)
            console.print(f"  model       : {model_key} ({cost} credits total, balance {have})")

        # Resuming: read the scene back so the run appends after the clips that
        # are already there, seeding from the real tail rather than the original.
        target_scene = scene_id
        start_position = 1
        source_media = media_id
        if target_scene:
            existing = await client.get_scene_workflows(target_scene, project_id=project_id)
            clips = sorted(existing.workflows, key=lambda w: w.metadata.position)
            if clips:
                start_position = len(clips)
                tail = clips[-1]
                if tail.media_id:
                    source_media = tail.media_id
                if not as_json:
                    console.print(
                        f"  resuming    : scene has {len(clips)} clip(s), "
                        f"continuing from {source_media}"
                    )
        if not target_scene:
            workflow_id = video_extend.workflow_id_for_media(listing, media_id)
            if not workflow_id:
                msg = (
                    f"media {media_id} is not in project {project_id} "
                    "(no workflow owns it) — check --project"
                )
                raise ConfigurationError(msg)
            scene = await client.create_scene(project_id=project_id, workflow_ids=[workflow_id])
            target_scene = scene.scene_id

        # Publish the resume handle BEFORE the first submit, so an interrupt at
        # any point has something to report rather than only on a clean failure.
        set_interrupt_context(credits_spent=0, resume_id=target_scene, segments_done=0)

        def _on_submitted(started: Any) -> None:
            if not as_json:
                console.print(f"  submitted   : {started.media_id} ({started.model_key})")
            # Persist at SUBMIT: Flow bills on acceptance, so a row written only
            # after the ~2 min poll would leave an interrupted run's paid media
            # invisible to `gflow data`. Never fatal — a catalog write must not
            # sink a generation the user has already paid for.
            if recorder is not None:
                try:
                    recorder.record_started_extend(
                        profile_name=profile_name,
                        profile_dir=profile_dir,
                        project_id=project_id,
                        prompt=prompts[0],
                        aspect=aspect,
                        started=started,
                    )
                except DataStoreError as exc:
                    logger.warning("extend_record_failed", error_class=type(exc).__name__)

        result = await run_extend_chain(
            client,
            media_id=source_media,
            start_position=start_position,
            project_id=project_id,
            scene_id=target_scene,
            prompts=prompts,
            segments=segments,
            aspect=aspect,
            seed=seed,
            jitter=effective_jitter,
            on_submitted=_on_submitted,
        )
        set_interrupt_context(
            credits_spent=result.credits_spent,
            resume_id=target_scene,
            segments_done=len(result.completed_media_ids),
        )

        rendered: str | None = None
        # Render even a partial chain: those segments are generated and billed,
        # and discarding them because the run did not finish wastes real money.
        if output_file is not None and result.completed_media_ids:
            scene_state = await client.get_scene_workflows(target_scene, project_id=project_id)
            inputs = [
                ConcatInput(
                    media_id=w.media_id,
                    length=w.metadata.total_duration,
                    start=w.metadata.start_time,
                    end=w.metadata.end_time,
                )
                for w in sorted(scene_state.workflows, key=lambda x: x.metadata.position)
                if w.media_id
            ]
            if not as_json:
                console.print(f"  rendering   : {len(inputs)} clips -> {output_file}")
            await client.concatenate_scene(inputs, out_path=output_file)
            rendered = str(output_file)

        payload = {
            "scene_id": target_scene,
            "project_id": project_id,
            "model": model_key,
            "segments_requested": segments,
            "segments_completed": len(result.completed_media_ids),
            "media_ids": result.completed_media_ids,
            "credits_spent": result.credits_spent,
            "seconds_added": len(result.completed_media_ids) * _EXTEND_SEGMENT_SECONDS,
            "output": rendered,
            "aborted": result.aborted,
            "profile": profile_name,
        }
        if as_json:
            json_output.emit(payload)
        else:
            state = (
                "[yellow]Partial[/yellow]"
                if result.aborted
                else "[bold green]Extended[/bold green]"
            )
            console.print(
                f"{state} — {len(result.completed_media_ids)}/{segments} segment(s), "
                f"{result.credits_spent} credits, scene {target_scene}"
            )
            if rendered:
                console.print(f"  wrote: {rendered}")
            elif result.completed_media_ids:
                console.print("  render one file: gflow scene create --output <path>")

        # A chain that abandoned paid work must not exit 0 and look complete.
        if result.aborted and result.error is not None:
            raise result.error


@video.command(
    "extend",
    short_help="Continue an existing clip by another 8 seconds (costs credits).",
    help=(
        "Continue an existing video by generating another 8-second segment that "
        "carries its motion and audio forward.\n\n"
        "MEDIA_ID is the clip to continue; PROMPT says what happens next.\n\n"
        "Unlike `video chain`, which restarts from an extracted still, extend is "
        "seeded server-side from the source clip, so the join is continuous. "
        "The result lands as a Scene; render it to one file with "
        "`gflow scene create --output`.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video extend <media-id> "the wave recedes back into the ocean"\n'
        '  gflow video extend <media-id> "the camera drifts upward" --aspect 16:9\n\n'
        "Each segment is 8 seconds and spends credits — the exact cost depends on "
        "your plan and is shown for confirmation before anything is submitted."
    ),
)
@click.argument("media_id")
@click.argument("prompts", nargs=-1, required=True)
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    # Flow publishes no SQUARE extend model in either family, so 1:1 is refused
    # here rather than surfacing as a late, paid-for failure.
    type=click.Choice(["9:16", "16:9"]),
    help="Aspect of the extension (portrait 9:16 or landscape 16:9). No square variant exists.",
)
@click.option(
    "--segments",
    "-n",
    default=None,
    type=click.IntRange(1, 30),
    help=(
        "How many 8s continuations to chain (default: one per PROMPT). Capped at "
        "30 — beyond that the credit cost and per-profile load exceed anything "
        "this tool has measured as safe."
    ),
)
@click.option(
    "--jitter",
    default=None,
    type=float,
    help=(
        "Max seconds of random pause between submissions. Defaults to the "
        "GFLOW_CLI_JITTER_RANGE setting; 0 disables pacing (not advised)."
    ),
)
@click.option(
    "-o",
    "--output",
    "output_file",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Render the finished scene to ONE mp4 here (server-side concat, credit-free).",
)
@_project_option
@click.option("--scene", "scene_id", default=None, help="Existing scene to extend within.")
@click.option(
    "--resume-from",
    "resume_from",
    default=None,
    help=(
        "Scene id from an interrupted or partial run. Continues appending to that "
        "scene instead of creating a new one, so already-billed segments are kept."
    ),
)
@click.option("--seed", default=None, type=int, help="Fixed seed, for a reproducible run.")
@click.option("--yes", is_flag=True, help="Skip the cost confirmation.")
@click.option("--dry-run", is_flag=True, help="Show the plan and cost, submit nothing.")
@click.option("--profile", default=None, help="Auth profile to use.")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def extend(  # noqa: PLR0913
    media_id: str,
    prompts: tuple[str, ...],
    aspect: str,
    segments: int | None,
    jitter: float | None,
    output_file: Path | None,
    project_id: str | None,
    scene_id: str | None,
    resume_from: str | None,
    seed: int | None,
    yes: bool,
    dry_run: bool,
    profile: str | None,
    as_json: bool,
) -> None:
    """Continue MEDIA_ID, one PROMPT per 8-second segment."""
    # --resume-from and --scene are the same knob wearing two names; resume is
    # the one the interrupt banner advertises, so it wins.
    scene_id = resume_from or scene_id
    if scene_id is not None and not is_media_uuid(scene_id):
        msg = f"scene id must be a UUID, got {scene_id!r}"
        raise click.BadParameter(msg)
    if not is_media_uuid(media_id):
        msg = f"MEDIA_ID must be a media UUID, got {media_id!r}"
        raise click.BadParameter(msg)
    count = segments if segments is not None else len(prompts)
    # The cost gate runs before a client exists, so --dry-run cannot spend and
    # cannot even open a browser.
    _print_extend_plan(media_id=media_id, prompt=prompts[0], aspect=aspect, segments=count)
    if dry_run:
        return
    if not yes:
        click.confirm(f"Submit {count} extend segment(s)?", abort=True)

    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    run_with_handlers(
        lambda: _run_extend(
            profile_name=profile_name,
            profile_dir=provider_dir,
            media_id=media_id,
            prompts=tuple(prompts),
            segments=count,
            aspect=aspect,
            jitter=jitter,
            output_file=output_file,
            project_id=project_id,
            scene_id=scene_id,
            seed=seed,
            as_json=as_json,
        ),
        cli_command="video extend",
        as_json=as_json,
    )
