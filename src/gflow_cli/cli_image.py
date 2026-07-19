"""`gflow image` command group — image asset operations.

Subcommands:

* ``upload PATH`` — uploads a local image into a Flow project's library and
  prints the resulting media UUID and inferred dimensions.
* ``t2i PROMPT`` — text-to-image generation (1-4 images per call).
* ``i2i PROMPT --ref PATH_OR_UUID`` — image-to-image with reference images.

The profile/auth helpers ``_resolve_profile`` and ``_make_provider_dir`` live
in :mod:`gflow_cli._cli_helpers` since T4b — a negative AST-based test in
``tests/cli/test_helpers.py`` prevents drift back into this module.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import click
import structlog
from rich.console import Console
from rich.table import Table

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    _FLOW_ID_RE,
    _make_provider_dir,
    _resolve_profile,
    _validate_project_id,
    apply_tool_option,
    run_with_handlers,
    safe_path_text,
    tool_option,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import ProjectInfo
from gflow_cli.api.image import (
    AgentInstruction,
    Aspect,
    GenerateImageRequest,
    ImageRef,
    Model,
    reference_cap_for,
)
from gflow_cli.api.image_upscale import TargetResolution, UpsampleImageRequest
from gflow_cli.api.transports import transport_choices
from gflow_cli.api.video import is_media_uuid
from gflow_cli.config import UiMode, get_settings, parse_jitter_range
from gflow_cli.data.models import OperationKind
from gflow_cli.data.recorder import (
    OperationRecorder,
    escalate_asset_collision,
    record_failed_operation_safe,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import (
    ConfigurationError,
    DataIntegrityError,
    DataStoreError,
)
from gflow_cli.image_batch import (
    ALLOWED_ASPECT_RATIOS as _ALLOWED_ASPECT_RATIOS,
)
from gflow_cli.image_batch import (
    ALLOWED_MODELS as _ALLOWED_MODELS,
)
from gflow_cli.image_batch import (
    DEFAULT_ASPECT_RATIO as _DEFAULT_ASPECT_RATIO,
)
from gflow_cli.image_batch import (
    DEFAULT_COUNT as _DEFAULT_COUNT,
)
from gflow_cli.image_batch import (
    DEFAULT_MODEL as _DEFAULT_MODEL,
)
from gflow_cli.image_batch import (
    MAX_BATCH_PROMPTS as _MAX_BATCH_PROMPTS,
)
from gflow_cli.image_batch import (
    MAX_COUNT as _MAX_COUNT,
)
from gflow_cli.image_batch import (
    MAX_PROMPT_FILE_BYTES,
    BatchPromptItem,
    parse_manifest_file,
    parse_prompt_lines,
    prompt_items_from_parsed,
    prompt_items_from_texts,
    read_prompt_file,
    render_image_batch_summary,
    resolve_jitter_range,
    run_image_batch,
    run_manifest_image_batch,
)
from gflow_cli.image_batch import (
    MIN_COUNT as _MIN_COUNT,
)
from gflow_cli.paths import image_output_path, resolve_batch_output_dir
from gflow_cli.storage import cloud_info_from_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from gflow_cli.api.dto import GeneratedImage
    from gflow_cli.tools.invocation import AppliedTool

_CmdFn = TypeVar("_CmdFn", bound="Callable[..., object]")

# A `--ref` value shaped like a Flow media UUID (see `is_media_uuid`) is an
# already-uploaded asset passed through verbatim; anything else is a local
# path that needs to be uploaded first.

_CREATING_PROJECT_MSG = "  Creating project..."
_T2I_PROJECT_TITLE = "gflow-cli t2i"
_I2I_PROJECT_TITLE = "gflow-cli i2i"

# `_FLOW_ID_RE` / `_validate_project_id` (Flow project/character-entity id
# allowlist for --project / --reference-entity) live in gflow_cli._cli_helpers
# since they're shared with cli_video.py's --project option — see that
# module's docstring for the injection-surface rationale.


def _validate_entity_ids(
    _ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> tuple[str, ...]:
    """Reject any --reference-entity id that isn't alphanumeric/hyphen.

    Entity ids are interpolated into a CSS selector (`data-tile-id='fe_id_<id>'`);
    the allowlist also blocks quote/metacharacter selector-breakage.
    """
    for v in value:
        if not _FLOW_ID_RE.fullmatch(v):
            msg = f"invalid --reference-entity id {v!r}: expected 1-128 chars of [A-Za-z0-9-]."
            raise click.BadParameter(msg, param=param)
    return value


def _project_and_entity_options(*, single_prompt: bool) -> Callable[[_CmdFn], _CmdFn]:
    """Shared `--project` / `--reference-entity` / `--reference-entity-name` options.

    Applied to both `t2i` and `i2i` so the (identical) option definitions live in one
    place. ``single_prompt`` appends the single-prompt-only note to t2i's help.
    """
    note = " Single-prompt only." if single_prompt else ""

    def decorator(func: _CmdFn) -> _CmdFn:
        func = click.option(
            "--reference-entity-name",
            "reference_entity_names",
            multiple=True,
            help="Character display name paired with --reference-entity (UI picker fallback).",
        )(func)
        func = click.option(
            "--reference-entity",
            "reference_entities",
            multiple=True,
            callback=_validate_entity_ids,
            help=(
                "Flow CHARACTER entity id to reference for consistency (repeatable). "
                "The entity must live in the --project you target." + note
            ),
        )(func)
        return click.option(
            "--project",
            "project_id",
            default=None,
            callback=_validate_project_id,
            help=(
                "Generate in this existing Flow project id instead of creating a scratch "
                "project. Required to reference locked entities/assets that live in a "
                "specific project." + note
            ),
        )(func)

    return decorator


console = Console()
logger = structlog.get_logger(__name__)


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
    console.print("[yellow]Generated media was saved, but local history was not updated.[/yellow]")


def _classify_ref(ref: str) -> ImageRef | Path:
    """Classify a ``--ref`` value as either a pre-uploaded UUID or a local path.

    UUIDs are wrapped in :class:`ImageRef` and returned verbatim. Path-like
    values are canonicalized via ``Path.resolve(strict=True)`` so that:

    * Symlinks are followed once at validation time, eliminating the
      symlink-laundering vector where ``./hero.png -> ~/.ssh/id_rsa`` would
      pass an ``exists()`` check and then be uploaded. This mirrors the
      ``resolve_path=True`` behavior of the ``upload`` subcommand.
    * Broken symlinks and non-existent paths surface as ``FileNotFoundError``
      (raised by ``strict=True``) which we re-raise as :class:`click.UsageError`
      for an exit-2 + friendly message.

    Centralized here so the ``i2i`` Click callback validates upfront and
    ``_run_i2i`` can split UUID refs (wire) from local paths (UI-attached)
    without duplicating the UUID regex check.

    Raises:
        click.UsageError: if *ref* is neither a UUID nor an existing path.
    """
    if is_media_uuid(ref):
        return ImageRef(name=ref)
    try:
        return Path(ref).resolve(strict=True)
    except FileNotFoundError as exc:
        msg = (
            f"--ref {ref!r} does not exist as a file and is not a valid asset UUID. "
            "Pass either a local image path or a 32-char hex UUID with hyphens "
            "(from a prior `gflow image upload`)."
        )
        raise click.UsageError(
            msg,
        ) from exc


async def _resolve_project(
    client: FlowApiClient,
    *,
    project_id: str | None,
    title: str,
    as_json: bool,
) -> tuple[ProjectInfo, bool]:
    """Resolve the project to generate in.

    When ``project_id`` is given, generation runs in that EXISTING project (so
    its locked entities/assets are visible) and no scratch project is created —
    this is what ``--project`` buys. When it is ``None`` the historical behavior
    holds: create a fresh ``gflow-cli ...`` project.

    Returns ``(project, created)``. ``created`` is False for an existing
    ``--project`` so the recorder does NOT overwrite that project's real title /
    source in the local history DB (the synthesized ``title`` here is only a
    placeholder for the created path).
    """
    if project_id is not None:
        if not as_json:
            console.print(f"  Project: [dim]{project_id}[/dim] [dim](existing)[/dim]")
        return ProjectInfo(project_id=project_id, title=title), False
    if not as_json:
        console.print(_CREATING_PROJECT_MSG)
    project = await client.create_project(title=title)
    if not as_json:
        console.print(f"  Project: [dim]{project.project_id}[/dim]")
    return project, True


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group()
def image() -> None:
    """Upload and generate images via Google Flow Imagen.

    Provides ``upload``, ``t2i``, and ``i2i``.
    """


# ---------------------------------------------------------------------------
# upload subcommand
# ---------------------------------------------------------------------------


@image.command(
    "upload",
    short_help="Upload a local image into an ephemeral Flow project.",
    help=(
        "Upload a local image (PNG/JPEG) into a fresh Flow project and print the "
        "asset UUID + dimensions Flow inferred.\n\n"
        "\b\n"
        "Examples:\n"
        "  gflow image upload hero.png\n"
        "  gflow image upload ./shots/01.jpg --profile experiments\n\n"
        "The asset UUID printed by this command is what later subcommands "
        "(t2i with reference, i2i, video i2v) accept as a starting frame."
    ),
)
@click.argument(
    "path",
    type=click.Path(
        exists=True,
        dir_okay=False,
        readable=True,
        # resolve_path follows symlinks AND canonicalises the path. Closes the
        # exfiltration vector where `./hero.png -> ~/.ssh/id_rsa` would pass
        # `exists=True` and silently upload a private key. The magic-byte check
        # in `FlowApiClient.upload_image` is the second layer of defense.
        resolve_path=True,
        path_type=Path,
    ),
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--transport",
    type=click.Choice(transport_choices(), case_sensitive=False),
    default=None,
    help=(
        "Override transport strategy. Falls back to GFLOW_CLI_TRANSPORT env var "
        "or built-in default (ui_automation). Set "
        "GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1 to enable evaluate_fetch/bearer/sapisidhash."
    ),
)
def upload(path: Path, profile: str | None, transport: str | None) -> None:
    """Upload PATH and print the asset UUID."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_upload(
            profile_name=profile_name,
            profile_dir=provider_dir,
            headless=settings.headless,
            image_path=path,
            transport=transport,
        ),
        cli_command="image upload",
    )


async def _run_upload(
    *,
    profile_name: str,
    profile_dir: Path,
    headless: bool,
    image_path: Path,
    transport: str | None = None,
) -> None:
    settings = get_settings()
    recorder = OperationRecorder.open(settings)
    try:
        async with FlowApiClient(
            profile_dir=profile_dir,
            headless=headless,
            transport=transport,
            out_dir=settings.output_dir,
        ) as client:
            console.print(_CREATING_PROJECT_MSG)
            project = await client.create_project(title="gflow-cli upload")
            console.print(f"  Project: [dim]{project.project_id}[/dim]")
            console.print(f"  Uploading {image_path.name}...")
            asset = await client.upload_image(project.project_id, image_path)
            # Render the UUID prominently — that's the load-bearing output.
            console.print(f"[bold green]Asset UUID:[/bold green] [bold]{asset.name}[/bold]")
            console.print(
                f"[dim]Dimensions:[/dim] {asset.width} x {asset.height}  "
                f"[dim]Project:[/dim] {project.project_id}",
            )
            try:
                recorder.record_upload_image(
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    project=project,
                    asset=asset,
                    image_path=image_path,
                )
            except DataStoreError as exc:
                _warn_persistence_failed_after_success(
                    exc=exc,
                    flow_media_id=asset.name,
                    local_path=image_path,
                )
    finally:
        recorder.close()


# ---------------------------------------------------------------------------
# upscale subcommand
# ---------------------------------------------------------------------------


@image.command(
    "upscale",
    short_help="Upscale a platform-generated image to 2K or 4K.",
    help=(
        "Upscale a Flow-generated image to 2K or 4K and save it locally.\n\n"
        "\b\n"
        "Examples:\n"
        "  gflow image upscale <mediaId> --scale 2k\n"
        "  gflow image upscale <mediaId> --scale 4k --out ~/Downloads\n\n"
        "MEDIA_ID is the UUID of a platform-generated image — find one with "
        "`gflow data list images`. Only images Flow generated can be upscaled "
        "(uploaded images are not supported). 4K requires a Flow Ultra "
        "subscription; on other plans use --scale 2k."
    ),
)
@click.argument("media_id")
@click.option(
    "--scale",
    required=True,
    help="Target resolution: 2k or 4k (4k is Ultra-only). 1k is the original.",
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Output directory (defaults to the configured gflow output dir).",
)
@click.option(
    "--project",
    "project_id",
    default=None,
    callback=_validate_project_id,
    help=(
        "Project that owns the image. Resolved from the local catalog when "
        "omitted; pass it explicitly for images gflow didn't record (e.g. "
        "generated in the web UI)."
    ),
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--transport",
    type=click.Choice(transport_choices(), case_sensitive=False),
    default=None,
    help="Override transport strategy (advanced).",
)
def upscale(
    media_id: str,
    scale: str,
    out_dir: Path | None,
    project_id: str | None,
    profile: str | None,
    transport: str | None,
) -> None:
    """Upscale MEDIA_ID to the requested --scale and save it locally."""
    # Validate scale + mediaId format before doing anything (fail fast, exit 2).
    try:
        resolution = TargetResolution.from_cli(scale)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--scale") from exc
    if not is_media_uuid(media_id):
        raise click.BadParameter(
            "MEDIA_ID must be a bare UUID (8-4-4-4-12 hex).", param_hint="MEDIA_ID"
        )

    profile_name = _resolve_profile(profile)
    # Resolve the owning project (catalog lookup or explicit --project) BEFORE
    # launching the browser — no reCAPTCHA mint is spent on an unresolvable id.
    resolved_project = _resolve_upscale_project_id(
        media_id=media_id, explicit=project_id, profile_name=profile_name
    )
    # Final guard: both ids must be well-formed UUIDs (the wire requires it).
    try:
        UpsampleImageRequest(
            media_id=media_id, project_id=resolved_project, target_resolution=resolution
        )
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc

    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_upscale(
            profile_dir=provider_dir,
            headless=settings.headless,
            media_id=media_id,
            project_id=resolved_project,
            resolution=resolution,
            scale_label=scale.strip().lower(),
            out_dir=out_dir,
            transport=transport,
        ),
        cli_command="image upscale",
    )


def _lookup_project_in_catalog(media_id: str, profile_name: str) -> str | None:
    """Return the owning projectId for *media_id* recorded under *profile_name*.

    Scoped to the authenticated profile on purpose: a hit means THIS account
    generated the image (ownership proof). Returns ``None`` if not recorded or
    the catalog is unavailable — the caller then asks for an explicit --project.
    """
    try:
        settings = get_settings()
        with DataStore.open(settings.resolved_db_path()) as store:
            asset = DataRepository(store).get_asset_by_flow_media_id(profile_name, media_id)
    except DataStoreError:
        return None
    if asset is not None and asset.flow_project_id:
        return asset.flow_project_id
    return None


def _resolve_upscale_project_id(*, media_id: str, explicit: str | None, profile_name: str) -> str:
    """Resolve the owning project: explicit --project wins, else the catalog.

    Fails fast with a usage error (exit 2) when neither yields a project, so no
    browser/reCAPTCHA work is wasted.
    """
    cataloged = _lookup_project_in_catalog(media_id, profile_name)
    if explicit is not None:
        if cataloged is not None and cataloged != explicit:
            console.print(
                f"[yellow]Note:[/yellow] --project {explicit} differs from the catalog's "
                f"{cataloged} for this media id; using --project as given."
            )
        return explicit
    if cataloged is not None:
        return cataloged
    msg = (
        f"Could not resolve the owning project for media {media_id!r} from the local "
        f"catalog (profile {profile_name!r}). Pass --project <id> — find it in the Flow "
        f"editor URL (…/project/<id>/…) or via `gflow data list images`."
    )
    raise click.UsageError(msg)


async def _run_upscale(
    *,
    profile_dir: Path,
    headless: bool,
    media_id: str,
    project_id: str,
    resolution: TargetResolution,
    scale_label: str,
    out_dir: Path | None,
    transport: str | None = None,
) -> None:
    settings = get_settings()
    output_root = out_dir if out_dir is not None else settings.output_dir
    out_path = output_root / "images" / date.today().isoformat() / f"{media_id}_{scale_label}.png"
    async with FlowApiClient(
        profile_dir=profile_dir,
        headless=headless,
        transport=transport,
        out_dir=output_root,
    ) as client:
        console.print(f"Upscaling [bold]{media_id}[/bold] to {scale_label.upper()}...")
        target = await client.upsample_image(
            media_id=media_id,
            project_id=project_id,
            target_resolution=resolution,
            out_path=out_path,
        )
        console.print(f"[bold green]Saved:[/bold green] {safe_path_text(target)}")


# ---------------------------------------------------------------------------
# t2i subcommand
# ---------------------------------------------------------------------------


def _validate_jitter_spec(
    _ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    """Fail fast (usage error) on an unparseable --jitter spec."""
    if value is not None:
        try:
            parse_jitter_range(value)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param=param) from None
    return value


_jitter_option = click.option(
    "--jitter",
    "jitter_spec",
    default=None,
    callback=_validate_jitter_spec,
    help=(
        "Anti-bot pause between prompt submissions in multi-prompt runs: "
        "'MIN-MAX' seconds (e.g. 10-30), a single number for 0-N, or 0 to "
        "disable. Defaults to a small 0.5-1.5; GFLOW_CLI_JITTER_RANGE "
        "overrides the default. Widen if runs start hitting WAF 403s."
    ),
)

_ui_mode_option = click.option(
    "--ui-mode",
    "ui_mode",
    type=click.Choice([m.value for m in UiMode], case_sensitive=False),
    default=None,
    help=(
        "Which Flow UI arm to require: 'classic' (hard aspect controls), "
        "'agentic' (chat surface; needed for -i), or 'auto' (bind whatever "
        "renders). gflow switches to it and aborts (exit 28, retryable) if it "
        "can't be reached. Overrides GFLOW_CLI_UI_MODE. Single-prompt only."
    ),
)


@image.command(
    "t2i",
    short_help="Generate image(s) from a text prompt.",
    help=(
        "Generate 1-4 images from a text prompt using Google Flow's Imagen / "
        "Nano Banana models.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow image t2i "a serene mountain lake at dawn"\n'
        '  gflow image t2i "prompt one" "prompt two" "prompt three"\n'
        "  gflow image t2i --prompts-file prompts.txt\n"
        "  cat prompts.txt | gflow image t2i --stdin\n"
        '  gflow image t2i "neon cyberpunk alley" --model nano-pro --aspect 16:9\n'
        '  gflow image t2i "variations of a logo" -n 4 --aspect 1:1'
    ),
)
@click.argument("prompts", nargs=-1, required=False)
@click.option(
    "--prompts-file",
    "prompts_file",
    default=None,
    type=click.Path(path_type=Path),
    help=(
        "Read prompts from a UTF-8 text file: one prompt per non-empty line; "
        "whole-line # comments skipped."
    ),
)
@click.option(
    "--stdin",
    "read_stdin",
    is_flag=True,
    help="Read prompts from stdin using the same format as --prompts-file.",
)
@click.option(
    "--continue-on-error/--fail-fast",
    default=True,
    show_default=True,
    help="In multi-prompt mode, continue after per-prompt failures or stop at the first failure.",
)
@_jitter_option
@click.option(
    "--model",
    default=_DEFAULT_MODEL,
    show_default=True,
    type=click.Choice(_ALLOWED_MODELS),
    help="Image model alias.",
)
@click.option(
    "--aspect",
    default=_DEFAULT_ASPECT_RATIO,
    show_default=True,
    type=click.Choice(_ALLOWED_ASPECT_RATIOS),
    help="Image aspect ratio.",
)
@click.option(
    "-n",
    "--count",
    "count",
    default=_DEFAULT_COUNT,
    show_default=True,
    type=click.IntRange(_MIN_COUNT, _MAX_COUNT),
    help=f"How many images to generate ({_MIN_COUNT}-{_MAX_COUNT}).",
)
@click.option(
    "--out",
    "out",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help=(
        "Directory to write generated PNGs. When omitted, files land under "
        "<output_dir>/images/<YYYY-MM-DD>/ (date-partitioned). When provided, "
        "files are written flat as <dir>/<media_name>_<n>.png."
    ),
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@tool_option
@click.option(
    "--transport",
    type=click.Choice(transport_choices(), case_sensitive=False),
    default=None,
    help=(
        "Override transport strategy. Falls back to GFLOW_CLI_TRANSPORT env var "
        "or built-in default (ui_automation). Set "
        "GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1 to enable evaluate_fetch/bearer/sapisidhash."
    ),
)
@_project_and_entity_options(single_prompt=True)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a machine-readable JSON result instead of a Rich table.",
)
@click.option(
    "--instruction",
    "-i",
    "instructions",
    multiple=True,
    help="Custom agent instruction to add or enable (only in agentic mode).",
)
@_ui_mode_option
def t2i(  # NOSONAR
    prompts: tuple[str, ...],
    prompts_file: Path | None,
    read_stdin: bool,
    continue_on_error: bool,
    jitter_spec: str | None,
    ui_mode: str | None,
    model: str,
    aspect: str,
    count: int,
    out: Path | None,
    profile: str | None,
    tool_specs: tuple[str, ...],
    transport: str | None,
    project_id: str | None,
    reference_entities: tuple[str, ...],
    reference_entity_names: tuple[str, ...],
    as_json: bool,
    instructions: tuple[str, ...],
) -> None:
    """Generate image(s) from one or more text prompts."""
    is_multi_prompt = len(prompts) > 1 or prompts_file is not None or read_stdin
    _validate_t2i_input(prompts, prompts_file, read_stdin)

    if is_multi_prompt and instructions:
        msg = "--instruction is single-prompt only; remove the extra prompts."
        raise click.UsageError(msg)

    if is_multi_prompt and (project_id is not None or reference_entities):
        # The multi-prompt/batch path creates one shared project internally; a
        # single explicit --project / --reference-entity across many prompts isn't
        # wired. Loop t2i one prompt at a time if you need every frame in the same
        # project with the same characters.
        msg = "--project / --reference-entity are single-prompt only; remove the extra prompts."
        raise click.UsageError(msg)

    if is_multi_prompt and as_json:
        # --json is single-prompt only — the batch summary shape is rich-table
        # specific and a worker shells out one prompt at a time anyway.
        msg = "--json is single-prompt only; remove the extra prompts."
        raise click.UsageError(msg)

    if is_multi_prompt and ui_mode is not None:
        # The batch path is env-controlled (GFLOW_CLI_UI_MODE); a per-command
        # arm override isn't threaded through the batch runner.
        msg = "--ui-mode is single-prompt only; set GFLOW_CLI_UI_MODE for batch runs."
        raise click.UsageError(msg)

    if ui_mode == UiMode.CLASSIC.value and instructions:
        # Agent instructions are agentic-only; requiring classic contradicts them.
        msg = "--ui-mode classic is incompatible with -i (instructions need the agentic UI)."
        raise click.UsageError(msg)

    if not is_multi_prompt:
        if not prompts:
            msg = "Provide a prompt, multiple prompts, --prompts-file, or --stdin."
            raise click.UsageError(
                msg,
            )
        prompt = prompts[0]
        profile_name = _resolve_profile(profile)
        provider_dir = _make_provider_dir(profile_name)
        settings = get_settings()
        run_with_handlers(
            lambda: _run_t2i(
                profile_name=profile_name,
                profile_dir=provider_dir,
                headless=settings.headless,
                req=GenerateImageRequest(
                    prompt=prompt,
                    aspect=Aspect.from_cli(aspect),
                    model=Model.from_cli(model),
                    reference_entities=tuple(reference_entities),
                    reference_entity_names=tuple(reference_entity_names),
                    original_prompt=None,
                    tool=None,
                    instructions=(
                        tuple(AgentInstruction(text=i) for i in instructions)
                        if instructions
                        else None
                    ),
                    ui_mode=UiMode(ui_mode) if ui_mode else None,
                ),
                count=count,
                out=out,
                output_root=settings.output_dir,
                transport=transport,
                project_id=project_id,
                as_json=as_json,
                tool_specs=tool_specs,
            ),
            cli_command="image t2i",
            as_json=as_json,
        )

        return

    batch_prompts = _build_t2i_batch_prompts(
        prompts,
        prompts_file,
        read_stdin,
        aspect,
        model,
        count,
    )
    # --tool now works on the batch path too (PR2): apply each tool per prompt
    # before submission (sequential Gemini calls, never fatal). Validation of an
    # unknown tool/style raises a UsageError here, before any browser opens.
    if tool_specs:
        batch_prompts = _apply_tools_to_batch_prompts(batch_prompts, tool_specs)
    _execute_t2i_batch(
        batch_prompts, count, continue_on_error, profile, out, transport, jitter_spec
    )


def _apply_tools_to_batch_prompts(
    batch_prompts: tuple[BatchPromptItem, ...],
    tool_specs: tuple[str, ...],
) -> tuple[BatchPromptItem, ...]:
    """Apply ``--tool`` to each batch item's text (sequential, ≤50 prompts).

    Returns new items carrying the rewritten ``text`` plus ``original_prompt`` /
    ``tool`` provenance. ``apply_tool_option`` is never-fatal per prompt (a
    missing key / API error degrades to the original text); an unknown tool or
    style still raises ``click.UsageError`` (pre-network) so the whole batch
    fails fast rather than silently skipping the tool.
    """
    from dataclasses import replace

    applied: list[BatchPromptItem] = []
    for item in batch_prompts:
        sent, original, tool = apply_tool_option(
            item.text, tool_specs, category="image", quiet=True
        )
        applied.append(replace(item, text=sent, original_prompt=original, tool=tool))
    return tuple(applied)


def _build_t2i_batch_prompts(
    prompts: tuple[str, ...],
    prompts_file: Path | None,
    read_stdin: bool,
    aspect: str,
    model: str,
    count: int,
) -> tuple[BatchPromptItem, ...]:
    """Build batch prompt items from whichever input source was given.

    Raises click.UsageError on stdin size overflow or malformed file content.
    """
    try:
        if prompts_file is not None:
            parsed = read_prompt_file(prompts_file)
            return prompt_items_from_parsed(parsed, aspect_ratio=aspect, model=model, count=count)
        if read_stdin:
            raw_stdin = sys.stdin.read(MAX_PROMPT_FILE_BYTES + 1)
            if len(raw_stdin) > MAX_PROMPT_FILE_BYTES:
                msg = (
                    f"Standard input exceeds the maximum allowed size of "
                    f"{MAX_PROMPT_FILE_BYTES // 1024} KiB."
                )
                raise click.UsageError(
                    msg,
                )
            parsed = parse_prompt_lines(raw_stdin, source_label="--stdin")
            return prompt_items_from_parsed(parsed, aspect_ratio=aspect, model=model, count=count)
        return prompt_items_from_texts(
            prompts,
            aspect_ratio=aspect,
            model=model,
            count=count,
            source_label="positional",
        )
    except ConfigurationError as exc:
        raise _as_usage_error(exc) from exc


def _execute_t2i_batch(
    batch_prompts: tuple[BatchPromptItem, ...],
    count: int,
    continue_on_error: bool,
    profile: str | None,
    out: Path | None,
    transport: str | None,
    jitter_spec: str | None = None,
) -> None:
    """Run a multi-prompt t2i batch and print the summary table."""
    jitter_range = resolve_jitter_range(jitter_spec)
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    output_dir = resolve_batch_output_dir(
        cli_override=out,
        output_root=settings.output_dir,
        kind="images",
    )
    console.print(
        f"\n[bold]gflow image t2i[/bold] · profile=[bold]{profile_name}[/bold] "
        f"· {len(batch_prompts)} prompt(s) · up to {len(batch_prompts) * count} image(s)",
    )
    console.print(f"  output_dir: [dim]{safe_path_text(output_dir)}[/dim]")
    if not continue_on_error:
        console.print("  mode: [yellow]fail-fast[/yellow]")
    recorder = OperationRecorder.open(settings)
    try:
        outcomes = asyncio.run(
            run_image_batch(
                profile_dir=provider_dir,
                headless=settings.headless,
                transport=transport,
                prompts=batch_prompts,
                output_dir=output_dir,
                continue_on_error=continue_on_error,
                project_title=_T2I_PROJECT_TITLE,
                jitter_range=jitter_range,
                _profile_name=profile_name,
                _recorder=recorder,
                _command="image t2i",
            ),
        )
    finally:
        recorder.close()
    exit_code = render_image_batch_summary(outcomes, title=_T2I_PROJECT_TITLE)
    if exit_code != 0:
        sys.exit(exit_code)


def _count_t2i_sources(
    prompts: tuple[str, ...],
    prompts_file: Path | None,
    read_stdin: bool,
) -> int:
    return int(bool(prompts)) + int(prompts_file is not None) + int(read_stdin)


def _validate_t2i_input(
    prompts: tuple[str, ...],
    prompts_file: Path | None,
    read_stdin: bool,
) -> None:
    """Raise click.UsageError for invalid t2i flag combinations.

    Click's IntRange already bounds count to [1, 4]; this enforces that
    exactly one prompt source is used.
    """
    source_count = _count_t2i_sources(prompts, prompts_file, read_stdin)
    if source_count == 0:
        msg = "Provide a prompt, multiple prompts, --prompts-file, or --stdin."
        raise click.UsageError(msg)
    if source_count > 1:
        msg = (
            "Prompt sources are mutually exclusive: use positional prompts, "
            "--prompts-file, or --stdin."
        )
        raise click.UsageError(
            msg,
        )


def _as_usage_error(exc: ConfigurationError) -> click.UsageError:
    return click.UsageError(str(exc))


async def _download_images(
    client: FlowApiClient,
    images: list[GeneratedImage],
    out: Path | None,
    output_root: Path,
) -> list[Path]:
    """Download each generated image to its resolved target path."""
    saved_paths: list[Path] = []
    for i, img in enumerate(images, start=1):
        target = (
            out / f"{img.media_name}_{i}.png"
            if out is not None
            else image_output_path(output_root, job_id=img.media_name, index=i)
        )
        saved = await client.download_image(img, target)
        saved_paths.append(saved)
    return saved_paths


def _record_generated_images_safe(
    recorder: OperationRecorder,
    *,
    profile_name: str,
    profile_dir: Path,
    project: ProjectInfo,
    project_created: bool,
    request: GenerateImageRequest,
    images: list[GeneratedImage],
    saved_paths: list[Path],
    input_media_ids: list[str],
    operation_kind: str,
) -> None:
    """Persist generation metadata; warn on DataStoreError (never abort success).

    Tool provenance (``original_prompt`` / ``tool``) travels on ``request``, so
    the recorder reads it directly — no separate kwarg to drift out of sync.

    Collision escalation (issue #281, #282 review): a ``DataIntegrityError``
    whose ``route`` is the asset-collision constraint means the write itself
    violated a local DB constraint — most likely the per-profile uniqueness of
    ``flow_media_id`` — i.e. the just-downloaded file may be a pre-existing
    asset rather than genuinely new media. That is NOT a warn-and-continue
    case like a generic ``DataStoreError``; ``escalate_asset_collision``
    raises ``MediaAttributionError`` for that route and returns normally for
    any other (unrelated) ``DataIntegrityError``, in which case this falls
    through to the same warn-and-continue path as a plain ``DataStoreError``.
    """
    try:
        recorder.record_generated_images(
            profile_name=profile_name,
            profile_dir=profile_dir,
            project=project,
            project_created=project_created,
            request=request,
            images=images,
            saved_paths=saved_paths,
            cloud_storage_infos=[cloud_info_from_path(path) for path in saved_paths],
            input_media_ids=input_media_ids,
            operation_kind=operation_kind,
        )
    except DataStoreError as exc:
        if isinstance(exc, DataIntegrityError):
            escalate_asset_collision(exc, images=images, saved_paths=saved_paths)
        first_image = images[0] if images else None
        first_path = saved_paths[0] if saved_paths else None
        _warn_persistence_failed_after_success(
            exc=exc,
            flow_media_id=first_image.media_name if first_image else None,
            local_path=first_path,
        )


async def _run_t2i(
    *,
    profile_name: str,
    profile_dir: Path,
    headless: bool,
    req: GenerateImageRequest,
    count: int,
    out: Path | None,
    output_root: Path,
    transport: str | None = None,
    project_id: str | None = None,
    as_json: bool = False,
    tool_specs: tuple[str, ...] = (),
) -> None:
    settings = get_settings()
    recorder = OperationRecorder.open(settings)
    try:
        async with FlowApiClient(
            profile_dir=profile_dir,
            headless=headless,
            transport=transport,
            out_dir=out if out is not None else output_root,
        ) as client:
            # Title is a `gflow-cli ...` prefix per project convention (post-rename a02684f).
            # cli_video.py's _run_t2v / _run_i2v don't currently set a title — tracked separately.
            project, project_created = await _resolve_project(
                client, project_id=project_id, title=_T2I_PROJECT_TITLE, as_json=as_json
            )

            # Resolve @-mentions and expand --tool specs (shared helper).
            from gflow_cli.services.mentions import resolve_and_apply

            req = await resolve_and_apply(
                client,
                req,
                path="image",
                project_id=project.project_id,
                tool_specs=tool_specs,
                quiet=as_json,
            )

            if not as_json:
                console.print(
                    f"  Generating {count} image(s) ({req.model.value}, {req.aspect.value})...",
                )

            if count == 1:
                img = await client.generate_image(project_id=project.project_id, req=req)
                images: list[GeneratedImage] = [img]
            else:
                images = await client.generate_images_batch(
                    project_id=project.project_id,
                    req=req,
                    count=count,
                )

            recorder.verify_media_attribution(profile_name=profile_name, images=images)
            saved_paths = await _download_images(client, images, out, output_root)

            if as_json:
                json_output.emit(
                    json_output.image_result(
                        command="image t2i",
                        project_id=project.project_id,
                        model=req.model.value,
                        images=images,
                        saved_paths=saved_paths,
                    ),
                )
            else:
                _print_t2i_summary(images, saved_paths)

            _record_generated_images_safe(
                recorder,
                profile_name=profile_name,
                profile_dir=profile_dir,
                project=project,
                project_created=project_created,
                request=req,
                images=images,
                saved_paths=saved_paths,
                input_media_ids=[],
                operation_kind="t2i",
            )
    except Exception as exc:
        # #341: persist the failure before re-raising (images have no STARTED
        # pre-insert, so this is always a fresh FAILED row).
        record_failed_operation_safe(
            recorder,
            logger=logger,
            profile_name=profile_name,
            profile_dir=profile_dir,
            command="image t2i",
            mode=OperationKind.T2I,
            exc=exc,
            request=req,
        )
        raise
    finally:
        recorder.close()


def _print_t2i_summary(images: list[GeneratedImage], saved_paths: list[Path]) -> None:
    """Render a Rich table of generated images and where they landed."""
    table = Table(title=_T2I_PROJECT_TITLE)
    table.add_column("media_name", overflow="fold")
    table.add_column("seed", justify="right")
    table.add_column("dimensions")
    table.add_column("output_path", overflow="fold")
    for img, path in zip(images, saved_paths, strict=True):
        w, h = img.dimensions
        table.add_row(img.media_name, str(img.seed), f"{w}x{h}", safe_path_text(path))
    console.print(table)


# ---------------------------------------------------------------------------
# batch subcommand
# ---------------------------------------------------------------------------

_BATCH_TITLE = "gflow-cli image batch"


@image.command(
    "batch",
    short_help=f"Batch-generate images from a manifest (max {_MAX_BATCH_PROMPTS}, shared project).",
    help=(
        "Generate images from a JSON or TSV manifest file "
        f"(up to {_MAX_BATCH_PROMPTS} prompts).\n\n"
        "All prompts share one Flow project (stay-mounted editor). A small\n"
        "0.5-1.5s jitter is applied between submissions as an anti-bot\n"
        "courtesy (configurable via --jitter or GFLOW_CLI_JITTER_RANGE).\n\n"
        "To generate each prompt in its own project, loop `gflow image t2i` instead.\n\n"
        "\b\n"
        "TSV format (tab-separated): prompt[\\tcount[\\taspect_ratio[\\tmodel]]]\n"
        "  Lines starting with # or blank lines are skipped.\n\n"
        'JSON format: [{"text": "...", "count": 2, "aspect_ratio": "16:9", '
        '"model": "nano2"}, ...]\n\n'
        "\b\n"
        "Examples:\n"
        "  gflow image batch prompts.tsv\n"
        "  gflow image batch prompts.json\n"
        "  gflow image batch prompts.tsv -n 4 --aspect 16:9 --out ./output\n"
    ),
)
@click.argument(
    "manifest",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
@click.option(
    "-n",
    "--count",
    "count",
    default=_DEFAULT_COUNT,
    show_default=True,
    type=click.IntRange(_MIN_COUNT, _MAX_COUNT),
    help=(
        "Default image count for manifest rows that do not specify one "
        f"({_MIN_COUNT}-{_MAX_COUNT})."
    ),
)
@click.option(
    "--aspect",
    default=_DEFAULT_ASPECT_RATIO,
    show_default=True,
    type=click.Choice(_ALLOWED_ASPECT_RATIOS),
    help="Default aspect ratio for rows that do not specify one.",
)
@click.option(
    "--model",
    default=_DEFAULT_MODEL,
    show_default=True,
    type=click.Choice(_ALLOWED_MODELS),
    help="Default model for rows that do not specify one.",
)
@click.option(
    "--continue-on-error/--fail-fast",
    default=True,
    show_default=True,
    help="Continue after per-prompt failures (default) or stop at the first failure.",
)
@_jitter_option
@click.option(
    "--out",
    "out",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help=(
        "Directory to write generated PNGs. When omitted, files land under "
        "<output_dir>/images/<YYYY-MM-DD>/."
    ),
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@tool_option
@click.option(
    "--transport",
    type=click.Choice(transport_choices(), case_sensitive=False),
    default=None,
    help="Override transport strategy.",
)
def batch(
    manifest: Path,
    count: int,
    aspect: str,
    model: str,
    continue_on_error: bool,
    jitter_spec: str | None,
    out: Path | None,
    profile: str | None,
    tool_specs: tuple[str, ...],
    transport: str | None,
) -> None:
    """Run MANIFEST (JSON or TSV) through Flow's image generator."""
    try:
        prompts = parse_manifest_file(
            manifest,
            default_count=count,
            default_aspect_ratio=aspect,
            default_model=model,
        )
    except ConfigurationError as exc:
        raise _as_usage_error(exc) from exc

    # Apply --tool to each manifest row before submission (≤5 prompts, sequential,
    # never-fatal per row; unknown tool/style fails fast pre-network).
    if tool_specs:
        prompts = _apply_tools_to_batch_prompts(prompts, tool_specs)

    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    output_dir = resolve_batch_output_dir(
        cli_override=out,
        output_root=settings.output_dir,
        kind="images",
    )

    total_images = sum(p.count for p in prompts)
    console.print(
        f"\n[bold]{_BATCH_TITLE}[/bold] · profile=[bold]{profile_name}[/bold] "
        f"· {len(prompts)} prompt(s) · up to {total_images} image(s)",
    )
    console.print(f"  output_dir: [dim]{safe_path_text(output_dir)}[/dim]")
    if not continue_on_error:
        console.print("  mode: [yellow]fail-fast[/yellow]")

    recorder = OperationRecorder.open(settings)
    try:
        outcomes = asyncio.run(
            run_manifest_image_batch(
                profile_dir=provider_dir,
                headless=settings.headless,
                transport=transport,
                prompts=prompts,
                output_dir=output_dir,
                continue_on_error=continue_on_error,
                jitter_range=resolve_jitter_range(jitter_spec),
                profile_name=profile_name,
                recorder=recorder,
            ),
        )
    finally:
        recorder.close()
    exit_code = render_image_batch_summary(outcomes, title=_BATCH_TITLE)
    if exit_code != 0:
        sys.exit(exit_code)


# ---------------------------------------------------------------------------
# i2i subcommand
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _I2IParams:
    """Bundles image-generation options for :func:`_run_i2i`.

    Separating these from the profile/transport/output fields keeps the
    function signature below Sonar's 13-parameter limit (S107) while preserving
    every CLI option.
    """

    prompt: str
    classified_refs: list[ImageRef | Path]
    aspect: Aspect
    model: Model
    reference_entities: tuple[str, ...]
    reference_entity_names: tuple[str, ...]
    # Tool provenance (set when a --tool rewrote the prompt; recorded only).
    original_prompt: str | None = None
    tool: AppliedTool | None = None
    instructions: tuple[AgentInstruction, ...] | None = None
    ui_mode: UiMode | None = None


@image.command(
    "i2i",
    short_help="Generate image(s) from a prompt + one or more reference images.",
    help=(
        "Image-to-image generation: blend a text prompt with one or more "
        "reference images. Each --ref is either a local image path (auto-uploaded) "
        "or the media UUID of an already-uploaded asset (from a prior `gflow image upload`).\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow image i2i "make it cinematic" --ref hero.png\n'
        '  gflow image i2i "blend these" --ref a.png --ref b.png\n'
        '  gflow image i2i "stylize" --ref ddb6ef97-262d-49f4-8269-4a28c0fae6a2\n'
        '  gflow image i2i "mix" --ref hero.png --ref ddb6ef97-262d-49f4-8269-4a28c0fae6a2\n\n'
        "For text-only generation, use `gflow image t2i` instead."
    ),
)
@click.argument("prompt")
@click.option(
    "--ref",
    "refs",
    multiple=True,
    required=True,
    help=(
        "Reference image: either a local path (auto-uploaded) or the media UUID of an "
        "already-uploaded asset. Repeat to pass multiple refs (order is preserved). "
        "For text-only generation, use `gflow image t2i` instead."
    ),
)
@click.option(
    "--model",
    default=_DEFAULT_MODEL,
    show_default=True,
    type=click.Choice(_ALLOWED_MODELS),
    help="Image model alias.",
)
@click.option(
    "--aspect",
    default=_DEFAULT_ASPECT_RATIO,
    show_default=True,
    type=click.Choice(_ALLOWED_ASPECT_RATIOS),
    help="Image aspect ratio.",
)
@click.option(
    "-n",
    "--count",
    "count",
    default=1,
    show_default=True,
    type=click.IntRange(1, 4),
    help="How many images to generate (1-4).",
)
@click.option(
    "--out",
    "out",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help=(
        "Directory to write generated PNGs. When omitted, files land under "
        "<output_dir>/images/<YYYY-MM-DD>/ (date-partitioned). When provided, "
        "files are written flat as <dir>/<media_name>_<n>.png."
    ),
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@tool_option
@click.option(
    "--transport",
    type=click.Choice(transport_choices(), case_sensitive=False),
    default=None,
    help=(
        "Override transport strategy. Falls back to GFLOW_CLI_TRANSPORT env var "
        "or built-in default (ui_automation). Set "
        "GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1 to enable evaluate_fetch/bearer/sapisidhash."
    ),
)
@_project_and_entity_options(single_prompt=False)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a machine-readable JSON result instead of a Rich table.",
)
@click.option(
    "--instruction",
    "-i",
    "instructions",
    multiple=True,
    help="Custom agent instruction to add or enable (only in agentic mode).",
)
@_ui_mode_option
def i2i(  # NOSONAR
    prompt: str,
    refs: tuple[str, ...],
    model: str,
    aspect: str,
    count: int,
    out: Path | None,
    profile: str | None,
    tool_specs: tuple[str, ...],
    transport: str | None,
    project_id: str | None,
    reference_entities: tuple[str, ...],
    reference_entity_names: tuple[str, ...],
    as_json: bool,
    instructions: tuple[str, ...],
    ui_mode: str | None,
) -> None:
    """Generate image(s) from PROMPT + reference image(s) (image-to-image)."""
    if ui_mode == UiMode.CLASSIC.value and instructions:
        msg = "--ui-mode classic is incompatible with -i (instructions need the agentic UI)."
        raise click.UsageError(msg)
    # Classify each --ref upfront: UUIDs become ImageRef, path-likes become
    # canonical Paths (with symlinks resolved). _classify_ref raises
    # click.UsageError on missing/broken paths, which Click maps to exit 2.
    # Click's `multiple=True` with `required=True` already rejects the
    # "no --ref" case with exit 2 before we get here.
    classified_refs: list[ImageRef | Path] = [_classify_ref(ref) for ref in refs]

    # Reject over-cap ref counts at the CLI boundary with a clear message (exit
    # 2) rather than letting the domain ValueError surface as a generic error.
    # GenerateImageRequest.__post_init__ enforces the same cap as an invariant.
    model_enum = Model.from_cli(model)
    cap = reference_cap_for(model_enum)
    # Image refs AND character entities share one per-model reference budget.
    n_refs = len(classified_refs) + len(reference_entities)
    if n_refs > cap:
        msg = f"{model} allows at most {cap} reference(s); got {n_refs}."
        raise click.UsageError(
            msg,
        )

    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    i2i_params = _I2IParams(
        prompt=prompt,
        classified_refs=classified_refs,
        aspect=Aspect.from_cli(aspect),
        model=model_enum,
        reference_entities=tuple(reference_entities),
        reference_entity_names=tuple(reference_entity_names),
        original_prompt=None,
        tool=None,
        instructions=(
            tuple(AgentInstruction(text=i) for i in instructions) if instructions else None
        ),
        ui_mode=UiMode(ui_mode) if ui_mode else None,
    )
    run_with_handlers(
        lambda: _run_i2i(
            profile_name=profile_name,
            profile_dir=provider_dir,
            headless=settings.headless,
            params=i2i_params,
            count=count,
            out=out,
            output_root=settings.output_dir,
            transport=transport,
            project_id=project_id,
            as_json=as_json,
            tool_specs=tool_specs,
        ),
        cli_command="image i2i",
        as_json=as_json,
    )


async def _run_i2i(
    *,
    profile_name: str,
    profile_dir: Path,
    headless: bool,
    params: _I2IParams,
    count: int,
    out: Path | None,
    output_root: Path,
    transport: str | None = None,
    project_id: str | None = None,
    as_json: bool = False,
    tool_specs: tuple[str, ...] = (),
) -> None:
    settings = get_settings()
    recorder = OperationRecorder.open(settings)
    req: GenerateImageRequest | None = None
    try:
        async with FlowApiClient(
            profile_dir=profile_dir,
            headless=headless,
            transport=transport,
            out_dir=out if out is not None else output_root,
        ) as client:
            # Title is a `gflow-cli ...` prefix per project convention (post-rename a02684f).
            # cli_video.py's _run_t2v / _run_i2v don't currently set a title — tracked separately.
            project, project_created = await _resolve_project(
                client, project_id=project_id, title=_I2I_PROJECT_TITLE, as_json=as_json
            )

            # Local-file refs are attached through the editor's media dialog by the
            # ui_automation transport (the REST uploadImage path 401s — see #15/#39).
            # Already-uploaded UUID refs go on `refs`: the transport binds them by
            # SELECTING the existing Flow asset in the reference picker (no duplicate
            # upload — v0.26.0, `_attach_image_uuid_refs`), falling back to uploading
            # the asset's local file, and failing loud if neither is possible. A UUID
            # ref is never silently dropped.
            uuid_refs_initial = [r for r in params.classified_refs if isinstance(r, ImageRef)]
            local_ref_paths = tuple(r for r in params.classified_refs if isinstance(r, Path))
            req = GenerateImageRequest(
                prompt=params.prompt,
                aspect=params.aspect,
                model=params.model,
                refs=tuple(uuid_refs_initial),
                ref_paths=local_ref_paths,
                reference_entities=params.reference_entities,
                reference_entity_names=params.reference_entity_names,
                original_prompt=params.original_prompt,
                tool=params.tool,
                instructions=params.instructions,
                ui_mode=params.ui_mode,
            )

            # Resolve @-mentions (entities → reference_entities, media → refs) and
            # expand --tool specs (shared helper).
            from gflow_cli.services.mentions import resolve_and_apply

            req = await resolve_and_apply(
                client,
                req,
                path="image",
                project_id=project.project_id,
                tool_specs=tool_specs,
                quiet=as_json,
            )

            n_refs = len(req.refs) + len(req.ref_paths)
            if not as_json:
                console.print(
                    f"  Generating {count} image(s) with {n_refs} ref(s) "
                    f"({req.model.value}, {req.aspect.value})...",
                )
            if count == 1:
                img = await client.generate_image(project_id=project.project_id, req=req)
                images: list[GeneratedImage] = [img]
            else:
                images = await client.generate_images_batch(
                    project_id=project.project_id,
                    req=req,
                    count=count,
                )

            recorder.verify_media_attribution(profile_name=profile_name, images=images)
            saved_paths = await _download_images(client, images, out, output_root)

            if as_json:
                json_output.emit(
                    json_output.image_result(
                        command="image i2i",
                        project_id=project.project_id,
                        model=req.model.value,
                        images=images,
                        saved_paths=saved_paths,
                        ref_count=n_refs,
                    ),
                )
            else:
                _print_i2i_summary(images, saved_paths)

            _record_generated_images_safe(
                recorder,
                profile_name=profile_name,
                profile_dir=profile_dir,
                project=project,
                project_created=project_created,
                request=req,
                images=images,
                saved_paths=saved_paths,
                # Only already-uploaded UUID refs have a flow_media_id we
                # can persist as INPUT. Local files attached via the media
                # dialog don't surface a media_id at this layer; the recorder
                # will skip them silently (record_generated_images guards on
                # repo.get_asset_by_flow_media_id returning None).
                input_media_ids=[ref.name for ref in req.refs],
                operation_kind="i2i",
            )
    except Exception as exc:
        # #341: persist the failure before re-raising. ``req`` is None when the
        # failure predates request construction (e.g. project resolution).
        record_failed_operation_safe(
            recorder,
            logger=logger,
            profile_name=profile_name,
            profile_dir=profile_dir,
            command="image i2i",
            mode=OperationKind.I2I,
            exc=exc,
            request=req,
        )
        raise
    finally:
        recorder.close()


def _print_i2i_summary(images: list[GeneratedImage], saved_paths: list[Path]) -> None:
    """Render a Rich table of generated images and where they landed."""
    table = Table(title=_I2I_PROJECT_TITLE)
    table.add_column("media_name", overflow="fold")
    table.add_column("seed", justify="right")
    table.add_column("dimensions")
    table.add_column("output_path", overflow="fold")
    for img, path in zip(images, saved_paths, strict=True):
        w, h = img.dimensions
        table.add_row(img.media_name, str(img.seed), f"{w}x{h}", safe_path_text(path))
    console.print(table)
