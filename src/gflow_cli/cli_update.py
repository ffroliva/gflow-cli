"""``gflow update`` — upgrade gflow-cli in place through the installer that
put it here (``uv tool`` / ``pipx`` / the venv's own ``pip``).

Rendering only: the decisions live in :func:`gflow_cli.update_check.run_update`.
Exit contract: 0 (upgraded, already current, or ``--check``), 11
(:class:`~gflow_cli.errors.ConfigurationError`: not an index install, manager
missing from PATH, or the manager failed).
"""

from __future__ import annotations

import click

from gflow_cli import json_output
from gflow_cli._cli_helpers import run_with_handlers

# Bound in THIS module's namespace so tests can monkeypatch
# ``gflow_cli.cli_update.run_update`` and never spawn a package manager.
from gflow_cli.update_check import UpdateReport, run_update


@click.command("update")
@click.option(
    "--check",
    is_flag=True,
    help="Only report the installed and latest versions; change nothing.",
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON report.")
def update(check: bool, as_json: bool) -> None:
    """Upgrade gflow-cli to the latest PyPI release.

    Runs the package manager that installed gflow-cli (uv tool, pipx, or the
    venv's own pip) and shows its output. Source / editable installs are
    refused (exit 11) — update those the way they were installed.
    """
    run_with_handlers(lambda: _run(check, as_json), cli_command="update", as_json=as_json)


async def _run(check: bool, as_json: bool) -> None:
    report = run_update(check=check)
    if as_json:
        json_output.emit(
            {
                "installed": report.installed,
                "latest": report.latest,
                "update_available": report.update_available,
                "installer": report.installer,
                "command": list(report.command),
                "upgraded": report.upgraded,
                "notes": list(report.notes),
            }
        )
        return
    for line in _render(report):
        click.echo(line)


def _render(report: UpdateReport) -> list[str]:
    latest = report.latest or "unknown (PyPI unreachable)"
    shown = " ".join(report.command)
    if report.upgraded:
        return [
            f"Upgraded gflow-cli {report.installed} -> {latest} via {report.installer}.",
            "Restart any running `gflow serve` / MCP server to pick it up.",
            *report.notes,
        ]
    if report.update_available is False:
        return [f"gflow-cli {report.installed} is up to date."]
    lines = [
        f"gflow-cli {report.installed} installed; latest on PyPI: {latest}.",
        f"Installer: {report.installer} -> `{shown}`",
    ]
    if report.update_available:
        lines.append("Run `gflow update` to upgrade.")
    return lines
