"""Tests for gflow_cli.data.queries — pure read-only query functions."""
from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.data.queries import list_projects
from tests.fixtures.seeded_catalog import build_seeded_catalog


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    build_seeded_catalog(db)
    return db


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
