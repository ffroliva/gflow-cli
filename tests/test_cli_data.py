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
    assert "title" in result.output


def test_emit_projects_table(capsys: pytest.CaptureFixture[str]) -> None:
    from datetime import UTC, datetime

    from gflow_cli.cli_data import _emit_projects_table
    from gflow_cli.data.queries import ProjectRow

    row = ProjectRow(
        project_id="flow-proj-001",
        profile="alice",
        created_at=datetime.now(UTC),
        image_count=2,
        video_count=1,
        title="My Project",
    )
    _emit_projects_table([row])
    captured = capsys.readouterr().out
    assert "PROJECT_ID" in captured
    assert "TITLE" in captured
    assert "My Project" in captured


def test_data_list_projects_json(seeded_db: Path) -> None:
    result = CliRunner().invoke(main, ["data", "list", "projects", "--json"])
    assert result.exit_code == 0
    rows = [json.loads(ln) for ln in result.output.splitlines() if ln.strip()]
    assert len(rows) == 4
    assert "project_id" in rows[0]
    assert "image_count" in rows[0]
    assert "title" in rows[0]
    assert rows[0]["title"] is not None


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


# ─── --all-copies ────────────────────────────────────────────────────────────


def test_data_list_images_all_copies_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--all-copies returns one row per local_file; duplicate asset appears twice."""
    import uuid
    from datetime import UTC, datetime, timedelta

    from gflow_cli.data.models import AssetKind, AssetRecord, LocalFileRecord, ProjectRecord
    from gflow_cli.data.repository import DataRepository
    from gflow_cli.data.store import DataStore

    db = tmp_path / "all_copies.db"
    with DataStore.open(db) as store:
        repo = DataRepository(store)
        now = datetime.now(UTC)
        repo.upsert_profile("tester", tmp_path / "p")
        repo.upsert_project(
            ProjectRecord(
                id=str(uuid.uuid4()),
                profile_name="tester",
                flow_project_id="proj-ac",
                title="all-copies test",
                source="cli",
                created_at=now.isoformat(),
            )
        )
        asset_id = str(uuid.uuid4())
        repo.upsert_asset(
            AssetRecord(
                id=asset_id,
                profile_name="tester",
                flow_project_id="proj-ac",
                flow_media_id="img-ac-001",
                flow_workflow_id=None,
                flow_media_generation_id=None,
                kind=AssetKind.IMAGE,
                status="ready",
                model="imagen-3.0-fast-generate-001",
                aspect_ratio="1:1",
                width=512,
                height=512,
                duration_seconds=None,
                seed=1,
                metadata_json={},
                created_at=now.isoformat(),
            )
        )
        for i in range(2):
            repo.upsert_local_file(
                LocalFileRecord(
                    id=str(uuid.uuid4()),
                    profile_name="tester",
                    asset_id=asset_id,
                    path=Path(f"/tmp/gflow/tester/copy_{i}.png"),
                    media_type="image/png",
                    bytes=1024,
                    sha256=None,
                    created_at=(now + timedelta(seconds=i)).isoformat(),
                )
            )

    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))

    # default (aggregated): 1 row with copy_count=2
    result = CliRunner().invoke(main, ["data", "list", "images", "--json"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(ln) for ln in result.output.splitlines() if ln.strip()]
    assert len(rows) == 1
    assert rows[0]["copy_count"] == 2

    # --all-copies: 2 rows
    result2 = CliRunner().invoke(main, ["data", "list", "images", "--all-copies", "--json"])
    assert result2.exit_code == 0, result2.output
    rows2 = [json.loads(ln) for ln in result2.output.splitlines() if ln.strip()]
    assert len(rows2) == 2


# ─── prune ───────────────────────────────────────────────────────────────────


def _seed_with_dead_path(db_path: Path, *, live_path: Path, dead_path: Path) -> None:
    """Seed a DB with one image asset having two local_files: one live, one dead."""
    import uuid
    from datetime import UTC, datetime

    from gflow_cli.data.models import AssetKind, AssetRecord, LocalFileRecord, ProjectRecord
    from gflow_cli.data.repository import DataRepository
    from gflow_cli.data.store import DataStore

    with DataStore.open(db_path) as store:
        repo = DataRepository(store)
        now = datetime.now(UTC).isoformat()
        repo.upsert_profile("tester", db_path.parent / "p")
        repo.upsert_project(
            ProjectRecord(
                id=str(uuid.uuid4()),
                profile_name="tester",
                flow_project_id="proj-prune",
                title="prune test",
                source="cli",
                created_at=now,
            )
        )
        asset_id = str(uuid.uuid4())
        repo.upsert_asset(
            AssetRecord(
                id=asset_id,
                profile_name="tester",
                flow_project_id="proj-prune",
                flow_media_id="img-prune-001",
                flow_workflow_id=None,
                flow_media_generation_id=None,
                kind=AssetKind.IMAGE,
                status="ready",
                model="imagen-3.0-fast-generate-001",
                aspect_ratio="1:1",
                width=512,
                height=512,
                duration_seconds=None,
                seed=1,
                metadata_json={},
                created_at=now,
            )
        )
        live_path.write_bytes(b"x")
        for path in (live_path, dead_path):
            repo.upsert_local_file(
                LocalFileRecord(
                    id=str(uuid.uuid4()),
                    profile_name="tester",
                    asset_id=asset_id,
                    path=path,
                    media_type="image/png",
                    bytes=1,
                    sha256=None,
                    created_at=now,
                )
            )


def test_data_prune_dry_run_reports_dead_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = tmp_path / "live.png"
    dead = tmp_path / "dead.png"  # intentionally NOT created
    db = tmp_path / "prune.db"
    _seed_with_dead_path(db, live_path=live, dead_path=dead)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))

    result = CliRunner().invoke(main, ["data", "prune", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "dead" in result.output.lower()
    assert str(dead) in result.output
    assert "no changes made" in result.output.lower()

    # dry-run must not delete anything
    from gflow_cli.data.store import DataStore

    with DataStore.open(db) as store:
        count = store.conn.execute("SELECT COUNT(*) FROM local_files").fetchone()[0]
    assert count == 2


def test_data_prune_deletes_dead_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = tmp_path / "live.png"
    dead = tmp_path / "dead.png"
    db = tmp_path / "prune.db"
    _seed_with_dead_path(db, live_path=live, dead_path=dead)
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))

    result = CliRunner().invoke(main, ["data", "prune"])
    assert result.exit_code == 0, result.output
    assert "pruned" in result.output.lower()

    from gflow_cli.data.store import DataStore

    with DataStore.open(db) as store:
        rows = store.conn.execute("SELECT path FROM local_files").fetchall()
    paths = [r["path"] for r in rows]
    assert len(paths) == 1
    assert str(live) in paths[0]


def test_data_prune_no_dead_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """prune on a fresh (empty) DB exits cleanly with a 'nothing found' message."""
    from gflow_cli.data.store import DataStore

    db = tmp_path / "clean.db"
    DataStore.open(db).close()
    monkeypatch.setenv("GFLOW_CLI_DB_PATH", str(db))

    result = CliRunner().invoke(main, ["data", "prune"])
    assert result.exit_code == 0
    assert "no dead" in result.output.lower()


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
