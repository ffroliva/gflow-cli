"""``DataRepository`` table group: profiles and projects (#1249 wave 2).

Moved out of ``repository.py`` unchanged.
"""

# pyright: reportPrivateUsage=false
# The shared helpers are private to the data package; this module is one of its parts.

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, cast

from gflow_cli.data.models import (
    ProjectRecord,
)
from gflow_cli.errors import DataIntegrityError

if TYPE_CHECKING:
    pass


from gflow_cli.data._repo_base import (
    RepositoryBase,
    _utc_now,
)


class ProjectsRepo(RepositoryBase):
    """Profiles and projects."""

    def upsert_profile(self, name: str, profile_dir: Path) -> None:
        now = _utc_now()
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO profiles(name, profile_dir, first_seen_at, last_used_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        profile_dir = excluded.profile_dir,
                        last_used_at = excluded.last_used_at
                    """,
                    (name, str(profile_dir), now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.upsert_profile") from exc

    def upsert_project(self, record: ProjectRecord) -> ProjectRecord:
        # #1022: a run's title for a project it did not create is a placeholder
        # ("gflow-cli video"). Only an explicit create (source "gflow-cli") replaces a
        # stored title, and that source sticks; rename goes through update_project_title.
        now = _utc_now()
        created_at = record.created_at or now
        try:
            with self._store.transaction(immediate=True):
                self._store.conn.execute(
                    """
                    INSERT INTO projects(
                        id, profile_name, flow_project_id, title, source, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(profile_name, flow_project_id) DO UPDATE SET
                        title = CASE
                            WHEN excluded.source = 'gflow-cli'
                                THEN COALESCE(NULLIF(excluded.title, ''), projects.title)
                            ELSE COALESCE(NULLIF(projects.title, ''), excluded.title)
                        END,
                        source = CASE
                            WHEN projects.source = 'gflow-cli' THEN projects.source
                            ELSE excluded.source
                        END
                    """,
                    (
                        record.id,
                        record.profile_name,
                        record.flow_project_id,
                        record.title,
                        record.source,
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DataIntegrityError(detail=str(exc), route="data.upsert_project") from exc
        return cast("ProjectRecord", dataclasses.replace(record, created_at=created_at))  # pyright: ignore[reportUnnecessaryCast]

    def update_project_title(self, profile_name: str, flow_project_id: str, title: str) -> None:
        with self._store.transaction(immediate=True):
            self._store.conn.execute(
                """
                UPDATE projects
                SET title = ?
                WHERE profile_name = ? AND flow_project_id = ?
                """,
                (title, profile_name, flow_project_id),
            )

    def get_project(
        self,
        profile_name: str | None,
        flow_project_id: str,
    ) -> ProjectRecord | None:
        if profile_name is not None:
            row = self._store.conn.execute(
                """
                SELECT id, profile_name, flow_project_id, title, source, created_at
                FROM projects
                WHERE profile_name = ? AND flow_project_id = ?
                """,
                (profile_name, flow_project_id),
            ).fetchone()
        else:
            row = self._store.conn.execute(
                """
                SELECT id, profile_name, flow_project_id, title, source, created_at
                FROM projects
                WHERE flow_project_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (flow_project_id,),
            ).fetchone()
        if row is None:
            return None
        return ProjectRecord(
            id=str(row["id"]),
            profile_name=str(row["profile_name"]),
            flow_project_id=str(row["flow_project_id"]),
            title=str(row["title"]) if row["title"] is not None else None,
            source=str(row["source"]),
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
        )
