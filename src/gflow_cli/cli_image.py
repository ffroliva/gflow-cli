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
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import click
import structlog
from rich.console import Console
from rich.table import Table

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    run_with_handlers,
    safe_path_text,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import ProjectInfo
from gflow_cli.api.image import Aspect, GenerateImageRequest, ImageRef, Model, reference_cap_for
from gflow_cli.api.transports import transport_choices
from gflow_cli.config import get_settings
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.errors import ConfigurationError, DataStoreError
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

_CmdFn = TypeVar("_CmdFn", bound="Callable[..., object]")

# Case-insensitive 8-4-4-4-12 hex with hyphens — Flow's media UUIDs.
# When a `--ref` value matches this regex it's treated as an already-uploaded
# asset and passed through verbatim; anything else is treated as a local path
# that needs to be uploaded first.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_CREATING_PROJECT_MSG = "  Creating project..."
_T2I_PROJECT_TITLE = "gflow-cli t2i"
_I2I_PROJECT_TITLE = "gflow-cli i2i"

# Flow project / character-entity ids interpolated into navigation URLs and UI
# selectors. Mirror routes._PROJECT_ID_RE so untrusted --project / --reference-entity
# values are rejected at the CLI boundary (closes the page.goto + CSS-selector
# injection gaps before the value ever reaches the browser).
_FLOW_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,128}$")


def _validate_project_id(
    _ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    """Reject a --project id that isn't alphanumeric/hyphen (1-128 chars).

    The value is interpolated into the editor navigation URL (`page.goto`) before
    any other guard runs, so validate here at the boundary.
    """
    if value is not None and not _FLOW_ID_RE.fullmatch(value):
        msg = "project id must be 1-128 chars of letters, digits, or hyphens."
        raise click.BadParameter(msg, param=param)
    return value


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
    if _UUID_RE.fullmatch(ref):
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
# t2i subcommand
# ---------------------------------------------------------------------------


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
def t2i(
    prompts: tuple[str, ...],
    prompts_file: Path | None,
    read_stdin: bool,
    continue_on_error: bool,
    model: str,
    aspect: str,
    count: int,
    out: Path | None,
    profile: str | None,
    transport: str | None,
    project_id: str | None,
    reference_entities: tuple[str, ...],
    reference_entity_names: tuple[str, ...],
    as_json: bool,
) -> None:
    """Generate image(s) from one or more text prompts."""
    is_multi_prompt = len(prompts) > 1 or prompts_file is not None or read_stdin
    _validate_t2i_input(prompts, prompts_file, read_stdin)

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
                ),
                count=count,
                out=out,
                output_root=settings.output_dir,
                transport=transport,
                project_id=project_id,
                as_json=as_json,
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
    _execute_t2i_batch(batch_prompts, count, continue_on_error, profile, out, transport)


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
) -> None:
    """Run a multi-prompt t2i batch and print the summary table."""
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
                _profile_name=profile_name,
                _recorder=recorder,
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
) -> None:
    settings = get_settings()
    recorder = OperationRecorder.open(settings)
    try:
        async with FlowApiClient(
            profile_dir=profile_dir,
            headless=headless,
            transport=transport,
        ) as client:
            # Title is a `gflow-cli ...` prefix per project convention (post-rename a02684f).
            # cli_video.py's _run_t2v / _run_i2v don't currently set a title — tracked separately.
            project, project_created = await _resolve_project(
                client, project_id=project_id, title=_T2I_PROJECT_TITLE, as_json=as_json
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

            saved_paths: list[Path] = []
            for i, img in enumerate(images, start=1):
                target = (
                    out / f"{img.media_name}_{i}.png"
                    if out is not None
                    else image_output_path(output_root, job_id=img.media_name, index=i)
                )
                saved = await client.download_image(img, target)
                saved_paths.append(saved)

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

            try:
                recorder.record_generated_images(
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    project=project,
                    project_created=project_created,
                    request=req,
                    images=images,
                    saved_paths=saved_paths,
                    cloud_storage_infos=[cloud_info_from_path(path) for path in saved_paths],
                    input_media_ids=[],
                    operation_kind="t2i",
                )
            except DataStoreError as exc:
                first_image = images[0] if images else None
                first_path = saved_paths[0] if saved_paths else None
                _warn_persistence_failed_after_success(
                    exc=exc,
                    flow_media_id=first_image.media_name if first_image else None,
                    local_path=first_path,
                )
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
        "All prompts share one Flow project (stay-mounted editor). A 3-7s\n"
        "jitter is applied between submissions as an anti-bot courtesy.\n\n"
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
    out: Path | None,
    profile: str | None,
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


@image.command(
    "i2i",
    short_help="Generate image(s) from a prompt + one or more reference images.",
    help=(
        "Image-to-image generation: blend a text prompt with one or more "
        "reference images. Each --ref is either a local image path (auto-uploaded) "
        "or an already-uploaded asset UUID (from a prior `gflow image upload`).\n\n"
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
        "Reference image: either a local path (auto-uploaded) or an already-uploaded "
        "asset UUID. Repeat to pass multiple refs (order is preserved). "
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
def i2i(
    prompt: str,
    refs: tuple[str, ...],
    model: str,
    aspect: str,
    count: int,
    out: Path | None,
    profile: str | None,
    transport: str | None,
    project_id: str | None,
    reference_entities: tuple[str, ...],
    reference_entity_names: tuple[str, ...],
    as_json: bool,
) -> None:
    """Generate image(s) from PROMPT + reference image(s) (image-to-image)."""
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
    run_with_handlers(
        lambda: _run_i2i(
            profile_name=profile_name,
            profile_dir=provider_dir,
            headless=settings.headless,
            prompt=prompt,
            classified_refs=classified_refs,
            aspect=Aspect.from_cli(aspect),
            model=model_enum,
            count=count,
            out=out,
            output_root=settings.output_dir,
            transport=transport,
            project_id=project_id,
            reference_entities=tuple(reference_entities),
            reference_entity_names=tuple(reference_entity_names),
            as_json=as_json,
        ),
        cli_command="image i2i",
        as_json=as_json,
    )


async def _run_i2i(
    *,
    profile_name: str,
    profile_dir: Path,
    headless: bool,
    prompt: str,
    classified_refs: list[ImageRef | Path],
    aspect: Aspect,
    model: Model,
    count: int,
    out: Path | None,
    output_root: Path,
    transport: str | None = None,
    project_id: str | None = None,
    reference_entities: tuple[str, ...] = (),
    reference_entity_names: tuple[str, ...] = (),
    as_json: bool = False,
) -> None:
    settings = get_settings()
    recorder = OperationRecorder.open(settings)
    try:
        async with FlowApiClient(
            profile_dir=profile_dir,
            headless=headless,
            transport=transport,
        ) as client:
            # Title is a `gflow-cli ...` prefix per project convention (post-rename a02684f).
            # cli_video.py's _run_t2v / _run_i2v don't currently set a title — tracked separately.
            project, project_created = await _resolve_project(
                client, project_id=project_id, title=_I2I_PROJECT_TITLE, as_json=as_json
            )

            # Local-file refs are attached through the editor's media dialog by the
            # ui_automation transport (the REST uploadImage path 401s — see #15/#39).
            # Already-uploaded UUID refs go on `refs` (best-effort; binding a library
            # asset by UUID via the UI is not wired yet — local files are the path).
            uuid_refs = tuple(r for r in classified_refs if isinstance(r, ImageRef))
            local_ref_paths = tuple(r for r in classified_refs if isinstance(r, Path))
            req = GenerateImageRequest(
                prompt=prompt,
                aspect=aspect,
                model=model,
                refs=uuid_refs,
                ref_paths=local_ref_paths,
                reference_entities=reference_entities,
                reference_entity_names=reference_entity_names,
            )

            n_refs = len(uuid_refs) + len(local_ref_paths)
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

            saved_paths: list[Path] = []
            for i, img in enumerate(images, start=1):
                target = (
                    out / f"{img.media_name}_{i}.png"
                    if out is not None
                    else image_output_path(output_root, job_id=img.media_name, index=i)
                )
                saved = await client.download_image(img, target)
                saved_paths.append(saved)

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

            try:
                recorder.record_generated_images(
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    project=project,
                    project_created=project_created,
                    request=req,
                    images=images,
                    saved_paths=saved_paths,
                    cloud_storage_infos=[cloud_info_from_path(path) for path in saved_paths],
                    # Only already-uploaded UUID refs have a flow_media_id we
                    # can persist as INPUT. Local files attached via the media
                    # dialog don't surface a media_id at this layer; the recorder
                    # will skip them silently (record_generated_images guards on
                    # repo.get_asset_by_flow_media_id returning None).
                    input_media_ids=[ref.name for ref in uuid_refs],
                    operation_kind="i2i",
                )
            except DataStoreError as exc:
                first_image = images[0] if images else None
                first_path = saved_paths[0] if saved_paths else None
                _warn_persistence_failed_after_success(
                    exc=exc,
                    flow_media_id=first_image.media_name if first_image else None,
                    local_path=first_path,
                )
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


# ---------------------------------------------------------------------------
# avatar subcommand
# ---------------------------------------------------------------------------


@image.command(
    "avatar",
    short_help="Generate image(s) from a prompt + a pre-existing Flow avatar.",
    help=(
        "Avatar generation: use a pre-existing Flow avatar/likeness with a text prompt.\n\n"
        "Flow's avatar is selected automatically via UI automation — "
        "no UUID needed. Unlike i2i (which uploads a local image), avatar "
        "uses your pre-existing Flow character.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow image avatar "raise a fist in Paris" --avatar\n'
        '  gflow image avatar "walk through Tokyo" --avatar -n 4\n'
        '  gflow image avatar "cinematic portrait" --avatar --aspect 1:1\n\n'
        "For local-file reference images, use `gflow image i2i` instead."
    ),
)
@click.argument("prompt")
@click.option(
    "--avatar",
    "use_avatar",
    is_flag=True,
    default=True,
    help="Use your Flow avatar (UI automation clicks Avatar → Add to Prompt).",
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
        "<output_dir>/images/<YYYY-MM-DD>/."
    ),
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--transport",
    type=click.Choice(transport_choices(), case_sensitive=False),
    default=None,
    help="Override transport strategy.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a machine-readable JSON result instead of a Rich table.",
)
def avatar(
    prompt: str,
    use_avatar: bool,
    model: str,
    aspect: str,
    count: int,
    out: Path | None,
    profile: str | None,
    transport: str | None,
    as_json: bool,
) -> None:
    """Generate image(s) from PROMPT using your Flow avatar."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_avatar(
            profile_name=profile_name,
            profile_dir=provider_dir,
            headless=settings.headless,
            prompt=prompt,
            aspect=Aspect.from_cli(aspect),
            model=Model.from_cli(model),
            count=count,
            out=out,
            output_root=settings.output_dir,
            transport=transport,
            as_json=as_json,
        ),
        cli_command="image avatar",
        as_json=as_json,
    )


async def _run_avatar(
    *,
    profile_name: str,
    profile_dir: Path,
    headless: bool,
    prompt: str,
    aspect: Aspect,
    model: Model,
    count: int,
    out: Path | None,
    output_root: Path,
    transport: str | None = None,
    as_json: bool = False,
) -> None:
    settings = get_settings()
    recorder = OperationRecorder.open(settings)
    try:
        async with FlowApiClient(
            profile_dir=profile_dir,
            headless=headless,
            transport=transport,
        ) as client:
            if not as_json:
                console.print(_CREATING_PROJECT_MSG)
            project = await client.create_project(title="gflow-cli avatar")
            if not as_json:
                console.print(f"  Project: [dim]{project.project_id}[/dim]")

            req = GenerateImageRequest(
                prompt=prompt,
                aspect=aspect,
                model=model,
                use_avatar=True,
            )

            if not as_json:
                console.print(
                    f"  Generating {count} image(s) with avatar "
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

            saved_paths: list[Path] = []
            for i, img in enumerate(images, start=1):
                target = (
                    out / f"{img.media_name}_{i}.png"
                    if out is not None
                    else image_output_path(output_root, job_id=img.media_name, index=i)
                )
                saved = await client.download_image(img, target)
                saved_paths.append(saved)

            if as_json:
                json_output.emit(
                    json_output.image_result(
                        command="image avatar",
                        project_id=project.project_id,
                        model=req.model.value,
                        images=images,
                        saved_paths=saved_paths,
                    ),
                )
            else:
                _print_i2i_summary(images, saved_paths)

            try:
                recorder.record_generated_images(
                    profile_name=profile_name,
                    profile_dir=profile_dir,
                    project=project,
                    request=req,
                    images=images,
                    saved_paths=saved_paths,
                    cloud_storage_infos=[cloud_info_from_path(path) for path in saved_paths],
                    input_media_ids=[],
                    operation_kind="avatar",
                )
            except DataStoreError as exc:
                first_image = images[0] if images else None
                first_path = saved_paths[0] if saved_paths else None
                _warn_persistence_failed_after_success(
                    exc=exc,
                    flow_media_id=first_image.media_name if first_image else None,
                    local_path=first_path,
                )
    finally:
        recorder.close()
