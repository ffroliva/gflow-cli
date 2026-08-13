"""CLI entry point — Click app exposing the gflow commands."""

from __future__ import annotations

import asyncio
import re
import sys
import uuid

import click
import structlog
from pydantic import ValidationError as PydanticValidationError
from rich.console import Console
from rich.table import Table

from gflow_cli import __version__, profile_store
from gflow_cli import auth as auth_mod
from gflow_cli.cli_character import character as _character_group
from gflow_cli.cli_data import data as _data_group
from gflow_cli.cli_image import image as _image_group
from gflow_cli.cli_instructions import instructions as _instructions_group
from gflow_cli.cli_models import models as _models_command
from gflow_cli.cli_movie import movie as _movie_group
from gflow_cli.cli_project import project as _project_group
from gflow_cli.cli_run import run as _run_command
from gflow_cli.cli_scene import scene as _scene_group
from gflow_cli.cli_tools import tools as _tools_group
from gflow_cli.cli_video import video as _video_group
from gflow_cli.config import get_settings, warn_if_removed_gemini_key_set
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
    try:
        settings = get_settings()
    except PydanticValidationError as exc:
        # A bad GFLOW_CLI_* value (e.g. an unknown browser_engine / provider /
        # log_level) must fail with a clear config error (exit 11), not a raw
        # pydantic traceback. Logging is not configured yet, so render directly.
        from gflow_cli.errors import ConfigurationError

        first = exc.errors()[0]
        field = ".".join(str(p) for p in first.get("loc", ())) or "(unknown)"
        console.print(
            f"[red]{ConfigurationError.title}:[/red] "
            f"GFLOW_CLI_{field.upper()} — {first.get('msg', 'invalid value')}"
        )
        console.print("[dim]Set a valid value, or unset it to use the default.[/dim]")
        sys.exit(11)
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
    # GFLOW_CLI_GEMINI_API_KEY was removed in v0.46.0 and is not forwarded. The
    # prompt tools never raise, so an unmigrated user would otherwise see no
    # error at all — just silently un-rewritten prompts on full-price
    # generations. config.py also emits a DeprecationWarning for library/MCP
    # consumers; this stderr line is what a CLI user actually sees, since
    # DeprecationWarning is hidden by default and structlog may be JSON-piped.
    if warn_if_removed_gemini_key_set():
        click.secho(
            "warning: GFLOW_CLI_GEMINI_API_KEY is no longer read. Set "
            "GFLOW_CLI_LLM_API_KEY instead (your existing key still works "
            "against the default endpoint) — until then --tool leaves prompts "
            "unchanged. See docs/CONFIGURATION.md.",
            err=True,
            fg="yellow",
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


def _maybe_rename_first_profile(
    profile: str | None,
    profiles: list[profile_store.ProfileMeta],
) -> list[profile_store.ProfileMeta]:
    """Auto-rename an opaque 'default' profile to the email local-part on first login.

    Only fires when no explicit ``--profile`` was given, there is exactly one
    profile, it is still named ``"default"``, and a google_account is present.
    Returns the (possibly refreshed) profiles list.
    """
    if not (
        profile is None
        and len(profiles) == 1
        and profiles[0].name == "default"
        and profiles[0].google_account
    ):
        return profiles
    local_part = _profile_name_from_account(profiles[0].google_account)
    if not local_part or local_part == "default":
        return profiles
    try:
        profile_store.rename_profile("default", local_part)
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
    return profiles


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
    profiles = _maybe_rename_first_profile(profile, profiles)

    # If this was the very first profile, set it as default automatically so
    # subsequent commands work without explicit --profile / GFLOW_CLI_PROFILE.
    if len(profiles) == 1:
        profile_store.set_default_profile(profiles[0].name)
        console.print(f"[dim]Set [bold]{profiles[0].name}[/bold] as default profile.[/dim]")


@auth.command("status")
@click.option("--profile", default=None)
def auth_status(profile: str | None) -> None:
    """Show whether a profile has a saved session and verify it against Flow.

    Probes the Flow session endpoint with the profile's cookies (no browser,
    no credits). Exits 0 when the session is verified, 1 otherwise.
    """
    name = profile or _resolve_or_exit()
    s = auth_mod.status(name)
    for k, v in s.items():
        console.print(f"  {k}: {v}")
    # Surface the active browser engine so a two-engine setup is debuggable.
    from gflow_cli.config import get_settings

    console.print(f"  browser_engine: {get_settings().browser_engine}")

    if not (s["exists"] and s["cookies_present"]):
        console.print(
            f"[yellow]Profile '{name}' has no session.[/yellow] "
            f"Run [bold]gflow auth login --profile {name}[/bold].",
        )
        sys.exit(1)

    # Files on disk say nothing about whether the session still works — prove
    # it (issue #471). Fail-closed: only a verified session exits 0.
    from rich.markup import escape

    from gflow_cli.auth import verification

    console.print("[dim]Probing Flow session (may take up to ~45s on a slow network)...[/dim]")
    status_result = asyncio.run(
        verification.verify_flow_profile(auth_mod.profile_dir(name), source="status")
    )
    if not status_result.authenticated:
        if status_result.outcome is verification.FlowSessionOutcome.VERIFICATION_ERROR:
            # Re-login cannot fix an unreachable endpoint — don't send the
            # user into an interactive browser flow for a network problem.
            console.print(
                f"[yellow]{status_result.detail}[/yellow] "
                "Check network connectivity and retry; re-login is only needed "
                "if the session is actually dead.",
            )
        else:
            console.print(
                f"[yellow]{status_result.detail}[/yellow] "
                f"Run [bold]gflow auth login --profile {name}[/bold] to refresh the session.",
            )
        sys.exit(1)
    who = f" as [bold]{escape(status_result.user_email)}[/bold]" if status_result.user_email else ""
    console.print(f"[green]Flow session verified{who}.[/green]")


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
main.add_command(_project_group)
main.add_command(_video_group)
main.add_command(_image_group)
main.add_command(_instructions_group)
main.add_command(_run_command)
main.add_command(_models_command)
main.add_command(_scene_group)
main.add_command(_tools_group)


# --- mcp --------------------------------------------------------------------


@main.group()
def mcp() -> None:
    """Model Context Protocol server for IDE/agent integration."""


@mcp.command("run")
@click.option(
    "--profile",
    default=None,
    envvar="GFLOW_CLI_PROFILE",
    help=(
        "Profile to use for generation tools. "
        "Defaults to the profile set as default (gflow auth use <name>). "
        "Can also be set via the GFLOW_CLI_PROFILE environment variable."
    ),
)
def mcp_run(profile: str | None) -> None:
    """Start the MCP server over stdio transport.

    Use this with Claude Desktop, Cursor, or other MCP-aware clients.
    The server communicates via stdin/stdout JSON-RPC.

    The server auto-selects your default profile (set via `gflow auth use`).
    Pass --profile or set GFLOW_CLI_PROFILE to pin a specific profile.

    \b
    Configuration example (claude_desktop_config.json):
      {
        "mcpServers": {
          "gflow": {
            "command": "gflow",
            "args": ["mcp", "run"]
          }
        }
      }
    """
    import os

    from gflow_cli.mcp.server import main_stdio

    # Pin the profile for all tool calls in this server process.
    # _resolve_and_validate_profile() in tools.py reads GFLOW_CLI_PROFILE.
    if profile:
        os.environ["GFLOW_CLI_PROFILE"] = profile
        sys.stderr.write(f"[gflow] MCP server using profile: {profile}\n")

    main_stdio()


@mcp.command("setup")
@click.option(
    "--target",
    type=click.Choice(["claude-desktop", "cursor", "vscode"]),
    default="claude-desktop",
    help="Target IDE/agent to configure.",
)
def mcp_setup(target: str) -> None:
    """Auto-configure the gflow MCP server for a supported IDE/agent.

    Merges the server entry into the target's config file (existing content
    is preserved; a pre-existing file is backed up as <name>.gflow-backup).
    """
    from gflow_cli.errors import ConfigurationError
    from gflow_cli.mcp import setup as setup_mod

    try:
        path, changed = setup_mod.apply(target)
    except ConfigurationError as exc:
        console.print(f"[red]{ConfigurationError.title}:[/red] {exc.detail or exc}")
        console.print(
            "[dim]Fix (or move away) the existing config file and re-run "
            "gflow mcp setup — it never overwrites a file it cannot parse.[/dim]"
        )
        sys.exit(11)
    except OSError as exc:
        # Read-only/locked config file, permission problems, disk errors.
        console.print(f"[red]Could not write the client config:[/red] {type(exc).__name__}: {exc}")
        sys.exit(11)
    if changed:
        console.print(f"[green]gflow MCP server configured for {target}.[/green]")
        console.print(f"  config: {path}")
        console.print(f"[dim]Restart {target} to load the server.[/dim]")
    else:
        console.print(
            f"[green]Already configured[/green] — an existing gflow entry in {path} "
            "was left untouched."
        )


# --- serve ------------------------------------------------------------------


@main.command()
@click.option("--port", default=8000, show_default=True, help="Port to bind the daemon to.")
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind. Use 0.0.0.0 with caution (requires GFLOW_DAEMON_TOKEN).",
)
@click.option("--profile", default=None, help="Profile for the background worker.")
@click.option(
    "--transport",
    type=click.Choice(["http", "sse"]),
    default="http",
    show_default=True,
    help="MCP HTTP transport. 'sse' is deprecated by the MCP 2026-07-28 spec.",
)
def serve(port: int, host: str, profile: str | None, transport: str) -> None:
    """Start the gflow MCP server over HTTP.

    \b
    Foundation for Gflow Studio and external API consumers:
      • MCP Streamable HTTP at /mcp — the current spec transport (default)
      • MCP-SSE at /sse — DEPRECATED (--transport sse), one cycle only
      • REST /api/v1/* — CRUD + generation queue (planned)
      • Background FlowWorker — sequential generation (planned)

    \b
    Example:
      gflow serve --port 8000
      gflow serve --transport sse --port 8000   # deprecated transport
      gflow serve --host 0.0.0.0 --port 8000  # requires GFLOW_DAEMON_TOKEN
    """
    if host != "127.0.0.1":
        token = get_settings().daemon_token if hasattr(get_settings(), "daemon_token") else None
        if not token:
            console.print(
                "[red]Error:[/red] Binding to a non-localhost address requires "
                "[bold]GFLOW_DAEMON_TOKEN[/bold] to be set.\n"
                "[dim]Set it in .env (CWD or $GFLOW_CLI_HOME) or as an environment variable.[/dim]"
            )
            sys.exit(11)

    if transport == "sse":
        console.print(
            f"\n[bold]🎬 gflow daemon[/bold] starting on [cyan]{host}:{port}[/cyan]\n"
            f"  MCP-SSE: [cyan]http://{host}:{port}/sse[/cyan]\n"
            "[yellow]Warning:[/yellow] HTTP+SSE is deprecated by the MCP 2026-07-28 spec.\n"
            "[dim]Drop --transport sse to serve Streamable HTTP at /mcp instead.[/dim]\n"
        )

        from gflow_cli.mcp.server import main_sse

        main_sse(host=host, port=port)
        return

    from gflow_cli.mcp.server import HTTP_PATH, main_http

    console.print(
        f"\n[bold]🎬 gflow daemon[/bold] starting on [cyan]{host}:{port}[/cyan]\n"
        f"  MCP (Streamable HTTP): [cyan]http://{host}:{port}{HTTP_PATH}[/cyan]\n"
    )

    main_http(host=host, port=port)


if __name__ == "__main__":
    main()
