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
    page1 = CliRunner().invoke(
        main, ["data", "list", "projects", "--limit", "2", "--offset", "0", "--json"]
    )
    page2 = CliRunner().invoke(
        main, ["data", "list", "projects", "--limit", "2", "--offset", "2", "--json"]
    )
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


def test_data_list_videos_null_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a video row with NULL duration_seconds (e.g. omni-flash t2v,
    where the response shape omits duration) must not crash list_videos with
    TypeError on float(None). Both JSON output and the Rich table renderer
    must handle a None duration cleanly."""
    import uuid
    from datetime import UTC, datetime

    from gflow_cli.data.models import AssetKind, AssetRecord, ProjectRecord
    from gflow_cli.data.repository import DataRepository
    from gflow_cli.data.store import DataStore

    db = tmp_path / "null_duration.db"
    store = DataStore.open(db)
    try:
        repo = DataRepository(store)
        now_iso = datetime.now(UTC).isoformat()
        repo.upsert_profile(name="someone", profile_dir=tmp_path / "profile_someone")
        repo.upsert_project(
            ProjectRecord(
                id=str(uuid.uuid4()),
                profile_name="someone",
                flow_project_id="proj-x",
                title="null-duration regression",
                source="cli",
                created_at=now_iso,
            )
        )
        repo.upsert_asset(
            AssetRecord(
                id=str(uuid.uuid4()),
                profile_name="someone",
                flow_project_id="proj-x",
                flow_media_id="vid-null-duration",
                flow_workflow_id=None,
                flow_media_generation_id=None,
                kind=AssetKind.VIDEO,
                status="ready",
                model="omni-flash",
                aspect_ratio="16:9",
                width=1280,
                height=720,
                duration_seconds=None,
                seed=None,
                metadata_json={},
                created_at=now_iso,
            )
        )
    finally:
        store.close()
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))

    result_json = CliRunner().invoke(main, ["data", "list", "videos", "--json"])
    assert result_json.exit_code == 0, result_json.output
    rows = [json.loads(ln) for ln in result_json.output.splitlines() if ln.strip()]
    assert len(rows) == 1
    assert rows[0]["duration"] is None

    result_tbl = CliRunner().invoke(main, ["data", "list", "videos"])
    assert result_tbl.exit_code == 0, result_tbl.output
    assert "vid-null-duration" in result_tbl.output


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


def test_data_list_db_missing_exits_0_with_empty_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes #88 — a missing DB path is no longer an error.

    `_safe_db` now routes through `DataStore.open`, which auto-creates the file
    and runs migrations. A first-time user (or anyone recovering from #86 by
    deleting the DB) sees zero rows and exit 0, not a `DataStoreError`/exit 16.
    """
    missing = tmp_path / "does-not-exist.db"
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(missing))
    result = CliRunner().invoke(main, ["data", "list", "projects"])
    assert result.exit_code == 0, result.output
    # DataStore.open is responsible for creating the file + applying migrations.
    assert missing.exists(), "DataStore.open must create the catalog file"
