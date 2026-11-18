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
    OutputFormat,
    _make_provider_dir,
    _resolve_profile,
    _validate_project_id,
    apply_tool_option,
    default_project,
    out_file_option,
    output_format_option,
    run_with_handlers,
    set_interrupt_context,
    slugify_project_name,
    spends_credits,
    tool_option,
    upscale_out_dir_option,
)
from gflow_cli.api import video_extend
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.extend_chain import run_extend_chain
from gflow_cli.api.transports import migrated_extend
from gflow_cli.api.transports._common import migrated_route
from gflow_cli.api.video import (
    I2V_DEFAULT_MODEL,
    VideoModel,
    is_media_uuid,
    reference_cap_for,
    validate_duration_for_model,
)
from gflow_cli.cli_video_reporting import (
    _chain_json,
    _emit_chain_result,
    _emit_extend_session_result,
    _emit_video_result,
    _free_rerender_hint,
    _print_chain_plan,
    _print_extend_plan,
    _report_migrated_extend,
)
from gflow_cli.config import UiMode, get_settings
from gflow_cli.data.models import AssetKind, OperationKind
from gflow_cli.data.recorder import OperationRecorder, record_failed_operation_safe
from gflow_cli.data.repository import DataRepository, verified_local_path
from gflow_cli.data.store import DataStore
from gflow_cli.errors import (
    ConfigurationError,
    DataStoreError,
    GFlowError,
    SceneConcatError,
)
from gflow_cli.image_batch import resolve_jitter_range
from gflow_cli.paths import (
    chain_clip_relocator,
    extend_output_path,
    relocate_video_output,
)
from gflow_cli.storage import cloud_info_from_path, refuse_cloud_storage_for_local_file

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from gflow_cli.api.video import VideoResult
    from gflow_cli.api.video_extend import ExtendStarted
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
_resolution_option = click.option(
    "--resolution",
    default=None,
    type=click.Choice(["360p", "720p"], case_sensitive=False),
    help=(
        "Video resolution ('360p' or '720p'). Omit for Flow's default. Only models whose "
        "settings show a resolution row offer it (Omni Flash 1.1/1.2); on any other model, or on "
        "the labs editor, the run stops before submit (exit 11), with no credits spent."
    ),
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
        # Since #900 every sqlite fault in a catalog write arrives as DataStoreError, a
        # real bug as well as a lock; the class alone no longer tells them apart.
        detail=getattr(exc, "detail", None) or str(exc),
        route=getattr(exc, "route", None),
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
            out_file_option(
                help="Explicit output file path for the generated asset.",
            ),
            output_format_option(has_out_file=True),
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
        "Which Flow UI arm to require. Labs video only has a classic "
        "driver: 'classic'/'auto' verify the classic editor pre-submit "
        "(best-effort DOM probe) and abort with exit 28 (no credits spent) "
        "if it is unreachable; 'agentic' is not yet supported for video and "
        "is rejected. An agent-only flow.google.com composer (#799) is routed "
        "to its own driver first, so t2v runs there. Overrides GFLOW_CLI_UI_MODE."
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


def _reject_duration_without_control(
    model: str | None,
    duration: str | int | None,
    *,
    default_model: VideoModel | None = None,
    end_frame: bool = False,
) -> None:
    """Validate ``--duration`` when this command has a known model.

    The DTO guards this too (defence in depth for API callers), but a bare
    ``ValueError`` surfaces through the CLI as "Unexpected error." (exit 1) and
    the explanation is lost. Raising a ``UsageError`` here gives exit 2 and
    prints the reason — the same treatment ``--ui-mode agentic`` gets.

    ``default_model`` is the model this command binds when the user passes no
    ``--model`` (#630). Pass it only where gflow *knows* that default: `i2v`
    binds ``I2V_DEFAULT_MODEL``, so an omitted flag there is not "no model" and
    the guard must still run — otherwise the most natural way to try
    ``--duration`` skips this check and dies as "Unexpected error." `t2v`/`r2v`
    inherit Flow's sticky UI default, which gflow cannot know, so they pass
    nothing and are held to the legacy 4/6/8/10 set; only an explicit model
    unlocks other lengths (omni-1.2-flash's 3–15).
    """
    if duration is None:
        return
    if model is None:
        resolved = default_model
    else:
        try:
            resolved = VideoModel.from_cli(model)
        except ValueError:
            # Unknown alias: Click's Choice already rejects it on the CLI path, and
            # a programmatic caller deserves that error, not this guard's. Let the
            # real validation report it rather than dying as "Unexpected error."
            return
    try:
        validate_duration_for_model(resolved, duration, end_frame=end_frame)
    except ValueError as exc:
        if model is not None:
            named = f"--model {model}"
        elif resolved is not None:
            named = f"the default model {resolved.value}"
        else:
            named = "without --model"
        raise click.UsageError(f"{named}: {exc}") from exc


async def _generate_and_report(
    request: Any,
    *,
    profile_name: str,
    profile_dir: Path,
    out_dir: Path | None,
    output_file: Path | None = None,
    command: str = "video",
    as_json: bool = False,
    output_format: str = "table",
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

    settings = get_settings()
    is_json = as_json or output_format == OutputFormat.JSON.value
    quiet_output = is_json or output_format == OutputFormat.ID.value
    if output_file is not None:
        refuse_cloud_storage_for_local_file(settings.storage_uri, "--out-file")
    if not quiet_output:
        console.print("[dim]Generating video — this takes ~2 minutes…[/dim]")
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
                quiet=quiet_output,
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

        result = relocate_video_output(result, output_file)

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

    _emit_video_result(
        command=command,
        request=request,
        result=result,
        output_format=output_format,
        is_json=is_json,
        console=console,
    )


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
    resolution: str | None = None,
    count: int = 1,
    as_json: bool = False,
    output_format: str = "table",
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
        resolution=resolution,
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
        output_format=output_format,
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
    resolution: str | None = None
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
    output_format: str = "table",
    project_id: str | None = None,
) -> None:
    from gflow_cli.api.video import (
        I2V_DEFAULT_MODEL,
        Aspect,
        GenerateVideoRequest,
        Mode,
        VideoModel,
    )

    # Resolve the model with i2v-specific defaulting, BEFORE any paid call.
    # No model/frame combination is rejected here any more: every current model
    # carries both start-only and start+end i2v. omni-flash's start frame was
    # wire-verified 2026-08-03 and its END frame on 2026-09-02 (#626) — two
    # accounts, route-aborted at zero credits, both firing
    # ``batchAsyncGenerateVideoStartAndEndImage`` with a non-null ``endImage``.
    # A partial regression (Flow silently dropping the end frame back to the
    # StartImage route) is caught post-submit by the transport's route
    # backstop, which fails the run rather than bill for a clip that ignored
    # the frame.
    resolved_model = VideoModel.from_cli(params.model)
    if resolved_model is None:
        resolved_model = I2V_DEFAULT_MODEL

    effective_title = params.project_name or slugify_project_name(params.prompt, prefix="gflow-i2v")
    request = GenerateVideoRequest(
        prompt=params.prompt,
        mode=Mode.I2V,
        aspect=Aspect.from_cli(params.aspect),
        model=resolved_model,
        duration=params.duration,
        resolution=params.resolution,
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
        output_format=output_format,
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
    resolution: str | None = None,
    count: int = 1,
    output_file: Path | None = None,
    as_json: bool = False,
    output_format: str = "table",
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
        resolution=resolution,
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
        output_format=output_format,
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
    if resolved in (VideoModel.OMNI_FLASH, VideoModel.OMNI_1_2_FLASH):
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


def _resolve_chain_resume(
    resume_from: str | None,
    links: list[Any],
    *,
    settings: Any,
    profile_name: str,
    profile_dir: Path,
) -> tuple[str, int, Path | None]:
    """Resolve the chain_id, the number of already-completed links and the last one's clip.

    For a fresh run mints a new UUID.  For a resume, opens the chain recorder
    to count completed links and returns early (raises SystemExit via
    console.print + return sentinel) when the chain is already done.
    Returns ``(chain_id, skipped, seed_clip)`` — callers check ``skipped >= len(links)``
    themselves. ``seed_clip`` seeds the first remaining link (#1300); a resume whose clip
    is gone is refused here, before the cost prompt, rather than sent as an unseeded t2v.
    """
    import uuid

    from gflow_cli.data.chain_repo import ChainLinkRecorder
    from gflow_cli.errors import FrameExtractionError

    if resume_from is None:
        return str(uuid.uuid4()), 0, None

    chain_id = resume_from
    probe = ChainLinkRecorder.open(
        settings,
        profile_name=profile_name,
        profile_dir=profile_dir,
        chain_id=chain_id,
    )
    try:
        completed = probe.completed_links()
    finally:
        probe.close()
    seed_clip = Path(completed[-1].local_path) if completed else None
    if seed_clip is not None and len(completed) < len(links) and not seed_clip.is_file():
        raise FrameExtractionError(
            detail=f"cannot resume chain {chain_id}: the clip of its last completed link, "
            f"{seed_clip}, is missing, so the next link has nothing to continue from",
            remediation_hint="Put the clip back at that path, or start a new chain.",
            charged="no",
        )
    return chain_id, len(completed), seed_clip


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
    relocate: Callable[[int, VideoResult], VideoResult] | None = None
    start_index: int = 0
    seed_clip: Path | None = None


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

    from gflow_cli.api.video import GenerateVideoRequest, VideoStarted
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
            relocate=cfg.relocate,
            start_index=cfg.start_index,
            seed_clip=cfg.seed_clip,
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
                    error=exc,
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
    output_format: str = "table",
    tool_specs: tuple[str, ...] = (),
) -> None:
    """Drive a sequential last-frame I2V chain from a JSONL manifest.

    The submission gate (``--yes`` / confirm), ``--max-links`` cap, ``--dry-run``
    short-circuit, and ``--resume-from`` completed-link filtering all run BEFORE
    a client is created so a rejected/dry run submits nothing and opens no browser.
    """
    from pathlib import Path as _Path

    from gflow_cli.errors import ChainManifestError, FrameExtractionError

    # `gflow_cli.chain` pulls in `gflow_cli.media`, which imports BOTH optional
    # `[chain]` dependencies (av, pillow) at module level. Guarding here — the
    # first statement of the command, ahead of the manifest read, the --dry-run
    # short-circuit and the cost prompt — turns a missing extra into a typed
    # exit 20 with an install hint instead of the generic "file a bug" exit 1
    # that `gflow-cli[chain]==0.74.0` shipped (#813).
    try:
        from gflow_cli import chain as chain_mod
        from gflow_cli.api.video import Aspect
        from gflow_cli.chain import reject_unusable_links
        from gflow_cli.chain_manifest import parse_chain_manifest
        from gflow_cli.data.chain_repo import ChainLinkRecorder
    except ImportError as exc:
        raise FrameExtractionError(
            detail=f"`gflow video chain` needs the optional [chain] dependencies: {exc}",
        ) from exc

    resolved_model = _resolve_chain_model(model)
    aspect_enum = Aspect.from_cli(aspect)

    links: list[ChainLinkSpec] = parse_chain_manifest(_Path(manifest))

    # Validate here as well as inside run_chain (#634). run_chain's own guard is
    # the root-cause one and protects programmatic callers, but it runs after the
    # --dry-run short-circuit below, after the cost prompt, and after Chrome
    # boots — so without this call `chain bad.jsonl --dry-run` would exit 0 on a
    # manifest the real run refuses, and the pre-flight command would green-light
    # the crash it exists to prevent.
    reject_unusable_links(model=resolved_model, links=links)

    if max_links is not None and len(links) > max_links:
        msg = (
            f"chain manifest has {len(links)} link(s) but --max-links is "
            f"{max_links}; raise the cap or trim the manifest before spending."
        )
        raise ChainManifestError(msg)

    settings = get_settings()
    # ffmpeg reads each link's clip from disk to seed the next one (#1008).
    refuse_cloud_storage_for_local_file(settings.storage_uri, "video chain")

    # Resume: bind the prior chain_id and skip completed links so they are not
    # regenerated. A fresh run mints a new id.
    chain_id, skipped, seed_clip = _resolve_chain_resume(
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

    quiet_output = as_json or output_format in (
        OutputFormat.JSON.value,
        OutputFormat.ID.value,
    )
    if not quiet_output:
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
                    as_json=quiet_output,
                    relocate=chain_clip_relocator(output_file),
                    start_index=skipped,
                    seed_clip=seed_clip,
                ),
            )
    finally:
        recorder.close()
        catalog_recorder.close()

    _emit_chain_result(
        results=results,
        chain_id=chain_id,
        partial=partial,
        output_format=output_format,
        as_json=as_json,
        console=console,
    )


@click.group()
def video() -> None:
    """Generate and manage videos via Google Flow Veo."""


@spends_credits(True)
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
    type=click.Choice(
        [
            "omni-flash",
            "omni-1.2-flash",
            "omni-1.2",
            "veo-lite",
            "veo-fast",
            "veo-quality",
            "veo-lite-lp",
        ]
    ),
    help=(
        "Video model. Omit to use Flow's current default. "
        "omni-1.2-flash supports 3–15 seconds; omni-flash supports 4/6/8/10."
    ),
)
@click.option(
    "--duration",
    default=None,
    type=click.Choice([str(n) for n in range(3, 16)]),
    help=(
        "Clip length: 3–15 by one second for omni-1.2-flash; "
        "4/6/8/10 for omni-flash; 4/6/8 for Veo 3.1. "
        "Omit for Flow's default length. Refused pre-submit (no credits) when your "
        "account's cohort renders no duration control for the model."
    ),
)
@_resolution_option
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
    resolution: str | None,
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
    output_format: str = "table",
) -> None:
    """Generate a video from PROMPT."""
    _reject_agentic_ui_mode(ui_mode)
    _reject_duration_without_control(model, duration)
    profile_name = _resolve_profile(profile)
    project_id = default_project(project_id, project_name, profile_name)
    provider_dir = _make_provider_dir(profile_name)
    is_json = as_json or output_format == OutputFormat.JSON.value
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
            resolution=resolution,
            count=count,
            as_json=as_json,
            output_format=output_format,
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
        as_json=is_json,
        output_format=output_format,
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


@spends_credits(True)
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
    type=click.Choice(
        [
            "omni-flash",
            "omni-1.2-flash",
            "omni-1.2",
            "veo-lite",
            "veo-fast",
            "veo-quality",
            "veo-lite-lp",
        ]
    ),
    help=(
        "Video model. Defaults to veo-lite (cheapest). omni-flash supports both "
        "--initial-frame and --end-frame, and unlocks --duration 10; "
        "omni-1.2-flash takes any --duration from 3 to 15 (at most 10 with --end-frame)."
    ),
)
@click.option(
    "--duration",
    default=None,
    type=click.Choice([str(n) for n in range(3, 16)]),
    help=(
        "Clip length: 3–15 by one second for omni-1.2-flash (at most 10 with "
        "--end-frame); 4/6/8/10 for omni-flash; 4/6/8 for Veo 3.1. "
        "Omit for Flow's default length. Refused pre-submit (no credits) when your "
        "account's cohort renders no duration control for the model."
    ),
)
@_resolution_option
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
    resolution: str | None,
    count: int,
    ui_mode: str | None,
    profile: str | None,
    tool_specs: tuple[str, ...],
    project_id: str | None,
    project_name: str | None,
    out_dir: Path | None,
    output_file: Path | None,
    as_json: bool,
    output_format: str = "table",
) -> None:
    """Generate a video from an initial frame + motion PROMPT."""
    _reject_agentic_ui_mode(ui_mode)
    # i2v binds I2V_DEFAULT_MODEL when --model is omitted, so the guard needs
    # that default to fire on the no-flag path (#630).
    _reject_duration_without_control(
        model,
        duration,
        default_model=I2V_DEFAULT_MODEL,
        end_frame=end_frame is not None or end_image_deprecated is not None,
    )
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
    project_id = default_project(project_id, project_name, profile_name)
    display_names, local_files = _media_picker_metadata([start_ref_id, end_ref_id], profile_name)
    start_local = local_files.get(start_ref_id or "")
    end_local = local_files.get(end_ref_id or "")
    provider_dir = _make_provider_dir(profile_name)
    is_json = as_json or output_format == OutputFormat.JSON.value
    quiet_output = is_json or output_format == OutputFormat.ID.value
    prompt_to_send, original_prompt, applied_tool = apply_tool_option(
        resolved_prompt, tool_specs, category="video", quiet=quiet_output
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
        resolution=resolution,
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
            output_format=output_format,
            project_id=project_id,
        ),
        cli_command="video i2v",
        as_json=is_json,
        output_format=output_format,
    )


@spends_credits(True)
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
        "omni-flash/omni-1.2-flash=7, veo-lite/veo-fast/veo-lite-lp=3, veo-quality rejects R2V."
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
    type=click.Choice(
        [
            "omni-flash",
            "omni-1.2-flash",
            "omni-1.2",
            "veo-lite",
            "veo-fast",
            "veo-quality",
            "veo-lite-lp",
        ]
    ),
    help="Veo model. Omit to use Flow's current default.",
)
@click.option(
    "--duration",
    default=None,
    type=click.Choice([str(n) for n in range(3, 16)]),
    help=(
        "Clip length: 3–15 by one second for omni-1.2-flash; "
        "4/6/8/10 for omni-flash; 4/6/8 for Veo 3.1. "
        "Omit for Flow's default length. Refused pre-submit (no credits) when your "
        "account's cohort renders no duration control for the model."
    ),
)
@_resolution_option
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
@out_file_option(help="Explicit output file path for the generated asset.")
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to save the generated mp4. Defaults to tmp/.",
)
@output_format_option(has_out_file=True)
def r2v(
    prompt: str,
    refs: tuple[str, ...],
    aspect: str,
    model: str | None,
    duration: str | None,
    resolution: str | None,
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
    output_format: str = "table",
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
    project_id = default_project(project_id, project_name, profile_name)
    provider_dir = _make_provider_dir(profile_name)
    is_json = as_json or output_format == OutputFormat.JSON.value
    run_with_handlers(
        lambda: _run_r2v(
            profile_name=profile_name,
            profile_dir=provider_dir,
            prompt=prompt,
            refs=refs,
            aspect=aspect,
            model=model,
            duration=int(duration) if duration is not None else None,
            resolution=resolution,
            count=count,
            out_dir=out_dir,
            output_file=output_file,
            as_json=as_json,
            output_format=output_format,
            reference_entities=tuple(reference_entities),
            reference_entity_names=tuple(reference_entity_names),
            original_prompt=None,
            tool=None,
            project_id=project_id,
            project_name=project_name,
            tool_specs=tool_specs,
        ),
        cli_command="video r2v",
        as_json=is_json,
        output_format=output_format,
    )


@spends_credits(True)
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
        'optional "model"/"aspect"/"duration" overrides. Veo links accept '
        "duration 4, 6, or 8 seconds (10s and other lengths need an Omni model, which chain "
        "does not accept); invalid values are rejected before submission. Note "
        "that explicit duration can only take effect in Flow cohorts where the "
        "control is rendered.\n\n"
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
@out_file_option(
    help="Write link i's clip to FILE's stem plus _i (clip.mp4 -> clip_1.mp4, clip_2.mp4)."
)
@click.option(
    "--out-dir",
    "out_dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for the seed frames, and the link mp4s unless --out-file is given.",
)
@output_format_option(has_out_file=True)
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
    output_format: str = "table",
) -> None:
    """Render the chain MANIFEST (one continuous last-frame I2V sequence)."""
    # Before the cost prompt and the browser: the clips' folder must be creatable.
    blocker = next((p for p in output_file.parents if p.exists()), None) if output_file else None
    if blocker is not None and not blocker.is_dir():
        msg = f"{blocker} is a file, so {output_file} cannot be written under it."
        raise click.BadParameter(msg, param_hint="'--out-file'")
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    is_json = as_json or output_format == OutputFormat.JSON.value
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
            output_format=output_format,
            tool_specs=tool_specs,
        ),
        cli_command="video chain",
        as_json=is_json,
        output_format=output_format,
    )


# --------------------------------------------------------------------------
# video extend
# --------------------------------------------------------------------------

# One extend segment is always 8s — the model's videoLengthSeconds, not a
# request parameter. inputSpec.maxInputV2vVideoDuration is also 8, which is why
# a long video is a chain of 8s continuations rather than one growing clip.
_EXTEND_SEGMENT_SECONDS = 8
# Measured: returned media is 7.000s while Flow bills 8. See KNOWN_ISSUES.
_EXTEND_CONTENT_SECONDS = 7
# labs.google's extend takes an aspect; flow.google.com keeps the source clip's.
_LABS_EXTEND_DEFAULT_ASPECT = "9:16"
_EXTEND_COMMAND = "video extend"


async def _run_extend(  # noqa: PLR0913
    *,
    profile_name: str,
    profile_dir: Path,
    media_id: str,
    prompts: tuple[str, ...],
    segments: int,
    aspect: str | None,
    jitter: str | None,
    output_file: Path | None,
    project_id: str | None,
    scene_id: str | None,
    seed: int | None,
    as_json: bool,
    output_format: str = "table",
    migrated: bool = False,
) -> None:
    """Submit N chained extends, then optionally render them to one file."""
    if not project_id:
        msg = "--project is required: extend must know which project owns MEDIA_ID"
        raise ConfigurationError(msg)

    # `chain` defaults jitter to 0.0, which ships unpaced runs and contradicts
    # ACCOUNT_SAFETY's "submissions are paced". Reuse image_batch's resolver so
    # a configured MIN is honoured (keeping only the max would let a 45-120
    # setting sleep 0.4s) and a malformed spec is surfaced, not swallowed.
    jitter_range = resolve_jitter_range(jitter)

    recorder: OperationRecorder | None = None
    store: DataStore | None = None
    try:
        store = DataStore.open(get_settings().resolved_db_path())
        recorder = OperationRecorder(
            DataRepository(store), prompt_mode=get_settings().history_prompts
        )
    except DataStoreError as exc:  # catalog is a convenience, never a gate
        logger.warning("extend_recorder_unavailable", error_class=type(exc).__name__)

    try:
        if migrated:
            await _extend_migrated_session(
                profile_name=profile_name,
                profile_dir=profile_dir,
                media_id=media_id,
                prompt=prompts[0],
                aspect=aspect,
                output_file=output_file,
                project_id=project_id,
                as_json=as_json,
                output_format=output_format,
                recorder=recorder,
            )
            return
        await _extend_session(
            profile_name=profile_name,
            profile_dir=profile_dir,
            media_id=media_id,
            prompts=prompts,
            segments=segments,
            aspect=aspect or _LABS_EXTEND_DEFAULT_ASPECT,
            jitter_range=jitter_range,
            output_file=output_file,
            project_id=project_id,
            scene_id=scene_id,
            seed=seed,
            as_json=as_json,
            output_format=output_format,
            recorder=recorder,
        )
    finally:
        # Leaks on every exit path otherwise, and on Windows a stray handle
        # blocks a later `gflow data` call in the same process.
        if store is not None:
            store.close()


async def _extend_migrated_session(  # noqa: PLR0913
    *,
    profile_name: str,
    profile_dir: Path,
    media_id: str,
    prompt: str,
    aspect: str | None,
    output_file: Path | None,
    project_id: str,
    as_json: bool,
    output_format: str = "table",
    recorder: OperationRecorder | None,
) -> None:
    """One segment on flow.google.com: Flow's own extend UI, downloaded to a file.

    No price, scene read-back or resume exists on this arm yet (exit 36 refuses the
    forms that would need them before this runs).
    """
    out_path = output_file or extend_output_path(get_settings().output_dir, media_id=media_id)
    quiet_output = as_json or output_format in (
        OutputFormat.JSON.value,
        OutputFormat.ID.value,
    )
    async with FlowApiClient(profile_dir=profile_dir) as client:
        outcome, target = await client.extend_video_migrated(
            media_id=media_id,
            project_id=project_id,
            prompt=prompt,
            aspect=aspect,
            out_path=out_path,
            on_started=_migrated_extend_on_started(
                profile_name=profile_name,
                profile_dir=profile_dir,
                project_id=project_id,
                source_media_id=media_id,
                aspect=aspect,
                as_json=quiet_output,
                recorder=recorder,
            ),
        )
    if recorder is not None and target is not None and outcome.record.media_id == media_id:
        try:  # #1184: grown in place — noted only now that Flow reports it done
            recorder.record_extended_in_place(
                profile_name, media_id, outcome.record.workflow_id, outcome.model_key
            )
        except DataStoreError as exc:
            logger.warning("extend_record_failed", error_class=type(exc).__name__)
    _report_migrated_extend(
        outcome,
        target,
        profile_name=profile_name,
        media_id=media_id,
        project_id=project_id,
        aspect=aspect,
        as_json=as_json,
        output_format=output_format,
    )


def _migrated_extend_on_started(  # noqa: PLR0913
    *,
    profile_name: str,
    profile_dir: Path,
    project_id: str,
    source_media_id: str,
    aspect: str | None,
    as_json: bool,
    recorder: OperationRecorder | None,
) -> Callable[[ExtendStarted, str | None], None]:
    """The migrated arm's submit hook: announce it and record it at acceptance."""

    def _on_started(started: ExtendStarted, page_aspect: str | None) -> None:
        if not as_json:
            console.print(f"  submitted   : {started.media_id} ({started.model_key})")
        if recorder is not None:  # at SUBMIT: Flow bills on acceptance
            try:
                recorder.record_started_extend(
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    project_id=project_id,
                    aspect=page_aspect or aspect or "unknown",
                    started=started,
                    source_media_id=source_media_id,
                )
            except DataStoreError as exc:
                logger.warning("extend_record_failed", error_class=type(exc).__name__)

    return _on_started


async def _extend_session(  # noqa: PLR0913
    *,
    profile_name: str,
    profile_dir: Path,
    media_id: str,
    prompts: tuple[str, ...],
    segments: int,
    aspect: str,
    jitter_range: tuple[float, float],
    output_file: Path | None,
    project_id: str,
    scene_id: str | None,
    seed: int | None,
    as_json: bool,
    output_format: str = "table",
    recorder: OperationRecorder | None,
) -> None:
    """The browser-bound half of `_run_extend`, split out so the caller can own
    the DataStore lifetime with a plain try/finally."""
    async with FlowApiClient(profile_dir=profile_dir) as client:
        # `auto` sent an unported form here before any page existed; if Flow served
        # flow.google.com, the labs read below is retired there (exit 7) -- say exit 36.
        refusal = migrated_extend.unported_refusal(segments=segments, scene_id=scene_id, seed=seed)
        if refusal is not None and client.served_route(prefer_migrated=False) != "labs":
            raise refusal
        listing = await client.capability_listing(project_id)
        tier = video_extend.account_service_tier(listing)
        model_key, unit = video_extend.resolve_extend_model(
            listing, service_tier=tier, aspect=aspect
        )
        balance = video_extend.account_credits(listing)

        # Pre-flight balance check for the WHOLE run. `chain` never knew prices
        # so it could not do this; stopping here beats stopping at segment 6
        # holding a half-length video and a spent balance.
        total_cost = unit * segments
        # `balance` is the only unknown here — the resolver cannot return a model
        # without also returning its cost.
        if balance is not None and balance < total_cost:
            msg = (
                f"insufficient credits: {segments} segment(s) cost {total_cost}, "
                f"balance is {balance}. Nothing was submitted."
            )
            raise ConfigurationError(msg)
        quiet_output = as_json or output_format in (
            OutputFormat.JSON.value,
            OutputFormat.ID.value,
        )
        if not quiet_output:
            have = "unknown" if balance is None else str(balance)
            console.print(
                f"  model       : {model_key} ({total_cost} credits total, balance {have})"
            )

        # Resuming: read the scene back so the run appends after the clips that
        # are already there, seeding from the real tail rather than the original.
        target_scene = scene_id
        start_position = 1
        source_media = media_id
        if target_scene:
            existing = await client.get_scene_workflows(target_scene, project_id=project_id)
            clips = sorted(existing.workflows, key=lambda w: w.metadata.position)
            if clips:
                # Positions go non-contiguous when clips are deleted in Flow's
                # UI; len(clips) would collide with an occupied slot.
                start_position = clips[-1].metadata.position + 1
                tail = clips[-1]
                if tail.media_id:
                    source_media = tail.media_id
                if not quiet_output:
                    console.print(
                        f"  resuming    : scene has {len(clips)} clip(s), "
                        f"continuing from {source_media}"
                    )
        # The clip refs `gflow scene create` needs to re-render this run for free (#1201).
        scene_clips: list[str] = []
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
            scene_clips.append(workflow_id)

        # Publish the resume handle BEFORE the first submit, so an interrupt at
        # any point has something to report rather than only on a clean failure.
        set_interrupt_context(credits_spent=0, resume_id=target_scene, segments_done=0)

        submitted_count = 0
        spent_so_far = 0
        # media id -> workflow id of each submitted segment; only confirmed ones get rendered.
        submitted_clips: dict[str, str] = {}

        def _on_submitted(started: Any) -> None:
            # Update BEFORE the ~2 min poll: a Ctrl+C during that wait must
            # report what is already billed, not the pre-run zeroes.
            nonlocal submitted_count, spent_so_far
            submitted_count += 1
            spent_so_far += started.unit_cost or 0
            if started.workflow_id:
                submitted_clips[started.media_id] = started.workflow_id
            set_interrupt_context(
                credits_spent=spent_so_far,
                resume_id=target_scene,
                segments_done=submitted_count,
            )
            if not quiet_output:
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
            jitter_range=jitter_range,
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
        # The scene holds the source clip too, so even N=1 renders source + extension.
        confirmed = [submitted_clips[m] for m in result.completed_media_ids if m in submitted_clips]
        rerender = _free_rerender_hint(
            project_id, profile_name, scene_clips + confirmed, resumed=bool(scene_id)
        )
        if output_file is not None and result.completed_media_ids:
            try:
                scene_state = await client.get_scene_workflows(target_scene, project_id=project_id)
                # Scene.to_concat_inputs owns the end_time>0 fallback (an omitted
                # endTime parses to 0s and would render a zero-length clip) and
                # raises on a missing media_id instead of silently dropping a paid
                # segment. cli_scene.py uses it for the identical job.
                inputs = list(scene_state.to_concat_inputs())
                if not quiet_output:
                    console.print(f"  rendering   : {len(inputs)} clips -> {output_file}")
                await client.concatenate_scene(inputs, out_path=output_file)
            except (GFlowError, ValueError) as exc:
                # #1201: the segments are billed; only the render failed, and it is free.
                billed = exc if isinstance(exc, GFlowError) else SceneConcatError(str(exc))
                billed.charged = "yes"
                billed.next_action = "reconcile"
                billed.retryable = False
                billed.remediation_hint = (
                    f"The {len(result.completed_media_ids)} segment(s) are billed and in "
                    f"scene {target_scene}. Render them again without spending credits: "
                    f"{rerender}"
                )
                if billed is exc:
                    raise
                raise billed from exc
            rendered = str(output_file)

        _emit_extend_session_result(
            result=result,
            rendered=rendered,
            profile_name=profile_name,
            project_id=project_id,
            model_key=model_key,
            aspect=aspect,
            segments=segments,
            seed=seed,
            target_scene=target_scene,
            rerender=rerender,
            output_format=output_format,
            as_json=as_json,
            console=console,
        )


@spends_credits(True)
@video.command(
    "extend",
    short_help="Continue an existing clip (costs credits).",
    help=(
        "Continue an existing video, carrying its motion and audio forward (by another "
        "8-second segment on labs.google; for flow.google.com see below).\n\n"
        "MEDIA_ID is the clip to continue; PROMPT says what happens next.\n\n"
        "Unlike `video chain`, which restarts from an extracted still, extend is "
        "seeded server-side from the source clip, so the join is continuous. "
        "The result lands as a Scene; render it to one file with "
        "`gflow scene create --out-file`.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow video extend <media-id> "the wave recedes" --project <project-id>\n'
        '  gflow video extend <media-id> "drifts upward" --project <project-id> --aspect 16:9\n\n'
        "--project is REQUIRED here: extend must know which project owns MEDIA_ID. "
        "The shared --project help calls it optional because it is — for every "
        "other generate command.\n\n"
        "Each segment spends credits — the exact cost depends on your plan and is "
        "shown for confirmation before anything is submitted (labs.google).\n\n"
        "Note: a segment carries ~7s of content though Flow bills 8s, so a "
        "multi-segment render holds a frozen, silent second at each internal "
        "seam. See KNOWN_ISSUES.\n\n"
        "On flow.google.com gflow drives Flow's own extend UI: one segment per call "
        "(one PROMPT; --segments > 1, --scene/--resume-from and --seed exit 36), in "
        "the source clip's aspect, downloaded to --out-file or the output directory. With "
        "the Omni key (macaron_cookie_extend_4s, measured 2026-11-08) Flow grows the source "
        "in place by ~4s under its own media id, so the file is the whole extended clip and "
        "no earlier version is listed. The price is not shown before submitting there."
    ),
)
@click.argument("media_id")
@click.argument("prompts", nargs=-1, required=True)
@click.option(
    "--aspect",
    default=None,
    # Flow publishes no SQUARE extend model in either family, so 1:1 is refused
    # here rather than surfacing as a late, paid-for failure.
    type=click.Choice(["9:16", "16:9"]),
    help=(
        "Aspect of the extension (portrait 9:16 or landscape 16:9). No square variant "
        "exists. labs.google defaults to 9:16; flow.google.com keeps the source clip's "
        "aspect and refuses (exit 36) a different one before anything is billed."
    ),
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
    type=str,
    help=(
        "Max seconds of random pause between submissions. Defaults to the "
        "GFLOW_CLI_JITTER_RANGE setting; 0 disables pacing (not advised)."
    ),
)
@out_file_option(
    help=(
        "labs.google: render the whole scene (source clip + extensions) to ONE mp4 "
        "here (server-side concat, credit-free). flow.google.com: save the clip Flow returns "
        "here; with the Omni key (measured 2026-11-08), the source grown in place by ~4s."
    ),
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
@output_format_option(has_out_file=True)
def extend(  # noqa: PLR0913
    media_id: str,
    prompts: tuple[str, ...],
    aspect: str | None,
    segments: int | None,
    jitter: str | None,
    output_file: Path | None,
    project_id: str | None,
    scene_id: str | None,
    resume_from: str | None,
    seed: int | None,
    yes: bool,
    dry_run: bool,
    profile: str | None,
    as_json: bool,
    output_format: str = "table",
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
    # Which host drives this run is decided here, from settings and the request alone,
    # so an unported form is refused before the plan, the prompt or any browser.
    refusal = migrated_extend.unported_refusal(segments=count, scene_id=scene_id, seed=seed)
    migrated = (
        migrated_route(None, get_settings().flow_host, prefer_migrated=refusal is None)
        == "migrated"
    )
    is_json = as_json or output_format == OutputFormat.JSON.value
    if migrated and refusal is not None:

        async def _refuse() -> None:
            raise refusal

        run_with_handlers(
            _refuse,
            cli_command=_EXTEND_COMMAND,
            as_json=is_json,
            output_format=output_format,
        )
        return
    # The cost gate runs before a client exists, so --dry-run cannot spend and
    # cannot even open a browser. Machine-readable output must not carry the
    # human plan preamble — it would corrupt the JSON document on stdout.
    if not is_json and output_format != OutputFormat.ID.value:
        _print_extend_plan(
            media_id=media_id,
            prompt=prompts[0],
            aspect=aspect or ("the source clip's" if migrated else _LABS_EXTEND_DEFAULT_ASPECT),
            segments=count,
            migrated=migrated,
        )
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
            output_format=output_format,
            migrated=migrated,
        ),
        cli_command=_EXTEND_COMMAND,
        as_json=is_json,
        output_format=output_format,
    )


@spends_credits(False)
@video.command(
    "upscale",
    short_help="Upscale a Flow-generated video to 1080p (or export as 270p GIF).",
    help=(
        "Upscale a Flow-generated video to 1080p Full HD, download it at 720p, "
        "or export as a 270p animated GIF and save it locally.\n\n"
        "MEDIA_ID is the UUID of a platform video — find one with `gflow data list videos`.\n\n"
        "\b\n"
        "Examples:\n"
        "  gflow video upscale <mediaId> --scale 1080p\n"
        "  gflow video upscale <mediaId> --scale 720p\n"
        "  gflow video upscale <mediaId> --scale 270p\n"
        "  gflow video upscale <mediaId> --scale 1080p --out ~/Downloads\n"
    ),
)
@click.argument("media_id")
@click.option(
    "--scale",
    type=click.Choice(["1080p", "720p", "270p"], case_sensitive=False),
    default="1080p",
    show_default=True,
    help=(
        "Target quality: 1080p (enhanced Full HD), 720p (the original download, "
        "or Flow's upscale of a smaller original), or 270p (animated GIF export). "
        "Flow lists only some scales for some models; an unlisted one exits 22."
    ),
)
@upscale_out_dir_option()
@click.option(
    "--project",
    "project_id",
    default=None,
    callback=_validate_project_id,
    help=(
        "Project that owns the video. Resolved from the local catalog when "
        "omitted; pass it explicitly for videos gflow didn't record."
    ),
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
def upscale(
    media_id: str,
    scale: str,
    out_dir: Path | None,
    project_id: str | None,
    profile: str | None,
) -> None:
    """Upscale or export MEDIA_ID to the requested --scale and save it locally."""
    if not is_media_uuid(media_id):
        raise click.BadParameter(
            "MEDIA_ID must be a bare UUID (8-4-4-4-12 hex).", param_hint="MEDIA_ID"
        )
    profile_name = _resolve_profile(profile)
    from gflow_cli.cli_image import resolve_upscale_project_id

    resolved_project = resolve_upscale_project_id(
        media_id=media_id, explicit=project_id, profile_name=profile_name
    )
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_video_upscale(
            profile_dir=provider_dir,
            headless=settings.headless,
            media_id=media_id,
            project_id=resolved_project,
            scale=scale.lower(),
            out_dir=out_dir,
        ),
        cli_command="video upscale",
    )


async def _run_video_upscale(
    *,
    profile_dir: Path,
    headless: bool,
    media_id: str,
    project_id: str,
    scale: str,
    out_dir: Path | None,
) -> None:
    from datetime import date

    from gflow_cli._cli_helpers import safe_path_text

    settings = get_settings()
    output_root = out_dir if out_dir is not None else settings.output_dir
    ext = "gif" if scale == "270p" else "mp4"
    out_path = output_root / "videos" / date.today().isoformat() / f"{media_id}_{scale}.{ext}"

    async with FlowApiClient(
        profile_dir=profile_dir,
        headless=headless,
        out_dir=output_root,
    ) as client:
        console.print(f"Upscaling video [bold]{media_id}[/bold] to {scale}...")
        target = await client.upsample_video(
            media_id=media_id,
            project_id=project_id,
            scale=scale,
            out_path=out_path,
        )
        console.print(f"[bold green]Saved:[/bold green] {safe_path_text(target)}")
