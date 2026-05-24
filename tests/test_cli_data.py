"""CLI integration tests for `gflow data list ...`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.cli import main
from tests.fixtures.seeded_catalog import build_seeded_catalog


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "catalog.db"
    store, _repo = build_seeded_catalog(db)
    store.close()
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    return db


@pytest.fixture
def empty_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from gflow_cli.data.store import DataStore

    db = tmp_path / "empty.db"
    DataStore.open(db).close()
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))
    return db


# ─── projects ────────────────────────────────────────────────────────────────


def test_data_list_projects_default(seeded_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "projects"])
    assert result.exit_code == 0
    assert "alice" in result.output


def test_data_list_projects_json(seeded_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "projects", "--json"])
    assert result.exit_code == 0
    rows = [json.loads(ln) for ln in result.output.splitlines() if ln.strip()]
    assert len(rows) == 4
    assert "project_id" in rows[0]
    assert "image_count" in rows[0]


def test_data_list_projects_profile_filter(seeded_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "projects", "--profile", "alice", "--json"])
    assert result.exit_code == 0
    rows = [json.loads(ln) for ln in result.output.splitlines() if ln.strip()]
    assert len(rows) == 2
    assert all(r["profile"] == "alice" for r in rows)


def test_data_list_projects_pagination(seeded_db: Path) -> None:
    page1 = CliRunner().invoke(main, ["data", "list", "projects", "--limit", "2", "--offset", "0", "--json"])
    page2 = CliRunner().invoke(main, ["data", "list", "projects", "--limit", "2", "--offset", "2", "--json"])
    assert page1.exit_code == 0
    assert page2.exit_code == 0
    p1 = {json.loads(ln)["project_id"] for ln in page1.output.splitlines() if ln.strip()}
    p2 = {json.loads(ln)["project_id"] for ln in page2.output.splitlines() if ln.strip()}
    assert p1 & p2 == set()


def test_data_list_projects_invalid_limit(seeded_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "projects", "--limit", "0"])
    assert result.exit_code == 2


def test_data_list_projects_empty_catalog(empty_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "projects"])
    assert result.exit_code == 0
    result_json = CliRunner().invoke(main, ["data", "list", "projects", "--json"])
    assert result_json.exit_code == 0
    assert result_json.output.strip() == ""


# ─── images ──────────────────────────────────────────────────────────────────


def test_data_list_images_json(seeded_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "images", "--json"])
    assert result.exit_code == 0
    rows = [json.loads(ln) for ln in result.output.splitlines() if ln.strip()]
    assert len(rows) == 8


def test_data_list_images_profile_filter(seeded_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "images", "--profile", "alice", "--json"])
    assert result.exit_code == 0
    rows = [json.loads(ln) for ln in result.output.splitlines() if ln.strip()]
    assert len(rows) == 4


# ─── videos ──────────────────────────────────────────────────────────────────


def test_data_list_videos_json(seeded_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "videos", "--json"])
    assert result.exit_code == 0
    rows = [json.loads(ln) for ln in result.output.splitlines() if ln.strip()]
    assert len(rows) == 2


def test_data_list_videos_filter_no_match(seeded_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "videos", "--profile", "bob", "--json"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


# ─── profiles ────────────────────────────────────────────────────────────────


def test_data_list_profiles(seeded_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "profiles", "--json"])
    assert result.exit_code == 0
    rows = [json.loads(ln) for ln in result.output.splitlines() if ln.strip()]
    assert len(rows) == 3
    assert {r["profile_name"] for r in rows} == {"alice", "bob", "carol"}


def test_data_list_profiles_no_profile_option(seeded_db: Path) -> None:
    """The profiles subcommand has no --profile flag."""
    result = CliRunner().invoke(main, ["data", "list", "profiles", "--profile", "alice"])
    assert result.exit_code == 2


# ─── error handling ──────────────────────────────────────────────────────────


def test_data_list_db_missing_exits_16(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A DB path pointing at a directory with no schema → DataStoreError → exit 16."""
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(tmp_path / "does-not-exist.db"))
    result = CliRunner().invoke(main, ["data", "list", "projects"])
    # sqlite3 creates an empty file on connect, but without migrations the
    # query fails with OperationalError (no such table). _safe_db wraps it
    # as DataStoreError → _guard raises Exit(16).
    assert result.exit_code == 16
