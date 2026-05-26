"""Tests for gflow_cli.data.queries — pure read-only query functions."""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.data.queries import (
    list_images,
    list_profiles,
    list_projects,
    list_videos,
)
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
