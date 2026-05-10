"""End-to-end smoke test — runs ONE T2I generation against live Flow.

Usage:
    uv run python scripts/smoke_image.py [--prompt "..."] [--profile NAME] \\
        [--model narwhal] [--aspect 9:16] [-n 1]

Pre-reqs: `gflow auth login` must have been run for the profile in question.
The script burns ~1 image credit per result (so `-n 4` burns ~4 credits).

Mirrors `scripts/smoke_e2e.py`, but for the synchronous image-generation
route — there is no polling because `generate_images_batch` returns the
finished `GeneratedImage` directly.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from gflow_cli import auth as auth_mod
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.config import get_settings
from gflow_cli.paths import image_output_path

console = Console()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="a cinematic close-up of a candle flame")
    parser.add_argument("--profile", default=None)
    parser.add_argument(
        "--aspect",
        default="9:16",
        choices=["9:16", "16:9", "1:1", "4:3", "3:4"],
    )
    parser.add_argument(
        "--model",
        default="narwhal",
        choices=[
            "narwhal",
            "nano2",
            "nano-banana-2",
            "nano_banana_2",
            "nanobanana2",
            "gem_pix_2",
            "gem-pix-2",
            "nano-pro",
            "nano_pro",
            "nanopro",
            "imagen_3_5",
            "imagen-3-5",
            "image4",
            "imagen4",
        ],
        help="Image model alias (e.g. narwhal, nano-pro, imagen4).",
    )
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=1,
        choices=range(1, 5),
        help="How many images to generate (1..4).",
    )
    args = parser.parse_args()

    settings = get_settings()
    profile_name = args.profile or settings.profile or "default"
    pdir = auth_mod.profile_dir(profile_name)
    if not pdir.exists():
        console.print(
            f"[red]No session for profile '{escape(profile_name)}'.[/red] "
            f"Run [bold]gflow auth login[/bold] first."
        )
        return 2

    console.print(
        f"[bold]Smoke test:[/bold] T2I '{escape(args.prompt)}' "
        f"(model={args.model}, aspect={args.aspect}, count={args.count})"
    )
    async with FlowApiClient(profile_dir=pdir, headless=settings.headless) as client:
        project = await client.create_project(title="gflow-cli smoke image")
        console.print(f"  Project: [dim]{escape(project.project_id)}[/dim]")

        req = GenerateImageRequest(
            prompt=args.prompt,
            aspect=Aspect.from_cli(args.aspect),
            model=Model.from_cli(args.model),
        )
        console.print("  Generating (this is synchronous — no polling)...")
        images = await client.generate_images_batch(
            project_id=project.project_id, req=req, count=args.count
        )
        if not images:
            console.print("[red]Empty response from API — aborting.[/red]")
            return 1

        table = Table(title="Generated images", show_lines=False)
        table.add_column("#", justify="right", style="cyan", no_wrap=True)
        table.add_column("media_name", style="dim")
        table.add_column("path", style="green")
        table.add_column("bytes", justify="right")

        failures = 0
        for i, img in enumerate(images, start=1):
            out = image_output_path(settings.output_dir, job_id=img.media_name, index=i)
            try:
                # Defense-in-depth: download_image already mkdirs, but mirror
                # the explicit pattern used in scripts/smoke_e2e.py.
                out.parent.mkdir(parents=True, exist_ok=True)
                await client.download_image(img, out)
                table.add_row(
                    str(i),
                    escape(img.media_name),
                    escape(str(out)),
                    f"{out.stat().st_size:,}",
                )
            except Exception as exc:  # noqa: BLE001 — partial-failure summary
                failures += 1
                console.print(f"  [red]download {i} failed: {escape(str(exc))}[/red]")
                table.add_row(
                    str(i),
                    escape(img.media_name),
                    "[red]FAILED[/red]",
                    "-",
                )

        console.print(table)
        ok = len(images) - failures
        if failures:
            console.print(
                f"[yellow]Partial:[/yellow] {ok}/{len(images)} image(s) written, {failures} failed"
            )
            return 1
        console.print(f"[green]OK[/green] -> {ok} image(s) written")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
