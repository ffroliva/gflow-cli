"""``gflow telemetry`` — inspect local usage counters (#1164).

Local counters and opt-out controls for weekly usage uploads.
"""

from __future__ import annotations

import json
import sys

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
    state, switch = telemetry_mod.state()
    upload_reason = telemetry_mod.upload_disabled_reason()
    upload = {"enabled": upload_reason is None, "reason": upload_reason}

    if as_json:
        payload = {
            "state": state,
            "switch": switch,
            "upload": upload,
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
    click.echo(f"Upload: {'on' if upload_reason is None else f'off ({upload_reason})'}")


@telemetry.command("on")
def on_cmd() -> None:
    """Allow weekly usage uploads after the interactive usage notice."""
    from gflow_cli.errors import ConfigurationError

    try:
        telemetry_mod.set_opted_out(False)
    except ConfigurationError as exc:
        click.echo(f"{ConfigurationError.title}: {exc.detail or exc}", err=True)
        sys.exit(11)
    if switch := telemetry_mod.killed():
        click.echo(f"Telemetry is still off because {switch} is set.", err=True)
    if not telemetry_mod.notice_seen():
        click.echo(
            "Weekly upload starts only after the usage notice has been shown in an interactive "
            "terminal.",
            err=True,
        )


@telemetry.command("off")
@click.option("--delete", is_flag=True, help="Also delete local usage data.")
def off_cmd(delete: bool) -> None:
    """Stop local usage counting and weekly uploads on this machine."""
    from gflow_cli.errors import ConfigurationError

    try:
        telemetry_mod.set_opted_out(True)
    except ConfigurationError as exc:
        click.echo(f"{ConfigurationError.title}: {exc.detail or exc}", err=True)
        sys.exit(11)
    click.echo(
        "Usage telemetry is off. Nothing will be counted or sent from this machine.", err=True
    )
    if delete:
        home = _resolve_home()
        path = home / "telemetry.db"
        try:
            for path in (
                home / "telemetry.db",
                home / "telemetry.db-wal",
                home / "telemetry.db-shm",
            ):
                path.unlink(missing_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                detail=f"Could not delete local usage data at {path}: {exc}"
            ) from exc
        click.echo(
            "Local usage data deleted. The server holds no per-install data, so there is nothing "
            "to delete remotely.",
            err=True,
        )


@telemetry.command("show")
def show_cmd() -> None:
    """Print the weekly usage report for the current ISO week as JSON."""
    report = telemetry_mod.build_report(telemetry_mod.iso_week())
    click.echo(json.dumps(report, indent=2))
