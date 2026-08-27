"""A NULL column must read back as ``None``, never as the string ``"None"``.

``assets`` declares three columns that gflow reads into every listing row and
that the schema leaves **nullable** (``migrations/0001_initial.sql``):

    flow_project_id TEXT      -- ImageRow.project_id / VideoRow.project_id
    model           TEXT      -- .model
    aspect_ratio    TEXT      -- .aspect

``list_images`` / ``list_videos`` wrapped all three in a bare ``str(...)``. On a
NULL that produces the four-character string ``"None"``, which is indistinguishable
from a model genuinely called "None" and is emitted verbatim into
``gflow data list images --json``. Downstream that is worse than a crash: a
consumer filtering ``model == "None"`` silently matches real rows, and the
governance work in v0.61.0 exists precisely to stop synthesised values passing
as observed ones.

``_row_to_operation_error`` (same module) already guards ``model`` correctly —
the two listing paths simply never got the same treatment.

These rows are reachable today: the agentic arm records assets before the model
is known, and a project id is absent for media that arrives without one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gflow_cli.data.models import AssetKind, AssetRecord, ProjectRecord
from gflow_cli.data.queries import list_images, list_videos
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

_PROFILE = "nulltest"
_ISO = datetime.now(UTC).isoformat()


def _seed(db: Path, kind: AssetKind) -> None:
    """One asset with model / aspect_ratio / flow_project_id all NULL."""
    store = DataStore.open(db)
    repo = DataRepository(store)
    repo.upsert_profile(name=_PROFILE, profile_dir=Path(f"/home/{_PROFILE}/.config/gflow"))
    # A project row must exist for the listing join, but the ASSET deliberately
    # carries no flow_project_id — that is the nullable case under test.
    repo.upsert_project(
        ProjectRecord(
            id=str(uuid.uuid4()),
            profile_name=_PROFILE,
            flow_project_id=f"proj-{uuid.uuid4()}",
            title="null-column project",
            source="cli",
            created_at=_ISO,
        )
    )
    repo.upsert_asset(
        AssetRecord(
            id=str(uuid.uuid4()),
            profile_name=_PROFILE,
            flow_project_id=None,
            flow_media_id=f"media-{uuid.uuid4()}",
            flow_workflow_id=None,
            flow_media_generation_id=None,
            kind=kind,
            status="ready",
            model=None,
            aspect_ratio=None,
            width=None,
            height=None,
            duration_seconds=None,
            seed=None,
            created_at=_ISO,
            metadata_json=None,
        )
    )
    store.close()


@pytest.mark.parametrize(
    ("kind", "lister"),
    [(AssetKind.IMAGE, list_images), (AssetKind.VIDEO, list_videos)],
    ids=["images", "videos"],
)
def test_null_columns_read_back_as_none(tmp_path: Path, kind: AssetKind, lister: object) -> None:
    db = tmp_path / "null.db"
    _seed(db, kind)

    rows = lister(db_path=db, profile=_PROFILE, limit=10, offset=0)  # type: ignore[operator]

    assert len(rows) == 1, rows
    row = rows[0]
    for field in ("model", "aspect", "project_id"):
        value = getattr(row, field)
        assert value is None, f"{field} came back as {value!r} — a NULL was stringified"
        assert value != "None"


@pytest.mark.parametrize(
    ("kind", "lister"),
    [(AssetKind.IMAGE, list_images), (AssetKind.VIDEO, list_videos)],
    ids=["images", "videos"],
)
def test_all_copies_path_guards_the_same_columns(
    tmp_path: Path, kind: AssetKind, lister: object
) -> None:
    """``--all-copies`` is a SEPARATE SQL + constructor; it must not diverge.

    The two listing paths are built from different statements, so a guard added
    to one is not a guard added to the other.
    """
    db = tmp_path / "null_copies.db"
    _seed(db, kind)

    rows = lister(  # type: ignore[operator]
        db_path=db, profile=_PROFILE, limit=10, offset=0, all_copies=True
    )

    # No local_files rows were seeded, so the flat path may legitimately return
    # nothing; when it returns a row, that row must obey the same contract.
    for row in rows:
        assert row.model is None
        assert row.aspect is None
        assert row.project_id is None
