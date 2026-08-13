"""Human-facing incident sentence on the CLI error path (Task 9 — S04/S21)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

import gflow_cli._cli_helpers as cli_helpers
from gflow_cli.diagnostics import IncidentRef
from gflow_cli.errors import FlowAppError


@pytest.fixture
def captured_console(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    buffer = io.StringIO()
    monkeypatch.setattr(
        cli_helpers, "_console", Console(file=buffer, force_terminal=False, width=200)
    )
    return buffer


def test_local_incident_output_warns_review_before_sharing(
    captured_console: io.StringIO,
) -> None:
    exc = FlowAppError("crash")
    exc.incident_ref = IncidentRef(
        id="corr-fp",
        capture_status="complete",
        path=Path("C:/gflow/incidents/2026-07-22/x"),
        artifacts=("ui.json", "sensitive/screenshot.png"),
    )
    code = cli_helpers._handle_gflow_error(exc, cli_command="video t2v")
    out = captured_console.getvalue()
    assert code == 31
    assert "Incident bundle:" in out
    assert str(Path("C:/gflow/incidents/2026-07-22/x")) in out
    assert "eview before sharing" in out  # Review/review
    assert "account or media data" in out


def test_report_path_printed_when_ref_carries_report(
    captured_console: io.StringIO, tmp_path: Path
) -> None:
    """Issue #476: the report line keys on the ref's artifact tuple — the same
    source of truth the --json surface serializes."""
    exc = FlowAppError("crash")
    exc.incident_ref = IncidentRef(
        id="corr-fp",
        capture_status="complete",
        path=tmp_path,
        artifacts=("report.md", "ui.json"),
    )
    cli_helpers._handle_gflow_error(exc, cli_command="video t2v")
    out = captured_console.getvalue()
    assert "Pre-filled bug report:" in out
    assert str(tmp_path / "report.md") in out


def test_no_report_line_when_report_write_failed(
    captured_console: io.StringIO, tmp_path: Path
) -> None:
    """A ref without report.md (write failed and was unlinked) must not
    advertise a report."""
    exc = FlowAppError("crash")
    exc.incident_ref = IncidentRef(
        id="corr-fp",
        capture_status="partial",
        path=tmp_path,
        artifacts=("ui.json",),
    )
    cli_helpers._handle_gflow_error(exc, cli_command="video t2v")
    assert "Pre-filled bug report:" not in captured_console.getvalue()


def test_no_incident_sentence_without_a_bundle(captured_console: io.StringIO) -> None:
    code = cli_helpers._handle_gflow_error(FlowAppError("crash"), cli_command="video t2v")
    assert code == 31
    assert "Incident bundle:" not in captured_console.getvalue()


def test_unhandled_error_surfaces_incident_bundle(captured_console: io.StringIO) -> None:
    """Review fix: unexpected exceptions with a staged bundle must print the
    path — otherwise the evidence is written and silently aged out."""
    exc = RuntimeError("totally unexpected")
    exc.incident_ref = IncidentRef(  # type: ignore[attr-defined]
        id="corr-fp",
        capture_status="partial",
        path=Path("C:/gflow/incidents/2026-07-23/x"),
        artifacts=("network.json",),
    )
    code = cli_helpers._handle_unhandled_error(exc, cli_command="video t2v")
    out = captured_console.getvalue()
    assert code == 1
    assert "Incident bundle:" in out
    assert str(Path("C:/gflow/incidents/2026-07-23/x")) in out
