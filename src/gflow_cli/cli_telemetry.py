"""``gflow telemetry`` — inspect local usage counters (#1164).

Local only: no network calls, no upload, no notice in this release.
"""

from __future__ import annotations

import json

import click

from gflow_cli import telemetry as telemetry_mod
from gflow_cli.config import _resolve_home  # pyright: ignore[reportPrivateUsage]


@click.group("telemetry")
def telemetry() -> None:
    """Inspect local usage counters and telemetry status."""


@telemetry.command("status")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON status.")
def status_cmd(as_json: bool) -> None:
    """Show what is counted locally, where, and the kill switch in force."""
    kill_switch = telemetry_mod.killed()
    if kill_switch is not None:
        state = "off"
        switch = kill_switch
    elif telemetry_mod.opted_out():
        state = "off"
        switch = "telemetry.off"
    else:
        state = "on"
        switch = None

    if as_json:
        payload = {
            "state": state,
            "switch": switch,
            "report": telemetry_mod.build_report(telemetry_mod.iso_week()),
        }
        click.echo(json.dumps(payload, indent=2))
        return

    home = _resolve_home()
    db_path = home / "telemetry.db"
    click.echo(f"Storage: {db_path}")
    click.echo(
        "Counters: Commands, flags, exits, durations, transitions, models, "
        "modes, aspects, outcomes, prompt tools, crash fingerprints."
    )
    click.echo(f"Switch in force: {switch or 'none'}")
    click.echo("Nothing is uploaded: this version keeps usage counters on this machine only.")


@telemetry.command("show")
def show_cmd() -> None:
    """Print the weekly usage report for the current ISO week as JSON."""
    report = telemetry_mod.build_report(telemetry_mod.iso_week())
    click.echo(json.dumps(report, indent=2))
