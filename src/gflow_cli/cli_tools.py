"""`gflow tools` — discover and run prompt tools (Flow "Tools" analogue)."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from gflow_cli import json_output
from gflow_cli.tools.registry import get_tool, iter_tools
from gflow_cli.tools.runtime import apply_tool

console = Console()


@click.group()
def tools() -> None:
    """Discover and run prompt tools."""


@tools.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
def list_tools(as_json: bool) -> None:
    specs = iter_tools()
    if as_json:
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


@tools.command("show")
@click.argument("name")
def show_tool(name: str) -> None:
    spec = get_tool(name)
    console.print(f"[bold]{spec.title}[/bold] ({spec.name}) — {spec.category}")
    console.print(spec.description)
    if spec.requires_env:
        console.print(f"Requires env: {', '.join(spec.requires_env)}")
    if spec.config.domains:
        console.print("Styles: " + ", ".join(d.name for d in spec.config.domains))


@tools.command("run")
@click.argument("name")
@click.argument("text")
@click.option("--style", default=None, help="Domain style mode (see `tools show`).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def run_tool(name: str, text: str, style: str | None, as_json: bool) -> None:
    spec = get_tool(name)
    options = {"style": style} if style else {}
    result = apply_tool(spec, text, options)
    if as_json:
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
