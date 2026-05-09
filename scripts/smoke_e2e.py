"""End-to-end smoke test — runs ONE T2V generation against live Flow.

Usage:
    uv run python scripts/smoke_e2e.py [--prompt "..."] [--profile NAME]

Pre-reqs: `gflow auth login` must have been run for the profile in question.
The script burns ~1 Veo Fast credit per run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console
from rich.markup import escape

from flow_cli import auth as auth_mod
from flow_cli.api.client import FlowApiClient
from flow_cli.api.video import Aspect, GenerateVideoRequest
from flow_cli.config import get_settings
from flow_cli.paths import video_output_path

console = Console()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="a cinematic push-in on a candle flame")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--aspect", default="9:16", choices=["9:16", "16:9", "1:1"])
    args = parser.parse_args()

    settings = get_settings()
    profile_name = args.profile or settings.profile or "default"
    pdir = auth_mod.profile_dir(profile_name)
    if not pdir.exists():
        console.print(
            f"[red]No session for profile '{profile_name}'.[/red] "
            f"Run [bold]gflow auth login[/bold] first."
        )
        return 2

    console.print(f"[bold]Smoke test:[/bold] T2V '{escape(args.prompt)}'")
    async with FlowApiClient(profile_dir=pdir, headless=settings.headless) as client:
        project = await client.create_project(title="gflow-cli smoke test")
        console.print(f"  Project: [dim]{escape(project.project_id)}[/dim]")
        req = GenerateVideoRequest(prompt=args.prompt, aspect=Aspect.from_cli(args.aspect))
        op = await client.generate_video(project_id=project.project_id, req=req)
        console.print(f"  Operation: [dim]{escape(op.operation_name)}[/dim]")
        console.print("  Polling (this takes ~90-180 s)...")
        _MAX_POLLS = 72  # 6 minutes at 5 s interval
        for _ in range(_MAX_POLLS):
            statuses = await client.get_video_status(project.project_id, [op.media_name])
            if not statuses:
                console.print("[red]Empty status response from API — aborting.[/red]")
                return 1
            s = statuses[0]
            if s.is_terminal:
                if not s.succeeded:
                    console.print(f"[red]Failed:[/red] {escape(s.status)}")
                    return 1
                break
            console.print(f"  {escape(s.status)}...")
            await asyncio.sleep(5)
        else:
            console.print("[red]Timed out after 6 minutes — generation did not complete.[/red]")
            return 1
        out = video_output_path(settings.output_dir, job_id=op.media_name)
        out.parent.mkdir(parents=True, exist_ok=True)
        await client.download(op.media_name, out)
        console.print(f"[green]OK[/green] -> {escape(str(out))} ({out.stat().st_size:,} bytes)")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
