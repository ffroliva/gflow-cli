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


def test_no_incident_sentence_without_a_bundle(captured_console: io.StringIO) -> None:
    code = cli_helpers._handle_gflow_error(FlowAppError("crash"), cli_command="video t2v")
    assert code == 31
    assert "Incident bundle:" not in captured_console.getvalue()
