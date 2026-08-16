"""``gflow doctor`` — read-only diagnostics over the catalog, DB, and env (#542).

Rendering only: every finding string arrives pre-redacted from
:mod:`gflow_cli.services.doctor` (UUIDs not display names, safe paths,
control chars stripped) and is echoed VERBATIM — nothing here re-composes
text from raw DB values. Exit contract: 0 clean, 33 on any warn/fail
finding, 16 on :class:`~gflow_cli.errors.DataStoreError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from gflow_cli import json_output
from gflow_cli.cli_data import _db_path, _guard
from gflow_cli.config import get_settings

# run_all is bound in THIS module's namespace so tests can monkeypatch
# ``gflow_cli.cli_doctor.run_all``.
from gflow_cli.services.doctor import run_all

if TYPE_CHECKING:
    from gflow_cli.services.doctor import DoctorReport, Finding

_TAGS = {"pass": "[PASS]", "info": "[INFO]", "warn": "[WARN]", "fail": "[FAIL]"}

# Report groups: header -> check-id prefixes (frozen v1 inventory).
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("catalog", ("catalog.",)),
    ("db", ("db.",)),
    ("operations & queue", ("operations.", "queue.")),
    ("env & auth", ("env.", "auth.")),
)

# Brew-style caveat: shown before the first warning, ASCII only.
_CAVEAT = (
    "Note: doctor findings are diagnostic signals, not a to-do list."
    " If everything is working as expected, there is no need to chase them."
)


def _render_finding(finding: Finding) -> None:
    click.echo(f"{_TAGS[finding.severity]} {finding.check}: {finding.summary}")
    if finding.remediation:
        click.echo(f"       remediation: {finding.remediation}")
    if finding.row_uuids:
        click.echo(f"       rows: {', '.join(finding.row_uuids)}")


def _render_text(report: DoctorReport) -> None:
    if report.overall_status == "issues":
        click.echo(_CAVEAT)
        click.echo()
    for title, prefixes in _GROUPS:
        click.echo(f"== {title} ==")
        for finding in report.findings:
            if finding.check.startswith(prefixes):
                _render_finding(finding)
        click.echo()
    click.echo(f"Overall: {report.overall_status}")


@click.command()
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a machine-readable JSON report (experimental; shape may change).",
)
@click.pass_context
@_guard
def doctor(ctx: click.Context, as_json: bool) -> None:
    """Run read-only health checks over the catalog, database, and environment.

    Diagnoses, never heals: nothing is migrated, repaired, or written.
    Exit code 0 when clean, 33 when any warn/fail finding is reported.
    """
    report = run_all(_db_path(), get_settings())
    if as_json:
        json_output.emit(json_output.doctor_payload(report))
    else:
        _render_text(report)
    if report.overall_status == "issues":
        ctx.exit(33)
