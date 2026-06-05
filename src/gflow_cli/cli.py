"""CLI entry point — Click app exposing the gflow commands."""

from __future__ import annotations

import asyncio
import re
import sys
import uuid

import click
import structlog
from rich.console import Console
from rich.table import Table

from gflow_cli import __version__, profile_store
from gflow_cli import auth as auth_mod
from gflow_cli.cli_character import character as _character_group
from gflow_cli.cli_data import data as _data_group
from gflow_cli.cli_image import image as _image_group
from gflow_cli.cli_models import models as _models_command
from gflow_cli.cli_movie import movie as _movie_group
from gflow_cli.cli_run import run as _run_command
from gflow_cli.cli_scene import scene as _scene_group
from gflow_cli.cli_video import video as _video_group
from gflow_cli.config import get_settings
from gflow_cli.observability import DEBUG_LEVEL, configure_logging

console = Console()


def _default_marker_glyph(encoding: str | None) -> str:
    """Return a default-profile marker glyph safe to render on `encoding`.

    Falls back to ASCII ``*`` when the console codec cannot encode ``●``
    (e.g. Windows ``cp1252`` PowerShell / cmd default). See issue #82.
    """
    if not encoding:
        return "*"
    try:
        "●".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return "*"
    return "●"


def _profile_name_from_account(account: str) -> str | None:
    """Derive a filesystem-safe profile name from a Google account email.

    Takes the local-part (before ``@``), replaces every run of characters
    outside ``[A-Za-z0-9_-]`` with a single ``-``, and trims leading/trailing
    ``-``. Returns ``None`` when nothing usable remains so the caller keeps the
    existing name. The result always satisfies
    ``profile_store._SAFE_PROFILE_NAME_RE``; without this, dotted/aliased emails
    (``flavio.oliva@``, ``user+flow@``) would make ``rename_profile`` raise.
    """
    local_part = account.split("@", 1)[0]
    slug = re.sub(r"[^\w\-]+", "-", local_part).strip("-")[:128]
    return slug or None


def _render_profiles_table(profiles: list[profile_store.ProfileMeta]) -> None:
    """Pretty-print the profile inventory."""
    if not profiles:
        console.print("[yellow]No profiles found.[/yellow]")
        return
    root = auth_mod.default_profile_root()
    console.print(f"\n[bold]Profiles in[/bold] {root}\n")
    marker_glyph = _default_marker_glyph(getattr(sys.stdout, "encoding", None))
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Default", justify="center")
    table.add_column("Name", style="bold")
    table.add_column("Google account")
    table.add_column("Session")
    table.add_column("Last used (UTC)")
    table.add_column("Profile dir", overflow="fold")
    for p in profiles:
        marker = f"[bold green]{marker_glyph}[/bold green]" if p.is_default else ""
        account = p.google_account or "unknown"
        session = "[green]present[/green]" if p.cookies_present else "[red]missing[/red]"
        last = p.last_used_at.strftime("%Y-%m-%d %H:%M:%S") if p.last_used_at else "-"
        table.add_row(marker, p.name, account, session, last, str(p.profile_dir))
    console.print(table)
    console.print("\nUse [bold]gflow auth use <name>[/bold] to set the default profile.")
    console.print(
        "Use [bold]gflow auth login --profile <name>[/bold] to add or refresh a profile.\n",
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
@click.option(
    "--browser",
    default=None,
    type=click.Choice(["auto", "chrome", "internal"], case_sensitive=False),
    help="Browser strategy for login. 'chrome' bypasses Google secure blocks.",
    envvar="GFLOW_CLI_AUTH_BROWSER",
)
def auth_login(profile: str | None, browser: str | None) -> None:
    """One-time interactive sign-in. Opens a browser window."""
    from gflow_cli.browser_manager import is_chrome_available
    from gflow_cli.errors import EXIT_CODE_MAP, GFlowError

    name = profile or _resolve_or_prompt(default_for_first_run="default")
    # Resolve browser strategy: CLI > Env > auto
    selected_browser = browser or "auto"

    # Announce the launch strategy (peek at Chrome availability for auto mode)
    if selected_browser == "internal":
        console.print("Launching internal Chromium...")
    elif selected_browser == "chrome":
        console.print("Launching real Chrome...")
    else:  # auto
        console.print(
            "Launching real Chrome..."
            if is_chrome_available()
            else "Launching internal Chromium...",
        )

    try:
        pdir = asyncio.run(auth_mod.login(name, browser=selected_browser))
    except GFlowError as e:
        console.print(f"[red]{e}[/red]")
        if e.remediation_hint:
            console.print(f"[dim]{e.remediation_hint}[/dim]")
        exit_code = next(
            (code for cls, code in EXIT_CODE_MAP.items() if isinstance(e, cls)),
            1,
        )
        sys.exit(exit_code)
    except Exception as e:
        console.print(f"[red]Unexpected error during login: {e}[/red]")
        sys.exit(1)
    console.print(f"[green]Session saved.[/green] Profile dir: {pdir}")

    # Auto-rename an opaque "default" profile to the email local-part on first
    # login so the profile inventory is immediately human-readable. Only fires
    # when no explicit --profile was given (profile is None), so `gflow auth
    # login --profile default` never silently renames the user's named profile.
    profiles = profile_store.list_profiles()
    if (
        profile is None
        and len(profiles) == 1
        and profiles[0].name == "default"
        and profiles[0].google_account
    ):
        local_part = _profile_name_from_account(profiles[0].google_account)
        if local_part and local_part != "default":
            try:
                pdir = profile_store.rename_profile("default", local_part)
                profiles = profile_store.list_profiles()
                console.print(
                    f"[dim]Renamed profile from [bold]default[/bold] to "
                    f"[bold]{local_part}[/bold] (derived from Google account).[/dim]",
                )
            except FileExistsError:
                console.print(
                    f"[dim]Profile [bold]{local_part}[/bold] already exists — "
                    "keeping name [bold]default[/bold].[/dim]",
                )

    # If this was the very first profile, set it as default automatically so
    # subsequent commands work without explicit --profile / GFLOW_CLI_PROFILE.
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
            f"Run [bold]gflow auth login --profile {name}[/bold].",
        )
    for k, v in s.items():
        console.print(f"  {k}: {v}")


@auth.command("list")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON instead of a table.")
def auth_list(as_json: bool) -> None:
    """List every profile and indicate the current default."""
    profiles = profile_store.list_profiles()
    if as_json:
        import json

        click.echo(
            json.dumps(
                [
                    {
                        "name": p.name,
                        "google_account": p.google_account,
                        "is_default": p.is_default,
                        "cookies_present": p.cookies_present,
                        "last_used_at": p.last_used_at.isoformat() if p.last_used_at else None,
                        "profile_dir": str(p.profile_dir),
                    }
                    for p in profiles
                ],
            ),
        )
        return
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
        f"[green]Default profile set to[/green] [bold]{name}[/bold]\n[dim]Persisted in {cfg}[/dim]",
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


main.add_command(_character_group)
main.add_command(_data_group)
main.add_command(_movie_group)
main.add_command(_video_group)
main.add_command(_image_group)
main.add_command(_run_command)
main.add_command(_models_command)
main.add_command(_scene_group)


if __name__ == "__main__":
    main()
