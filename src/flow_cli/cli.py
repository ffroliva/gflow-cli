"""CLI entry point — Click app exposing the v0.1 commands."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from flow_cli import __version__, profile_store
from flow_cli import auth as auth_mod
from flow_cli.cli_video import video as _video_group
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


def _render_profiles_table(profiles: list[profile_store.ProfileMeta]) -> None:
    """Pretty-print the profile inventory."""
    if not profiles:
        console.print("[yellow]No profiles found.[/yellow]")
        return
    root = auth_mod.default_profile_root()
    console.print(f"\n[bold]Profiles in[/bold] {root}\n")
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Default", justify="center")
    table.add_column("Name", style="bold")
    table.add_column("Session")
    table.add_column("Last used (UTC)")
    table.add_column("Profile dir", overflow="fold")
    for p in profiles:
        marker = "[bold green]●[/bold green]" if p.is_default else ""
        session = "[green]present[/green]" if p.cookies_present else "[red]missing[/red]"
        last = p.last_used_at.strftime("%Y-%m-%d %H:%M:%S") if p.last_used_at else "-"
        table.add_row(marker, p.name, session, last, str(p.profile_dir))
    console.print(table)
    console.print("\nUse [bold]gflow auth use <name>[/bold] to set the default profile.")
    console.print(
        "Use [bold]gflow auth login --profile <name>[/bold] to add or refresh a profile.\n"
    )


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


# --- auth -------------------------------------------------------------------


@main.group(invoke_without_command=True)
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Manage Google sessions for Flow.

    Bare `gflow auth` shows the profile inventory. If no profiles exist yet,
    it kicks off `gflow auth login` automatically.
    """
    if ctx.invoked_subcommand is not None:
        return
    profiles = profile_store.list_profiles()
    if not profiles:
        console.print("[yellow]No profiles found.[/yellow] Launching first-time login...\n")
        ctx.invoke(auth_login)
        return
    _render_profiles_table(profiles)


@auth.command("login")
@click.option(
    "--profile",
    default=None,
    help="Profile name. Defaults to the resolved default (env > config > auto).",
)
def auth_login(profile: str | None) -> None:
    """One-time interactive sign-in. Opens a browser window."""
    name = profile or _resolve_or_prompt(default_for_first_run="default")
    pdir = asyncio.run(auth_mod.login(name))
    console.print(f"[green]Session saved.[/green] Profile dir: {pdir}")
    # If this was the very first profile, set it as default automatically so
    # subsequent commands work without explicit --profile / FLOW_CLI_PROFILE.
    profiles = profile_store.list_profiles()
    if len(profiles) == 1:
        profile_store.set_default_profile(profiles[0].name)
        console.print(f"[dim]Set [bold]{profiles[0].name}[/bold] as default profile.[/dim]")


@auth.command("status")
@click.option("--profile", default=None)
def auth_status(profile: str | None) -> None:
    """Show whether a specific profile has a saved session."""
    name = profile or _resolve_or_exit()
    s = auth_mod.status(name)
    if s["exists"] and s["cookies_present"]:
        console.print(f"[green]Profile '{name}' is configured.[/green]")
    else:
        console.print(
            f"[yellow]Profile '{name}' has no session.[/yellow] "
            f"Run [bold]gflow auth login --profile {name}[/bold]."
        )
    for k, v in s.items():
        console.print(f"  {k}: {v}")


@auth.command("list")
def auth_list() -> None:
    """List every profile and indicate the current default."""
    profiles = profile_store.list_profiles()
    _render_profiles_table(profiles)


@auth.command("use")
@click.argument("name")
def auth_use(name: str) -> None:
    """Set NAME as the default profile."""
    try:
        cfg = profile_store.set_default_profile(name)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)
    console.print(
        f"[green]Default profile set to[/green] [bold]{name}[/bold]\n[dim]Persisted in {cfg}[/dim]"
    )


@auth.command("logout")
@click.option("--profile", default=None)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def auth_logout(profile: str | None, yes: bool) -> None:
    """Delete a profile's saved session (irreversible)."""
    name = profile or _resolve_or_exit()
    if not yes:
        click.confirm(
            f"Delete profile '{name}' and all cookies/state?",
            abort=True,
        )
    try:
        deleted = profile_store.delete_profile(name)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(2)
    console.print(f"[yellow]Profile '{name}' removed.[/yellow]\n[dim]Deleted dir: {deleted}[/dim]")


def _resolve_or_exit() -> str:
    """Resolve the active profile or print a friendly error and exit."""
    try:
        return profile_store.resolve_profile(None)
    except profile_store.NoProfilesError as e:
        console.print(f"[yellow]{e}[/yellow]")
        sys.exit(2)
    except profile_store.NoDefaultProfileError as e:
        console.print(f"[yellow]{e}[/yellow]")
        sys.exit(2)


def _resolve_or_prompt(default_for_first_run: str) -> str:
    """Like _resolve_or_exit but for `auth login` — accept any name to create."""
    try:
        return profile_store.resolve_profile(None)
    except profile_store.NoProfilesError:
        return default_for_first_run
    except profile_store.NoDefaultProfileError:
        return click.prompt(
            "Multiple profiles exist; pick a name to login or refresh",
            default=default_for_first_run,
        )


# --- generation commands ----------------------------------------------------


@main.command()
@click.argument("image", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--profile", default=None)
def upload(image: Path, profile: str | None) -> None:
    """Upload IMAGE to Flow library, print the asset UUID."""

    async def run() -> None:
        async with _make_provider(profile or _resolve_or_exit()) as p:
            asset = await p.upload_image(image)
            console.print(asset.uuid)

    asyncio.run(run())


@main.command()
@click.option("-s", "--start-uuid", required=True)
@click.option("-p", "--prompt", required=True)
@click.option("--aspect", default="9:16", show_default=True)
@click.option("--profile", default=None)
def generate(start_uuid: str, prompt: str, aspect: str, profile: str | None) -> None:
    """Kick off a Veo I2V generation. Prints the job_id."""

    async def run() -> None:
        from flow_cli.models import GenerationRequest

        async with _make_provider(profile or _resolve_or_exit()) as p:
            _ = start_uuid  # TODO(phase-3): wire start_uuid through GenerationRequest
            req = GenerationRequest(
                start_image=Path(""),  # TODO: refine when start_uuid wired
                motion_prompt=prompt,
                aspect=aspect,
            )
            job = await p.start_generation(req)
            console.print(job.job_id)

    asyncio.run(run())


@main.command()
@click.argument("job_id")
@click.option("--profile", default=None)
def status(job_id: str, profile: str | None) -> None:
    """Poll the status of a generation job."""

    async def run() -> None:
        async with _make_provider(profile or _resolve_or_exit()) as p:
            job = await p.get_job(job_id)
            console.print(f"{job.status.value} {job.output_url or ''}")

    asyncio.run(run())


@main.command()
@click.argument("job_id")
@click.option("-o", "--output", required=True, type=click.Path(path_type=Path))
@click.option("--profile", default=None)
def download(job_id: str, output: Path, profile: str | None) -> None:
    """Download the rendered mp4 from a SUCCEEDED job."""

    async def run() -> None:
        async with _make_provider(profile or _resolve_or_exit()) as p:
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
@click.option("--profile", default=None)
@click.option("--poll-interval", default=5, show_default=True)
def i2v(
    image: Path,
    prompt: str,
    output: Path,
    aspect: str,
    profile: str | None,
    poll_interval: int,
) -> None:
    """Convenience: upload + generate + poll + download in one shot."""
    from flow_cli.models import GenerationRequest, JobStatus

    async def run() -> None:
        async with _make_provider(profile or _resolve_or_exit()) as p:
            console.print(f"  Uploading {image.name}...")
            asset = await p.upload_image(image)
            console.print(f"  Asset uuid: {asset.uuid}")
            console.print("  Starting generation...")
            req = GenerationRequest(
                start_image=image,
                motion_prompt=prompt,
                aspect=aspect,
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


main.add_command(_video_group)


if __name__ == "__main__":
    main()
