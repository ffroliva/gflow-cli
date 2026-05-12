"""CLI entry point — Click app exposing the gflow commands."""

from __future__ import annotations

import asyncio
import sys
import uuid

import click
import structlog
from rich.console import Console
from rich.table import Table

from gflow_cli import __version__, profile_store
from gflow_cli import auth as auth_mod
from gflow_cli.cli_image import image as _image_group
from gflow_cli.cli_run import run as _run_command
from gflow_cli.cli_video import video as _video_group
from gflow_cli.config import get_settings
from gflow_cli.observability import DEBUG_LEVEL, configure_logging

console = Console()


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
    # Process-boundary bootstrap. Order matters:
    # 1. configure_logging() installs structlog processors (TTY-aware renderer,
    #    show_locals=False exception formatter, etc.).
    # 2. bind_contextvars() attaches process-scoped fields that flow through
    #    every event emitted in this invocation. We bind these ONLY here —
    #    binding inside async tasks risks cross-task leakage (spec C6).
    settings = get_settings()
    configure_logging(settings.log_format)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        cli_version=__version__,
        correlation_id=str(uuid.uuid4()),
    )
    if verbose:
        # Lower the structlog filter to DEBUG. We DO NOT call
        # `logging.basicConfig` — structlog owns logging in v0.4+. The
        # `DEBUG_LEVEL` constant is defined in `observability.py` so this
        # module doesn't need to `import logging` solely for one constant.
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(DEBUG_LEVEL),
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
    # subsequent commands work without explicit --profile / GFLOW_CLI_PROFILE.
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


main.add_command(_video_group)
main.add_command(_image_group)
main.add_command(_run_command)


if __name__ == "__main__":
    main()
