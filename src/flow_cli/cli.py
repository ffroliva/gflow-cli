"""CLI entry point — Click app exposing the 6 v0.1 commands."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console

from flow_cli import __version__
from flow_cli import auth as auth_mod
from flow_cli.providers.flow import FlowProvider

console = Console()


def _make_provider(profile: str) -> FlowProvider:
    pdir = auth_mod.profile_dir(profile)
    if not pdir.exists():
        console.print(
            f"[red]No session for profile '{profile}'.[/red] "
            f"Run [bold]gflow auth login[/bold] first."
        )
        sys.exit(2)
    return FlowProvider(profile_dir=pdir)


@click.group()
@click.version_option(__version__, "-V", "--version")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging.")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """gflow — drive Google Flow Veo I2V from the terminal."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)


@main.group()
def auth() -> None:
    """Manage Google session for Flow."""


@auth.command("login")
@click.option("--profile", default="default", help="Profile name (for multi-account use).")
def auth_login(profile: str) -> None:
    """One-time interactive sign-in. Opens a browser window."""
    pdir = asyncio.run(auth_mod.login(profile))
    console.print(f"[green]Session saved.[/green] Profile dir: {pdir}")


@auth.command("status")
@click.option("--profile", default="default")
def auth_status(profile: str) -> None:
    """Show whether a profile has a saved session."""
    s = auth_mod.status(profile)
    if s["exists"] and s["cookies_present"]:
        console.print(f"[green]Profile '{profile}' is configured.[/green]")
    else:
        console.print(
            f"[yellow]Profile '{profile}' has no session.[/yellow] "
            f"Run [bold]gflow auth login[/bold]."
        )
    for k, v in s.items():
        console.print(f"  {k}: {v}")


@main.command()
@click.argument("image", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--profile", default="default")
def upload(image: Path, profile: str) -> None:
    """Upload IMAGE to Flow library, print the asset UUID."""
    async def run() -> None:
        async with _make_provider(profile) as p:
            asset = await p.upload_image(image)
            console.print(asset.uuid)
    asyncio.run(run())


@main.command()
@click.option("-s", "--start-uuid", required=True, help="Start frame asset UUID.")
@click.option("-p", "--prompt", required=True, help="Motion prompt.")
@click.option("--aspect", default="9:16", show_default=True)
@click.option("--profile", default="default")
def generate(start_uuid: str, prompt: str, aspect: str, profile: str) -> None:
    """Kick off a Veo I2V generation. Prints the job_id."""
    async def run() -> None:
        from flow_cli.models import GenerationRequest
        async with _make_provider(profile) as p:
            req = GenerationRequest(
                start_image=Path(""),  # not used when start_uuid is set; TODO refine
                motion_prompt=prompt,
                aspect=aspect,
            )
            # TODO: wire start_uuid through GenerationRequest
            del start_uuid
            job = await p.start_generation(req)
            console.print(job.job_id)
    asyncio.run(run())


@main.command()
@click.argument("job_id")
@click.option("--profile", default="default")
def status(job_id: str, profile: str) -> None:
    """Poll the status of a generation job."""
    async def run() -> None:
        async with _make_provider(profile) as p:
            job = await p.get_job(job_id)
            console.print(f"{job.status.value} {job.output_url or ''}")
    asyncio.run(run())


@main.command()
@click.argument("job_id")
@click.option("-o", "--output", required=True, type=click.Path(path_type=Path))
@click.option("--profile", default="default")
def download(job_id: str, output: Path, profile: str) -> None:
    """Download the rendered mp4 from a SUCCEEDED job."""
    async def run() -> None:
        async with _make_provider(profile) as p:
            job = await p.get_job(job_id)
            if not job.output_url:
                console.print(f"[red]Job {job_id} has no output_url (status={job.status}).[/red]")
                sys.exit(1)
            out = await p.download(job.output_url, output)
            console.print(f"[green]Saved[/green] {out}")
    asyncio.run(run())


@main.command()
@click.argument("image", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("prompt")
@click.option("-o", "--output", required=True, type=click.Path(path_type=Path))
@click.option("--aspect", default="9:16", show_default=True)
@click.option("--profile", default="default")
@click.option("--poll-interval", default=5, show_default=True, help="Seconds between status polls.")
def i2v(image: Path, prompt: str, output: Path, aspect: str, profile: str, poll_interval: int) -> None:
    """Convenience: upload + generate + poll + download in one shot."""
    from flow_cli.models import GenerationRequest, JobStatus

    async def run() -> None:
        async with _make_provider(profile) as p:
            console.print(f"  Uploading {image.name}...")
            asset = await p.upload_image(image)
            console.print(f"  Asset uuid: {asset.uuid}")

            console.print("  Starting generation...")
            req = GenerationRequest(
                start_image=image, motion_prompt=prompt, aspect=aspect,
            )
            job = await p.start_generation(req)
            console.print(f"  Job id: {job.job_id}")

            while job.status not in (JobStatus.SUCCEEDED, JobStatus.FAILED):
                await asyncio.sleep(poll_interval)
                job = await p.get_job(job.job_id)
                console.print(f"  {job.status.value}...")

            if job.status == JobStatus.FAILED:
                console.print(f"[red]Generation failed:[/red] {job.error}")
                sys.exit(1)

            assert job.output_url
            await p.download(job.output_url, output)
            console.print(f"[green]Saved[/green] {output}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
