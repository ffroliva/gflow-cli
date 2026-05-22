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

import click
from rich.console import Console
from rich.table import Table

from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    run_with_handlers,
    safe_path_text,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import Aspect, GenerateImageRequest, ImageRef, Model
from gflow_cli.api.transports import transport_choices
from gflow_cli.config import get_settings
from gflow_cli.errors import ConfigurationError
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

console = Console()


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

    Centralized here so the ``i2i`` Click callback (upfront validation) and
    the ``_resolve_refs`` async helper (dispatch) share one implementation
    instead of duplicating the UUID regex check.

    Raises:
        click.UsageError: if *ref* is neither a UUID nor an existing path.
    """
    if _UUID_RE.fullmatch(ref):
        return ImageRef(name=ref)
    try:
        return Path(ref).resolve(strict=True)
    except FileNotFoundError as exc:
        raise click.UsageError(
            f"--ref {ref!r} does not exist as a file and is not a valid asset UUID. "
            "Pass either a local image path or a 32-char hex UUID with hyphens "
            "(from a prior `gflow image upload`)."
        ) from exc


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
            profile_dir=provider_dir,
            headless=settings.headless,
            image_path=path,
            transport=transport,
        ),
        cli_command="image upload",
    )


async def _run_upload(
    *,
    profile_dir: Path,
    headless: bool,
    image_path: Path,
    transport: str | None = None,
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
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
            f"[dim]Project:[/dim] {project.project_id}"
        )


# ---------------------------------------------------------------------------
# t2i subcommand
# ---------------------------------------------------------------------------

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
) -> None:
    """Generate image(s) from one or more text prompts."""
    is_multi_prompt = len(prompts) > 1 or prompts_file is not None or read_stdin
    _validate_t2i_input(prompts, prompts_file, read_stdin)

    if not is_multi_prompt:
        if not prompts:
            raise click.UsageError(
                "Provide a prompt, multiple prompts, --prompts-file, or --stdin."
            )
        prompt = prompts[0]
        profile_name = _resolve_profile(profile)
        provider_dir = _make_provider_dir(profile_name)
        settings = get_settings()
        run_with_handlers(
            lambda: _run_t2i(
                profile_dir=provider_dir,
                headless=settings.headless,
                req=GenerateImageRequest(
                    prompt=prompt,
                    aspect=Aspect.from_cli(aspect),
                    model=Model.from_cli(model),
                ),
                count=count,
                out=out,
                output_root=settings.output_dir,
                transport=transport,
            ),
            cli_command="image t2i",
        )
        return

    try:
        if prompts_file is not None:
            parsed = read_prompt_file(prompts_file)
            batch_prompts = prompt_items_from_parsed(
                parsed,
                aspect_ratio=aspect,
                model=model,
                count=count,
            )
        elif read_stdin:
            # Security: implement a bounded read for standard input to prevent OOM
            # crashes if a massive stream is piped to the CLI.
            raw_stdin = sys.stdin.read(MAX_PROMPT_FILE_BYTES + 1)
            if len(raw_stdin) > MAX_PROMPT_FILE_BYTES:
                raise click.UsageError(
                    f"Standard input exceeds the maximum allowed size of "
                    f"{MAX_PROMPT_FILE_BYTES // 1024} KiB."
                )
            parsed = parse_prompt_lines(raw_stdin, source_label="--stdin")
            batch_prompts = prompt_items_from_parsed(
                parsed,
                aspect_ratio=aspect,
                model=model,
                count=count,
            )
        else:
            batch_prompts = prompt_items_from_texts(
                prompts,
                aspect_ratio=aspect,
                model=model,
                count=count,
                source_label="positional",
            )
    except ConfigurationError as exc:
        raise _as_usage_error(exc) from exc

    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    output_dir = resolve_batch_output_dir(
        cli_override=out, output_root=settings.output_dir, kind="images"
    )
    console.print(
        f"\n[bold]gflow image t2i[/bold] · profile=[bold]{profile_name}[/bold] "
        f"· {len(batch_prompts)} prompt(s) · up to {len(batch_prompts) * count} image(s)"
    )
    console.print(f"  output_dir: [dim]{safe_path_text(output_dir)}[/dim]")
    if not continue_on_error:
        console.print("  mode: [yellow]fail-fast[/yellow]")

    outcomes = asyncio.run(
        run_image_batch(
            profile_dir=provider_dir,
            headless=settings.headless,
            transport=transport,
            prompts=batch_prompts,
            output_dir=output_dir,
            continue_on_error=continue_on_error,
            project_title=_T2I_PROJECT_TITLE,
        )
    )
    exit_code = render_image_batch_summary(outcomes, title=_T2I_PROJECT_TITLE)
    if exit_code != 0:
        sys.exit(exit_code)


def _count_t2i_sources(
    prompts: tuple[str, ...], prompts_file: Path | None, read_stdin: bool
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
        raise click.UsageError("Provide a prompt, multiple prompts, --prompts-file, or --stdin.")
    if source_count > 1:
        raise click.UsageError(
            "Prompt sources are mutually exclusive: use positional prompts, "
            "--prompts-file, or --stdin."
        )


def _as_usage_error(exc: ConfigurationError) -> click.UsageError:
    return click.UsageError(str(exc))


async def _run_t2i(
    *,
    profile_dir: Path,
    headless: bool,
    req: GenerateImageRequest,
    count: int,
    out: Path | None,
    output_root: Path,
    transport: str | None = None,
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        console.print(_CREATING_PROJECT_MSG)
        # Title is a `gflow-cli ...` prefix per project convention (post-rename a02684f).
        # cli_video.py's _run_t2v / _run_i2v don't currently set a title — tracked separately.
        project = await client.create_project(title=_T2I_PROJECT_TITLE)
        console.print(f"  Project: [dim]{project.project_id}[/dim]")
        console.print(f"  Generating {count} image(s) ({req.model.value}, {req.aspect.value})...")
        if count == 1:
            img = await client.generate_image(project_id=project.project_id, req=req)
            images: list[GeneratedImage] = [img]
        else:
            images = await client.generate_images_batch(
                project_id=project.project_id, req=req, count=count
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

        _print_t2i_summary(images, saved_paths)


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
        cli_override=out, output_root=settings.output_dir, kind="images"
    )

    total_images = sum(p.count for p in prompts)
    console.print(
        f"\n[bold]{_BATCH_TITLE}[/bold] · profile=[bold]{profile_name}[/bold] "
        f"· {len(prompts)} prompt(s) · up to {total_images} image(s)"
    )
    console.print(f"  output_dir: [dim]{safe_path_text(output_dir)}[/dim]")
    if not continue_on_error:
        console.print("  mode: [yellow]fail-fast[/yellow]")

    outcomes = asyncio.run(
        run_manifest_image_batch(
            profile_dir=provider_dir,
            headless=settings.headless,
            transport=transport,
            prompts=prompts,
            output_dir=output_dir,
            continue_on_error=continue_on_error,
            project_title=_BATCH_TITLE,
        )
    )
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
def i2i(
    prompt: str,
    refs: tuple[str, ...],
    model: str,
    aspect: str,
    count: int,
    out: Path | None,
    profile: str | None,
    transport: str | None,
) -> None:
    """Generate image(s) from PROMPT + reference image(s) (image-to-image)."""
    # Classify each --ref upfront: UUIDs become ImageRef, path-likes become
    # canonical Paths (with symlinks resolved). _classify_ref raises
    # click.UsageError on missing/broken paths, which Click maps to exit 2.
    # Click's `multiple=True` with `required=True` already rejects the
    # "no --ref" case with exit 2 before we get here.
    classified_refs: list[ImageRef | Path] = [_classify_ref(ref) for ref in refs]

    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_i2i(
            profile_dir=provider_dir,
            headless=settings.headless,
            prompt=prompt,
            classified_refs=classified_refs,
            aspect=Aspect.from_cli(aspect),
            model=Model.from_cli(model),
            count=count,
            out=out,
            output_root=settings.output_dir,
            transport=transport,
        ),
        cli_command="image i2i",
    )


async def _resolve_refs(
    client: FlowApiClient,
    project_id: str,
    classified_refs: list[ImageRef | Path],
) -> tuple[ImageRef, ...]:
    """Resolve a pre-classified ref list into a tuple of :class:`ImageRef`.

    The input list is produced by :func:`_classify_ref` at the CLI boundary,
    so this helper only has to dispatch on type:

    * :class:`ImageRef` — append verbatim (already-uploaded asset).
    * :class:`Path` — upload and wrap the returned UUID.

    Uploads are sequential — parallel uploads are tempting but Flow's web UI
    uploads serially and we don't want to surprise the rate limiter. Order is
    preserved so the resulting ``imageInputs[]`` matches the order the user
    specified on the command line.
    """
    resolved: list[ImageRef] = []
    for item in classified_refs:
        if isinstance(item, ImageRef):
            resolved.append(item)
            continue
        # Per-file progress feedback. Acceptable Rich `console.print` inside
        # this async helper because cli_image.py *is* the CLI layer; structlog
        # will replace this when it lands in Phase 1.
        console.print(f"  Uploading {item.name}...")
        asset = await client.upload_image(project_id, item)
        resolved.append(ImageRef(name=asset.name))
    return tuple(resolved)


async def _run_i2i(
    *,
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
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        console.print(_CREATING_PROJECT_MSG)
        # Title is a `gflow-cli ...` prefix per project convention (post-rename a02684f).
        # cli_video.py's _run_t2v / _run_i2v don't currently set a title — tracked separately.
        project = await client.create_project(title="gflow-cli i2i")
        console.print(f"  Project: [dim]{project.project_id}[/dim]")

        resolved_refs = await _resolve_refs(client, project.project_id, classified_refs)
        req = GenerateImageRequest(
            prompt=prompt,
            aspect=aspect,
            model=model,
            refs=resolved_refs,
        )

        console.print(
            f"  Generating {count} image(s) with {len(resolved_refs)} ref(s) "
            f"({req.model.value}, {req.aspect.value})..."
        )
        if count == 1:
            img = await client.generate_image(project_id=project.project_id, req=req)
            images: list[GeneratedImage] = [img]
        else:
            images = await client.generate_images_batch(
                project_id=project.project_id, req=req, count=count
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

        _print_i2i_summary(images, saved_paths)


def _print_i2i_summary(images: list[GeneratedImage], saved_paths: list[Path]) -> None:
    """Render a Rich table of generated images and where they landed."""
    table = Table(title="gflow-cli i2i")
    table.add_column("media_name", overflow="fold")
    table.add_column("seed", justify="right")
    table.add_column("dimensions")
    table.add_column("output_path", overflow="fold")
    for img, path in zip(images, saved_paths, strict=True):
        w, h = img.dimensions
        table.add_row(img.media_name, str(img.seed), f"{w}x{h}", safe_path_text(path))
    console.print(table)
