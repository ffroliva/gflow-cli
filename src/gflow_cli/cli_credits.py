"""`gflow credits` commands — inspect one or all saved Flow accounts."""

from __future__ import annotations

from typing import Any

import click
from rich.console import Console
from rich.table import Table

from gflow_cli import json_output
from gflow_cli._cli_helpers import _make_provider_dir, _resolve_profile, run_with_handlers
from gflow_cli.services.credits import inspect_all_profiles, inspect_profile

console = Console()


def _render_one(item: dict[str, Any]) -> None:
    console.print(f"[bold]Profile:[/bold] {item['profile']}")
    console.print(f"[bold]Google account:[/bold] {item.get('email') or 'unknown'}")
    console.print(f"[bold green]Credits:[/bold green] {item['credits']}")
    if item.get("sku"):
        console.print(f"[bold]SKU:[/bold] {item['sku']}")


def _render_many(payload: dict[str, Any]) -> None:
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Default", justify="center")
    table.add_column("Profile", style="bold")
    table.add_column("Google account")
    table.add_column("Credits", justify="right")
    table.add_column("SKU")
    table.add_column("Status")
    for item in payload["profiles"]:
        table.add_row(
            "*" if item["is_default"] else "",
            item["profile"],
            item.get("email") or "unknown",
            str(item["credits"]) if item["authenticated"] else "-",
            item.get("sku") or "-",
            "ok" if item["authenticated"] else item.get("error", "error"),
        )
    console.print(table)
    console.print(f"[bold]Total credits:[/bold] {payload['total_credits']}")


async def _show_one(profile: str | None, as_json: bool) -> None:
    payload = await inspect_profile(profile)
    json_output.emit(payload) if as_json else _render_one(payload)


async def _show_all(as_json: bool) -> None:
    payload = await inspect_all_profiles()
    json_output.emit(payload) if as_json else _render_many(payload)


@click.group(invoke_without_command=True)
@click.option("--profile", default=None, help="Profile name.")
@click.option("--all", "all_profiles", is_flag=True, help="Inspect every saved profile.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON output.")
@click.pass_context
def credits(ctx: click.Context, profile: str | None, all_profiles: bool, as_json: bool) -> None:
    """Show current Google Flow credit balances."""

    if ctx.invoked_subcommand is not None:
        return
    if all_profiles:
        run_with_handlers(lambda: _show_all(as_json), cli_command="credits", as_json=as_json)
        return
    resolved = _resolve_profile(profile)
    _make_provider_dir(resolved)
    run_with_handlers(lambda: _show_one(resolved, as_json), cli_command="credits", as_json=as_json)


@credits.command("user")
@click.argument("profile_name", required=False)
@click.option("--profile", "profile_option", default=None, help="Profile name.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON output.")
def user(profile_name: str | None, profile_option: str | None, as_json: bool) -> None:
    """Show the credit balance for one profile."""

    if profile_name and profile_option and profile_name != profile_option:
        raise click.UsageError("PROFILE and --profile name different profiles")
    selected = _resolve_profile(profile_option or profile_name)
    _make_provider_dir(selected)
    run_with_handlers(
        lambda: _show_one(selected, as_json), cli_command="credits user", as_json=as_json
    )


@credits.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON output.")
def list_command(as_json: bool) -> None:
    """Show credit balances for all saved profiles."""

    run_with_handlers(lambda: _show_all(as_json), cli_command="credits list", as_json=as_json)
