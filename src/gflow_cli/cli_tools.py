"""`gflow tools` — discover and run prompt tools (Flow "Tools" analogue)."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from gflow_cli import json_output
from gflow_cli._cli_helpers import OutputFormat, output_format_option, spends_credits
from gflow_cli.tools.registry import get_tool, iter_tools
from gflow_cli.tools.runtime import apply_tool

console = Console()


@click.group()
def tools() -> None:
    """Discover and run prompt tools."""


@spends_credits(False)
@tools.command("list")
@output_format_option(help="Output format (json|table|id).")
def list_tools(as_json: bool, output_format: str = "table") -> None:
    """List the prompt tools available on this install."""
    specs = iter_tools()
    if output_format == OutputFormat.ID.value:
        for s in specs:
            click.echo(s.name)
        return
    if as_json or output_format == OutputFormat.JSON.value:
        json_output.emit(
            {
                "tools": [
                    {
                        "name": s.name,
                        "title": s.title,
                        "description": s.description,
                        "category": s.category,
                        "requires_env": list(s.requires_env),
                    }
                    for s in specs
                ]
            }
        )
        return
    table = Table(title="Tools")
    for col in ("Name", "Title", "Category", "Description"):
        table.add_column(col)
    for s in specs:
        table.add_row(s.name, s.title, s.category, s.description)
    console.print(table)


@spends_credits(False)
@tools.command("show")
@click.argument("name")
def show_tool(name: str) -> None:
    """Show one prompt tool: what it does and what it needs."""
    spec = get_tool(name)
    console.print(f"[bold]{spec.title}[/bold] ({spec.name}) — {spec.category}")
    console.print(spec.description)
    if spec.requires_env:
        console.print(f"Requires env: {', '.join(spec.requires_env)}")
    if spec.config.domains:
        # A style name can exist for both image and video (e.g. "product"); show
        # the category in parentheses so the two are distinguishable.
        console.print(
            "Styles: "
            + ", ".join(
                d.name if d.category == "both" else f"{d.name} ({d.category})"
                for d in spec.config.domains
            )
        )


@spends_credits(False)
@tools.command("run")
@click.argument("name")
@click.argument("text")
@click.option("--style", default=None, help="Domain style mode (see `tools show`).")
@output_format_option(allow_id=False, help="Output format (json|table).")
def run_tool(
    name: str,
    text: str,
    style: str | None,
    as_json: bool,
    output_format: str = "table",
) -> None:
    """Run a prompt tool on TEXT and print the rewritten prompt."""
    spec = get_tool(name)
    options = {"style": style} if style else {}
    result = apply_tool(spec, text, options)
    if as_json or output_format == OutputFormat.JSON.value:
        json_output.emit(
            {
                "name": spec.name,
                "original": result.original,
                "expanded": result.expanded,
                "was_expanded": result.was_expanded,
            }
        )
        return
    console.print(result.expanded)
