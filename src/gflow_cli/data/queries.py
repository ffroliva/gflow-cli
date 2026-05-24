"""Read-only query functions over the gflow-cli SQLite catalog.

These are pure functions: they accept a db path, return frozen dataclass rows,
do not mutate state, and have no dependency on Click or Rich. The CLI adapter
(cli_data.py, Phase 2 Task 2.6) formats their output.

This module is the foundation for v0.10's ``show``/``search``/``export``
sub-commands; additional query functions (list_images, list_videos,
list_profiles) will be added in Tasks 2.3–2.5.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ProjectRow:
    project_id: str
    profile: str
    created_at: datetime
    image_count: int
    video_count: int


_LIST_PROJECTS_SQL = """
    SELECT
        p.flow_project_id AS project_id,
        p.profile_name    AS profile,
        p.created_at      AS created_at,
        (SELECT COUNT(*) FROM assets
         WHERE kind = 'image' AND flow_project_id = p.flow_project_id) AS image_count,
        (SELECT COUNT(*) FROM assets
         WHERE kind = 'video' AND flow_project_id = p.flow_project_id) AS video_count
    FROM projects p
    WHERE (:profile IS NULL OR p.profile_name = :profile)
    ORDER BY p.created_at DESC
    LIMIT :limit OFFSET :offset
"""


def list_projects(
    *,
    db_path: Path,
    profile: str | None,
    limit: int,
    offset: int,
) -> list[ProjectRow]:
    """Return projects newest-first, filtered by profile if given.

    image_count / video_count are aggregated via subqueries over the assets
    table (no dedicated images/videos tables in this schema).

    Args:
        db_path: Absolute path to the SQLite catalog.
        profile: If given, only return projects belonging to this profile name.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip (for pagination).

    Returns:
        A list of :class:`ProjectRow` instances ordered newest-first.
    """
    params = {"profile": profile, "limit": limit, "offset": offset}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(_LIST_PROJECTS_SQL, params).fetchall()
    return [
        ProjectRow(
            project_id=str(r["project_id"]),
            profile=str(r["profile"]),
            created_at=datetime.fromisoformat(str(r["created_at"])),
            image_count=int(r["image_count"]),
            video_count=int(r["video_count"]),
        )
        for r in rows
    ]
