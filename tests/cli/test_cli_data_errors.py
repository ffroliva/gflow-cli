"""`gflow data list errors` (#341): query + CLI surface + markup safety."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.cli_data import _emit_errors_table
from gflow_cli.data.models import OperationKind
from gflow_cli.data.queries import OperationErrorRow, list_errors
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import WafRejectionError


def _seed_failed_op(db_path: Path, *, profile: str = "default", detail: str = "blocked") -> None:
    with DataStore.open(db_path) as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        recorder.record_failed_operation(
            profile_name=profile,
            profile_dir=db_path.parent / "p",
            command="image t2i",
            mode=OperationKind.T2I,
            exc=WafRejectionError(detail, status=403),
        )


def test_list_errors_returns_failed_rows_newest_first(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)
    rows = list_errors(db_path=db, profile=None, limit=20, offset=0)
    assert len(rows) == 1
    assert rows[0].error_type == "waf-rejection"
    assert rows[0].error_detail == "blocked"
    assert rows[0].command == "image t2i"


def test_list_errors_filters_by_profile(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db, profile="alpha")
    _seed_failed_op(db, profile="beta")
    rows = list_errors(db_path=db, profile="alpha", limit=20, offset=0)
    assert [r.profile for r in rows] == ["alpha"]


def test_data_list_errors_cli_emits_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    result = CliRunner().invoke(main, ["data", "list", "errors", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["error_type"] == "waf-rejection"
    assert payload["error_detail"] == "blocked"


def test_data_list_errors_cli_empty_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    result = CliRunner().invoke(main, ["data", "list", "errors", "--json"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


def test_emit_errors_table_escapes_rich_markup(capsys: pytest.CaptureFixture[str]) -> None:
    row = OperationErrorRow(
        started_at=datetime(2026, 7, 18, 12, 0),
        completed_at=None,
        profile="default",
        command="image t2i",
        mode="t2i",
        model=None,
        error_type="[bold red]waf[/bold red]",
        error_detail="detail with [markup] and \x1b[31mansi\x1b[0m",
    )
    _emit_errors_table([row])  # must not raise on bracketed/ANSI content
    out = capsys.readouterr().out
    assert "waf" in out
