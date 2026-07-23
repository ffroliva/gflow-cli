"""`gflow data errors export` + `prune` — bounded retention & export (#345)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.cli_data import _parse_older_than
from gflow_cli.data.models import OperationKind
from gflow_cli.data.queries import export_errors, list_errors
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


def _backdate_all(db_path: Path, *, days: int) -> None:
    """Rewrite every operation's started_at to `days` ago (recorder's UTC 'Z' format)."""
    ts = (
        (datetime.now(UTC) - timedelta(days=days))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    with DataStore.open(db_path) as store, store.transaction(immediate=True):
        store.conn.execute("UPDATE operations SET started_at = ?", (ts,))


# --- _parse_older_than ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("90d", timedelta(days=90)),
        ("24h", timedelta(hours=24)),
        ("30m", timedelta(minutes=30)),
        (" 7d ", timedelta(days=7)),
    ],
)
def test_parse_older_than_valid(text: str, expected: timedelta) -> None:
    assert _parse_older_than(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "90x", "d", "0d", "-5d", "90"])
def test_parse_older_than_rejects_junk(text: str) -> None:
    with pytest.raises(click.BadParameter):
        _parse_older_than(text)


# --- export_errors ----------------------------------------------------------


def test_export_errors_returns_all_unbounded(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    for _ in range(3):
        _seed_failed_op(db)
    assert len(export_errors(db_path=db)) == 3


def test_export_errors_older_than_filters(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)  # recent
    assert export_errors(db_path=db, older_than=timedelta(days=30)) == []
    _backdate_all(db, days=100)
    assert len(export_errors(db_path=db, older_than=timedelta(days=30))) == 1


def test_data_errors_export_cli_to_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    out = tmp_path / "errors.jsonl"
    result = CliRunner().invoke(main, ["data", "errors", "export", "-o", str(out)])
    assert result.exit_code == 0, result.output
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["error_type"] == "waf-rejection"


# --- prune ------------------------------------------------------------------


def test_prune_deletes_only_old_rows(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db, profile="old")
    _backdate_all(db, days=100)  # make the first row old
    _seed_failed_op(db, profile="fresh")  # a recent row
    with DataStore.open(db) as store:
        deleted = DataRepository(store).prune_failed_operations(older_than=timedelta(days=90))
    assert deleted == 1
    remaining = list_errors(db_path=db, profile=None, limit=20, offset=0)
    assert [r.profile for r in remaining] == ["fresh"]


def test_prune_dry_run_deletes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)
    _backdate_all(db, days=100)
    with DataStore.open(db) as store:
        would = DataRepository(store).prune_failed_operations(
            older_than=timedelta(days=90), dry_run=True
        )
    assert would == 1
    assert len(list_errors(db_path=db, profile=None, limit=20, offset=0)) == 1


def test_prune_clears_operation_assets_children(tmp_path: Path) -> None:
    """A failed op with a linked operation_assets row must prune without an FK
    error (child rows removed first)."""
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)
    with DataStore.open(db) as store, store.transaction(immediate=True):
        conn = store.conn
        op_id = conn.execute("SELECT id FROM operations WHERE status='failed'").fetchone()["id"]
        conn.execute(
            "INSERT INTO assets (id, profile_name, flow_media_id, kind, status, created_at)"
            " VALUES ('a1', 'default', 'm1', 'image', 'ok', '2020-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO operation_assets (operation_id, asset_id, role, position)"
            " VALUES (?, 'a1', 'input', 0)",
            (op_id,),
        )
    _backdate_all(db, days=100)
    with DataStore.open(db) as store:
        deleted = DataRepository(store).prune_failed_operations(older_than=timedelta(days=90))
    assert deleted == 1
    with DataStore.open(db) as store:
        assert store.conn.execute("SELECT COUNT(*) c FROM operation_assets").fetchone()["c"] == 0


def test_data_errors_prune_requires_older_than(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "gflow.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    result = CliRunner().invoke(main, ["data", "errors", "prune"])
    assert result.exit_code == 2
    assert "--older-than" in result.output


def test_data_errors_prune_cli_reports_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)
    _backdate_all(db, days=100)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    result = CliRunner().invoke(main, ["data", "errors", "prune", "--older-than", "90d"])
    assert result.exit_code == 0, result.output
    assert "Pruned 1" in result.output


def test_data_errors_export_cli_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    result = CliRunner().invoke(main, ["data", "errors", "export"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output.strip().splitlines()[-1])["error_type"] == "waf-rejection"


def test_data_errors_prune_dry_run_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)
    _backdate_all(db, days=100)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    result = CliRunner().invoke(
        main, ["data", "errors", "prune", "--older-than", "90d", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "would be deleted" in result.output
    assert len(list_errors(db_path=db, profile=None, limit=20, offset=0)) == 1


def test_data_errors_prune_cli_nothing_to_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "gflow.db"
    _seed_failed_op(db)  # recent — not older than 90d
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    result = CliRunner().invoke(main, ["data", "errors", "prune", "--older-than", "90d"])
    assert result.exit_code == 0, result.output
    assert "No failed operations older than the cutoff." in result.output
