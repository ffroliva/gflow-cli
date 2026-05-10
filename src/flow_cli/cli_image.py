"""`gflow image` command group — image asset operations.

Subcommands:

* ``upload PATH`` — uploads a local image into a Flow project's library and
  prints the resulting media UUID and inferred dimensions.
* ``t2i PROMPT`` — text-to-image generation (1-4 images per call).

Helper functions ``_resolve_profile`` and ``_make_provider_dir`` mirror the
ones in :mod:`flow_cli.cli_video` so the test suite can patch them locally.
We deliberately duplicate them (rather than re-export) to keep each command
group self-contained: a future split into ``cli/image.py``/``cli/video.py``
should not require a cross-module patch dance in tests.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from flow_cli import auth as auth_mod
from flow_cli import profile_store
from flow_cli.api.client import FlowApiClient
from flow_cli.api.dto import GeneratedImage
from flow_cli.api.image import Aspect, GenerateImageRequest, Model
from flow_cli.config import get_settings
from flow_cli.paths import image_output_path

console = Console()


def _resolve_profile(profile: str | None) -> str:
    """Return the active profile name or exit with a friendly message."""
    if profile:
        return profile
    try:
        return profile_store.resolve_profile(None)
    except profile_store.NoProfilesError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        sys.exit(2)
    except profile_store.NoDefaultProfileError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        sys.exit(2)


def _make_provider_dir(profile_name: str) -> Path:
    """Return the Playwright profile dir for *profile_name*, or exit if absent."""
    pdir = auth_mod.profile_dir(profile_name)
    if not pdir.exists():
        console.print(
            f"[red]No session for profile '{profile_name}'.[/red] "
            "Run [bold]gflow auth login[/bold] first."
        )
        sys.exit(2)
    return pdir


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group()
def image() -> None:
    """Upload and generate images via Google Flow Imagen.

    Provides ``upload`` and ``t2i``. Image-to-image (``i2i``) lands in a
    subsequent task.
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
def upload(path: Path, profile: str | None) -> None:
    """Upload PATH and print the asset UUID."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    asyncio.run(
        _run_upload(
            profile_dir=provider_dir,
            headless=settings.headless,
            image_path=path,
        )
    )


async def _run_upload(
    *,
    profile_dir: Path,
    headless: bool,
    image_path: Path,
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
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
    help="Output directory. Defaults to <output_dir>/images/<YYYY-MM-DD>/.",
)
@click.option("--profile", default=None, help="Profile name (overrides default).")
def t2i(
    prompt: str,
    model: str,
    aspect: str,
    count: int,
    seed: int | None,
    out: Path | None,
    profile: str | None,
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
    asyncio.run(
        _run_t2i(
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
        )
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
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        console.print("  Creating project...")
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
