"""`gflow video` command group — t2v subcommand (text-to-video).

The profile/auth helpers ``_resolve_profile`` and ``_make_provider_dir`` live
in :mod:`gflow_cli._cli_helpers` since T4b — a negative AST-based test in
``tests/cli/test_helpers.py`` prevents drift back into this module.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console

from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    run_with_handlers,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.video import Aspect, GenerateVideoRequest
from gflow_cli.config import get_settings
from gflow_cli.manifest import ManifestEntry, parse_manifest
from gflow_cli.paths import video_output_path

console = Console()

# Terminal statuses that end the polling loop.
_TERMINAL = frozenset(
    [
        "MEDIA_GENERATION_STATUS_COMPLETED",
        "MEDIA_GENERATION_STATUS_FAILED",
    ]
)


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group()
def video() -> None:
    """Generate and manage videos via Google Flow Veo."""


# ---------------------------------------------------------------------------
# t2v subcommand
# ---------------------------------------------------------------------------


@video.command("t2v")
@click.argument("prompt")
@click.option(
    "-o",
    "--output",
    "output",
    default=None,
    type=click.Path(path_type=Path),
    help="Where to save the mp4. Defaults to <output_dir>/videos/<date>/<media>.mp4.",
)
@click.option(
    "--aspect",
    default="9:16",
    show_default=True,
    type=click.Choice(["9:16", "16:9", "1:1"]),
    help="Video aspect ratio.",
)
@click.option("--seed", default=None, type=int, help="RNG seed for reproducibility.")
@click.option("--profile", default=None, help="Profile name (overrides default).")
@click.option(
    "--poll-interval",
    default=5.0,
    show_default=True,
    type=float,
    help="Seconds between status polls.",
)
def t2v(
    prompt: str,
    output: Path | None,
    aspect: str,
    seed: int | None,
    profile: str | None,
    poll_interval: float,
) -> None:
    """Generate a video from PROMPT (text-to-video)."""
    profile_name = _resolve_profile(profile)
    provider_dir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_t2v(
            profile_dir=provider_dir,
            headless=settings.headless,
            prompt=prompt,
            output=output,
            aspect=Aspect.from_cli(aspect),
            seed=seed,
            poll_interval=poll_interval,
            output_root=settings.output_dir,
        ),
        cli_command="video t2v",
    )


async def _run_t2v(
    *,
    profile_dir: Path,
    headless: bool,
    prompt: str,
    output: Path | None,
    aspect: Aspect,
    seed: int | None,
    poll_interval: float,
    output_root: Path,
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        console.print("  Creating project...")
        project = await client.create_project()
        console.print(f"  Project: {project.project_id}")
        req = GenerateVideoRequest(prompt=prompt, aspect=aspect)
        console.print("  Submitting generation...")
        op = await client.generate_video(project_id=project.project_id, req=req, seed=seed)
        console.print(f"  Operation: {op.operation_name}")
        await _poll_and_download(
            client=client,
            project_id=project.project_id,
            media_name=op.media_name,
            output=output or video_output_path(output_root, job_id=op.media_name),
            poll_interval=poll_interval,
        )


# ---------------------------------------------------------------------------
# Shared polling helper
# ---------------------------------------------------------------------------


async def _poll_and_download(
    *,
    client: FlowApiClient,
    project_id: str,
    media_name: str,
    output: Path,
    poll_interval: float,
) -> None:
    """Poll until terminal status, then download the result to *output*."""
    while True:
        statuses = await client.get_video_status(project_id, [media_name])
        if not statuses:
            console.print("[red]No status returned from API.[/red]")
            sys.exit(1)
        st = statuses[0]
        console.print(f"  Status: {st.status}")
        if st.status in _TERMINAL:
            break
        await asyncio.sleep(poll_interval)

    if not st.succeeded:
        console.print(f"[red]Generation failed (status={st.status}).[/red]")
        sys.exit(1)

    saved = await client.download(media_name, output)
    console.print(f"[green]Saved[/green] {saved}")


# ---------------------------------------------------------------------------
# i2v subcommand
# ---------------------------------------------------------------------------


@video.command("i2v")
@click.argument("image", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("prompt")
@click.option("-o", "--output", type=click.Path(path_type=Path), default=None)
@click.option("--aspect", type=click.Choice(["9:16", "16:9", "1:1"]), default="9:16")
@click.option("--seed", type=int, default=None)
@click.option("--profile", default=None)
@click.option("--poll-interval", type=float, default=5.0)
def i2v(
    image: Path,
    prompt: str,
    output: Path | None,
    aspect: str,
    seed: int | None,
    profile: str | None,
    poll_interval: float,
) -> None:
    """Generate a video from a START IMAGE + text prompt."""
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    run_with_handlers(
        lambda: _run_i2v(
            profile_dir=pdir,
            headless=settings.headless,
            image=image,
            prompt=prompt,
            output=output,
            aspect=Aspect.from_cli(aspect),
            seed=seed,
            poll_interval=poll_interval,
            output_root=settings.output_dir,
        ),
        cli_command="video i2v",
    )


async def _run_i2v(
    *,
    profile_dir: Path,
    headless: bool,
    image: Path,
    prompt: str,
    output: Path | None,
    aspect: Aspect,
    seed: int | None,
    poll_interval: float,
    output_root: Path,
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        console.print("  Creating project...")
        project = await client.create_project()
        console.print(f"  Uploading {image.name}...")
        asset = await client.upload_image(project.project_id, image)
        console.print(f"  Asset: [dim]{asset.name}[/dim]")
        req = GenerateVideoRequest(prompt=prompt, aspect=aspect, start_asset_uuid=asset.name)
        console.print("  Submitting generation...")
        op = await client.generate_video(project_id=project.project_id, req=req, seed=seed)
        await _poll_and_download(
            client=client,
            project_id=project.project_id,
            media_name=op.media_name,
            output=output or video_output_path(output_root, job_id=op.media_name),
            poll_interval=poll_interval,
        )


# ---------------------------------------------------------------------------
# batch subcommand
# ---------------------------------------------------------------------------


@video.command("batch")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out-dir", type=click.Path(path_type=Path), default=None)
@click.option("--profile", default=None)
@click.option("--poll-interval", type=float, default=5.0, show_default=True)
def batch(
    manifest: Path,
    out_dir: Path | None,
    profile: str | None,
    poll_interval: float,
) -> None:
    """Run a TSV manifest of video generations."""
    profile_name = _resolve_profile(profile)
    pdir = _make_provider_dir(profile_name)
    settings = get_settings()
    entries = parse_manifest(manifest)
    out_root = out_dir or settings.output_dir
    run_with_handlers(
        lambda: _run_batch(
            profile_dir=pdir,
            headless=settings.headless,
            entries=entries,
            out_root=out_root,
            poll_interval=poll_interval,
        ),
        cli_command="video batch",
    )


async def _run_batch(
    *,
    profile_dir: Path,
    headless: bool,
    entries: list[ManifestEntry],
    out_root: Path,
    poll_interval: float,
) -> None:
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        console.print(f"  Creating project for {len(entries)} clips...")
        project = await client.create_project()
        for i, e in enumerate(entries, start=1):
            console.print(f"  [{i}/{len(entries)}] [bold]{e.prompt[:60]}[/bold]")
            start_uuid = None
            if e.start_image:
                asset = await client.upload_image(project.project_id, e.start_image)
                start_uuid = asset.name
            req = GenerateVideoRequest(
                prompt=e.prompt, aspect=e.aspect, start_asset_uuid=start_uuid
            )
            op = await client.generate_video(project_id=project.project_id, req=req)
            output = e.output_path or video_output_path(out_root, job_id=op.media_name)
            await _poll_and_download(
                client=client,
                project_id=project.project_id,
                media_name=op.media_name,
                output=output,
                poll_interval=poll_interval,
            )
