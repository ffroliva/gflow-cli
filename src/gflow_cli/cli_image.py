"""`gflow image` command group — image asset operations.

Subcommands:

* ``upload PATH`` — uploads a local image into a Flow project's library and
  prints the resulting media UUID and inferred dimensions.
* ``t2i PROMPT`` — text-to-image generation (1-4 images per call).
* ``i2i PROMPT --ref PATH_OR_UUID`` — image-to-image with seed references.

The profile/auth helpers ``_resolve_profile`` and ``_make_provider_dir`` live
in :mod:`gflow_cli._cli_helpers` since T4b — a negative AST-based test in
``tests/cli/test_helpers.py`` prevents drift back into this module.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    run_with_handlers,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import Aspect, GenerateImageRequest, ImageRef, Model
from gflow_cli.config import get_settings
from gflow_cli.paths import image_output_path

# Case-insensitive 8-4-4-4-12 hex with hyphens — Flow's media UUIDs.
# When a `--ref` value matches this regex it's treated as an already-uploaded
# asset and passed through verbatim; anything else is treated as a local path
# that needs to be uploaded first.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

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
    type=click.Choice(["evaluate_fetch", "bearer", "sapisidhash"], case_sensitive=False),
    default=None,
    help=(
        "Override transport strategy. Falls back to GFLOW_CLI_TRANSPORT env var "
        "or built-in default (evaluate_fetch)."
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
        console.print("  Creating project...")
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

# Click choices kept aligned with `Model.from_cli` and `Aspect.from_cli` aliases.
_MODEL_CHOICES = ["nano2", "nano-pro", "image4"]
_ASPECT_CHOICES = ["9:16", "16:9", "1:1", "4:3", "3:4"]


@image.command(
    "t2i",
    short_help="Generate image(s) from a text prompt.",
    help=(
        "Generate 1-4 images from a text prompt using Google Flow's Imagen / "
        "Nano Banana models.\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow image t2i "a serene mountain lake at dawn"\n'
        '  gflow image t2i "neon cyberpunk alley" --model nano-pro --aspect 16:9\n'
        '  gflow image t2i "variations of a logo" -n 4 --aspect 1:1\n'
        '  gflow image t2i "reproducible shot" --seed 42\n\n'
        "Note: --seed is only valid when generating a single image (-n 1)."
    ),
)
@click.argument("prompt")
@click.option(
    "--model",
    default="nano2",
    show_default=True,
    type=click.Choice(_MODEL_CHOICES),
    help="Image model alias.",
)
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(_ASPECT_CHOICES),
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
    "--seed",
    default=None,
    type=int,
    help="RNG seed for reproducibility (only valid when -n 1).",
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
    type=click.Choice(["evaluate_fetch", "bearer", "sapisidhash"], case_sensitive=False),
    default=None,
    help=(
        "Override transport strategy. Falls back to GFLOW_CLI_TRANSPORT env var "
        "or built-in default (evaluate_fetch)."
    ),
)
def t2i(
    prompt: str,
    model: str,
    aspect: str,
    count: int,
    seed: int | None,
    out: Path | None,
    profile: str | None,
    transport: str | None,
) -> None:
    """Generate image(s) from PROMPT (text-to-image)."""
    # Validate flag combinations BEFORE any I/O. Click's IntRange already
    # bounds count to [1, 4]; here we enforce the cross-flag rule that --seed
    # is only meaningful when generating a single image (multi-image fan-out
    # uses N independent random seeds and a shared batch_id).
    if seed is not None and count != 1:
        raise click.UsageError(
            "--seed is only valid when generating a single image (-n 1). "
            "For multi-image runs, omit --seed and let each shot get its own."
        )

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
            seed=seed,
            out=out,
            output_root=settings.output_dir,
            transport=transport,
        ),
        cli_command="image t2i",
    )


async def _run_t2i(
    *,
    profile_dir: Path,
    headless: bool,
    req: GenerateImageRequest,
    count: int,
    seed: int | None,
    out: Path | None,
    output_root: Path,
    transport: str | None = None,
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        console.print("  Creating project...")
        # Title is a `gflow-cli ...` prefix per project convention (post-rename a02684f).
        # cli_video.py's _run_t2v / _run_i2v don't currently set a title — tracked separately.
        project = await client.create_project(title="gflow-cli t2i")
        console.print(f"  Project: [dim]{project.project_id}[/dim]")
        console.print(f"  Generating {count} image(s) ({req.model.value}, {req.aspect.value})...")
        if count == 1:
            img = await client.generate_image(project_id=project.project_id, req=req, seed=seed)
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
    table = Table(title="gflow-cli t2i")
    table.add_column("media_name", overflow="fold")
    table.add_column("seed", justify="right")
    table.add_column("dimensions")
    table.add_column("output_path", overflow="fold")
    for img, path in zip(images, saved_paths, strict=True):
        w, h = img.dimensions
        table.add_row(img.media_name, str(img.seed), f"{w}x{h}", str(path))
    console.print(table)


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
        "For text-only generation, use `gflow image t2i` instead.\n"
        "Note: --seed is only valid when generating a single image (-n 1)."
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
    default="nano2",
    show_default=True,
    type=click.Choice(_MODEL_CHOICES),
    help="Image model alias.",
)
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(_ASPECT_CHOICES),
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
    "--seed",
    default=None,
    type=int,
    help="RNG seed for reproducibility (only valid when -n 1).",
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
    type=click.Choice(["evaluate_fetch", "bearer", "sapisidhash"], case_sensitive=False),
    default=None,
    help=(
        "Override transport strategy. Falls back to GFLOW_CLI_TRANSPORT env var "
        "or built-in default (evaluate_fetch)."
    ),
)
def i2i(
    prompt: str,
    refs: tuple[str, ...],
    model: str,
    aspect: str,
    count: int,
    seed: int | None,
    out: Path | None,
    profile: str | None,
    transport: str | None,
) -> None:
    """Generate image(s) from PROMPT + reference image(s) (image-to-image)."""
    # Mirror t2i's seed/count cross-flag rule — see _run_t2i for the rationale.
    if seed is not None and count != 1:
        raise click.UsageError(
            "--seed is only valid when generating a single image (-n 1). "
            "For multi-image runs, omit --seed and let each shot get its own."
        )

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
            seed=seed,
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
    seed: int | None,
    out: Path | None,
    output_root: Path,
    transport: str | None = None,
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        console.print("  Creating project...")
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
            img = await client.generate_image(project_id=project.project_id, req=req, seed=seed)
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
        table.add_row(img.media_name, str(img.seed), f"{w}x{h}", str(path))
    console.print(table)
