"""`gflow image` command group — image asset operations.

Currently exposes a single subcommand:

* ``upload PATH`` — uploads a local image into a Flow project's library and
  prints the resulting media UUID and inferred dimensions.

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

from flow_cli import auth as auth_mod
from flow_cli import profile_store
from flow_cli.api.client import FlowApiClient
from flow_cli.config import get_settings

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

    Currently provides ``upload``. Text-to-image (``t2i``) and
    image-to-image (``i2i``) land in subsequent tasks.
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
