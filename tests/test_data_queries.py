"""Tests for gflow_cli.data.queries — pure read-only query functions."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gflow_cli.data.models import AssetKind, AssetRecord, LocalFileRecord, ProjectRecord
from gflow_cli.data.queries import (
    _LIST_IMAGES_SQL,
    _LIST_VIDEOS_SQL,
    list_images,
    list_profiles,
    list_projects,
    list_videos,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from tests.fixtures.seeded_catalog import build_seeded_catalog


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    build_seeded_catalog(db)
    return db


# ---------------------------------------------------------------------------
# Empty / missing DB — regression coverage for #88
# ---------------------------------------------------------------------------


def test_list_profiles_on_missing_db_returns_empty(tmp_path: Path) -> None:
    """Closes #88 — `data list` used to crash on a missing/empty DB because the
    raw sqlite3.connect path skipped migrations. After routing through
    DataStore.open, missing/empty DBs are auto-migrated and queries return []."""
    missing = tmp_path / "does_not_exist.db"
    assert not missing.exists()
    rows = list_profiles(db_path=missing, limit=20, offset=0)
    assert rows == []
    # DataStore.open creates the file (with schema) — that's the contract.
    assert missing.exists()


def test_list_all_kinds_on_freshly_created_db_returns_empty(tmp_path: Path) -> None:
    """All four `list_*` functions must tolerate a freshly-created empty DB."""
    fresh = tmp_path / "fresh.db"
    assert list_profiles(db_path=fresh, limit=20, offset=0) == []
    assert list_projects(db_path=fresh, profile=None, limit=20, offset=0) == []
    assert list_images(db_path=fresh, profile=None, limit=20, offset=0) == []
    assert list_videos(db_path=fresh, profile=None, limit=20, offset=0) == []


def test_list_projects_returns_all_by_default(seeded: Path) -> None:
    rows = list_projects(db_path=seeded, profile=None, limit=20, offset=0)
    assert len(rows) == 4
    assert {r.profile for r in rows} == {"alice", "bob", "carol"}


def test_list_projects_filters_by_profile(seeded: Path) -> None:
    rows = list_projects(db_path=seeded, profile="alice", limit=20, offset=0)
    assert len(rows) == 2
    assert all(r.profile == "alice" for r in rows)


def test_list_projects_respects_limit(seeded: Path) -> None:
    rows = list_projects(db_path=seeded, profile=None, limit=2, offset=0)
    assert len(rows) == 2


def test_list_projects_respects_offset(seeded: Path) -> None:
    page1 = list_projects(db_path=seeded, profile=None, limit=2, offset=0)
    page2 = list_projects(db_path=seeded, profile=None, limit=2, offset=2)
    assert {r.project_id for r in page1} & {r.project_id for r in page2} == set()


def test_list_projects_newest_first(seeded: Path) -> None:
    rows = list_projects(db_path=seeded, profile=None, limit=20, offset=0)
    timestamps = [r.created_at for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


def test_list_projects_image_video_counts(seeded: Path) -> None:
    """Aggregates from assets table where kind = 'image' / 'video'."""
    rows = list_projects(db_path=seeded, profile="alice", limit=20, offset=0)
    # Alice has 2 projects, each with 2 images + 1 video
    for r in rows:
        assert r.image_count == 2
        assert r.video_count == 1


def test_list_projects_empty_catalog_returns_empty_list(tmp_path: Path) -> None:
    """Open a fresh DB with migrations only — no seed data."""
    from gflow_cli.data.store import DataStore

    db = tmp_path / "empty.db"
    DataStore.open(db).close()  # runs migrations, then closes
    rows = list_projects(db_path=db, profile=None, limit=20, offset=0)
    assert rows == []


# ─── list_images ──────────────────────────────────────────────────────────────


def test_list_images_returns_all_by_default(seeded: Path) -> None:
    rows = list_images(db_path=seeded, profile=None, limit=20, offset=0)
    assert len(rows) == 8


def test_list_images_filters_by_profile(seeded: Path) -> None:
    rows = list_images(db_path=seeded, profile="alice", limit=20, offset=0)
    assert len(rows) == 4
    assert all(r.profile == "alice" for r in rows)


def test_list_images_newest_first(seeded: Path) -> None:
    rows = list_images(db_path=seeded, profile=None, limit=20, offset=0)
    timestamps = [r.created_at for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


def test_list_images_carries_prompt_aspect_model_local_path(seeded: Path) -> None:
    rows = list_images(db_path=seeded, profile="alice", limit=1, offset=0)
    assert len(rows) == 1
    r = rows[0]
    assert r.prompt is not None  # operation seeded, so JOIN finds it
    assert r.aspect == "16:9"
    assert r.model  # non-empty; fixture uses Flow's real model identifier
    assert r.local_path is not None
    assert r.local_path.endswith(".png")
    assert r.copy_count == 1  # seeded catalog has one local_file per asset


def test_list_images_pagination(seeded: Path) -> None:
    page1 = list_images(db_path=seeded, profile=None, limit=4, offset=0)
    page2 = list_images(db_path=seeded, profile=None, limit=4, offset=4)
    assert {r.media_id for r in page1} & {r.media_id for r in page2} == set()


# ─── list_videos ──────────────────────────────────────────────────────────────


def test_list_videos_returns_all_by_default(seeded: Path) -> None:
    rows = list_videos(db_path=seeded, profile=None, limit=20, offset=0)
    assert len(rows) == 2
    assert all(r.profile == "alice" for r in rows)


def test_list_videos_filter_no_match(seeded: Path) -> None:
    rows = list_videos(db_path=seeded, profile="bob", limit=20, offset=0)
    assert rows == []


def test_list_videos_carries_duration(seeded: Path) -> None:
    rows = list_videos(db_path=seeded, profile="alice", limit=1, offset=0)
    assert rows[0].duration > 0  # float, e.g. 6.0


# ─── list_profiles ────────────────────────────────────────────────────────────


def test_list_profiles_returns_catalog_known_profiles(seeded: Path) -> None:
    rows = list_profiles(db_path=seeded, limit=20, offset=0)
    assert {r.profile_name for r in rows} == {"alice", "bob", "carol"}


def test_list_profiles_carries_aggregate_counts(seeded: Path) -> None:
    rows = list_profiles(db_path=seeded, limit=20, offset=0)
    by_name = {r.profile_name: r for r in rows}
    assert by_name["alice"].project_count == 2
    assert by_name["alice"].image_count == 4  # 2 projects × 2 images
    assert by_name["alice"].video_count == 2  # 2 projects × 1 video
    assert by_name["bob"].project_count == 1
    assert by_name["bob"].image_count == 2
    assert by_name["bob"].video_count == 0


def test_list_profiles_sorted_by_last_used_desc(seeded: Path) -> None:
    rows = list_profiles(db_path=seeded, limit=20, offset=0)
    timestamps = [r.last_used_at for r in rows]
    assert timestamps == sorted(timestamps, reverse=True)


# ─── multi-copy aggregation ───────────────────────────────────────────────────


def _build_multi_copy_db(db_path: Path) -> None:
    """One image asset with two local_files rows (different paths)."""
    with DataStore.open(db_path) as store:
        repo = DataRepository(store)
        now = datetime.now(UTC)
        repo.upsert_profile("tester", db_path.parent / "profile_tester")
        repo.upsert_project(
            ProjectRecord(
                id=str(uuid.uuid4()),
                profile_name="tester",
                flow_project_id="proj-mc",
                title="multi-copy test",
                source="cli",
                created_at=now.isoformat(),
            )
        )
        asset_id = str(uuid.uuid4())
        repo.upsert_asset(
            AssetRecord(
                id=asset_id,
                profile_name="tester",
                flow_project_id="proj-mc",
                flow_media_id="img-mc-001",
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
        # Two local copies at distinct paths.
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


def test_list_images_aggregated_shows_one_row_with_copy_count(tmp_path: Path) -> None:
    db = tmp_path / "mc.db"
    _build_multi_copy_db(db)
    rows = list_images(db_path=db, profile=None, limit=20, offset=0)
    assert len(rows) == 1
    assert rows[0].copy_count == 2


def test_list_images_all_copies_shows_one_row_per_file(tmp_path: Path) -> None:
    db = tmp_path / "mc.db"
    _build_multi_copy_db(db)
    rows = list_images(db_path=db, profile=None, limit=20, offset=0, all_copies=True)
    assert len(rows) == 2
    assert all(r.media_id == "img-mc-001" for r in rows)


def test_list_images_no_local_files_copy_count_is_zero(tmp_path: Path) -> None:
    """Asset with no local_files should appear with copy_count=0."""
    with DataStore.open(tmp_path / "zero.db") as store:
        repo = DataRepository(store)
        now = datetime.now(UTC).isoformat()
        repo.upsert_profile("tester", tmp_path / "p")
        repo.upsert_project(
            ProjectRecord(
                id=str(uuid.uuid4()),
                profile_name="tester",
                flow_project_id="proj-zero",
                title="no local",
                source="cli",
                created_at=now,
            )
        )
        repo.upsert_asset(
            AssetRecord(
                id=str(uuid.uuid4()),
                profile_name="tester",
                flow_project_id="proj-zero",
                flow_media_id="img-zero-001",
                flow_workflow_id=None,
                flow_media_generation_id=None,
                kind=AssetKind.IMAGE,
                status="ready",
                model="imagen-3.0-fast-generate-001",
                aspect_ratio="1:1",
                width=512,
                height=512,
                duration_seconds=None,
                seed=None,
                metadata_json={},
                created_at=now,
            )
        )
    rows = list_images(db_path=tmp_path / "zero.db", profile=None, limit=20, offset=0)
    assert len(rows) == 1
    assert rows[0].copy_count == 0
    assert rows[0].local_path is None


# ---------------------------------------------------------------------------
# Query-plan regression — closes #112
# ---------------------------------------------------------------------------


def _local_files_scan_lines(plan_rows: list[sqlite3.Row]) -> list[str]:
    """Return plan detail lines that scan the ``local_files`` table directly."""
    details = [str(row[-1]) for row in plan_rows]
    return [d for d in details if "local_files" in d and d.lstrip().startswith(("SCAN", "SEARCH"))]


@pytest.mark.parametrize(
    "sql",
    [_LIST_IMAGES_SQL, _LIST_VIDEOS_SQL],
    ids=["images", "videos"],
)
def test_list_aggregation_uses_index_not_full_scan(tmp_path: Path, sql: str) -> None:
    """#112: the ``GROUP BY asset_id`` aggregation must resolve via an index, never
    a full table scan of ``local_files``.

    The covering index is supplied for free by the ``UNIQUE(asset_id, path)``
    constraint — SQLite's implicit ``sqlite_autoindex_local_files_2`` — so no extra
    index or migration is warranted (this is why #112 needs no schema change). The
    assertion matches on "uses an index" rather than the autoindex's name so it stays
    valid if SQLite renames it or we later add an explicit index. It guards against a
    schema change (e.g. dropping that UNIQUE constraint) silently regressing the list
    queries to a full scan.
    """
    params = {"profile": None, "limit": 50, "offset": 0}
    with DataStore.open(tmp_path / "plan.db") as store:
        plan = store.conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()

    scans = _local_files_scan_lines(plan)
    assert scans, f"expected the plan to scan local_files; plan was {[r[-1] for r in plan]}"
    for line in scans:
        assert "USING" in line and "INDEX" in line, (
            f"full-scan regression on local_files — expected an index scan, got {line!r}"
        )


def test_local_files_scan_guard_detects_full_scan() -> None:
    """Proves the guard above is not vacuous: without the ``UNIQUE(asset_id, path)``
    covering index the same aggregation falls back to a bare ``SCAN local_files``,
    which :func:`_local_files_scan_lines` flags and the assertion above would reject."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE local_files (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, "
            "path TEXT NOT NULL)"
        )
        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT asset_id, COUNT(*), MAX(path) FROM local_files GROUP BY asset_id"
        ).fetchall()
    finally:
        conn.close()

    scans = _local_files_scan_lines(plan)
    assert scans, "expected a scan over local_files"
    assert any("USING" not in line or "INDEX" not in line for line in scans), (
        f"expected a bare full scan without the covering index; got {scans}"
    )
