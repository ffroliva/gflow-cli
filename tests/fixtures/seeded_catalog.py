"""Build a fixture SQLite catalog seeded with known projects/images/videos.

Used by tests/test_cli_data.py and tests/test_data_queries.py.

Catalog shape:
  - 3 profiles: alice (2 projects), bob (1 project), carol (1 project)
  - 4 projects total
  - 8 images total: 2 per project, each with operation prompt + local_file (.png)
  - 2 videos: only on alice's 2 projects (1 per project), each with operation
    prompt + local_file (.mp4)
  - 10 operations total (8 image ops + 2 video ops)
  - 10 local_files total
  - Timestamps span ~7 days so newest-first sort is verifiable
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gflow_cli.data.models import (
    AssetKind,
    AssetRecord,
    LocalFileRecord,
    OperationAssetRole,
    OperationKind,
    OperationRecord,
    OperationStatus,
    ProjectRecord,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore


def build_seeded_catalog(db_path: Path) -> tuple[DataStore, DataRepository]:
    """Create a fresh SQLite catalog at *db_path* and seed it with known data.

    Returns ``(store, repo)`` so callers can either query via the repository
    directly or inspect the raw SQLite connection via ``store.conn``.

    The caller is responsible for closing ``store`` when done (or using it as
    a context manager).
    """
    store = DataStore.open(db_path)
    repo = DataRepository(store)

    now = datetime.now(UTC)

    # profiles_with_project_counts: (profile_name, n_projects)
    # alice has 2 projects (and gets 1 video per project)
    # bob and carol have 1 project each (no videos)
    profiles_with_project_counts: list[tuple[str, int]] = [
        ("alice", 2),
        ("bob", 1),
        ("carol", 1),
    ]

    # Seed profiles first — projects/assets/operations all FK-reference profiles.
    for _, (profile, _) in enumerate(profiles_with_project_counts):
        repo.upsert_profile(name=profile, profile_dir=Path(f"/home/{profile}/.config/gflow"))

    for prof_idx, (profile, n_projects) in enumerate(profiles_with_project_counts):
        for proj_idx in range(n_projects):
            # Spread projects over the last 7 days (oldest = carol, newest = alice-0)
            proj_created = now - timedelta(days=prof_idx * 2 + proj_idx)
            proj_created_str = _iso(proj_created)

            project_record_id = str(uuid.uuid4())
            flow_project_id = f"flow-proj-{profile}-{proj_idx:03d}"

            repo.upsert_project(
                ProjectRecord(
                    id=project_record_id,
                    profile_name=profile,
                    flow_project_id=flow_project_id,
                    title=f"{profile} project {proj_idx}",
                    source="cli",
                    created_at=proj_created_str,
                )
            )

            # 2 images per project
            for img_idx in range(2):
                img_created = proj_created - timedelta(minutes=img_idx * 5)
                asset_id = str(uuid.uuid4())
                op_id = str(uuid.uuid4())

                repo.upsert_asset(
                    AssetRecord(
                        id=asset_id,
                        profile_name=profile,
                        flow_project_id=flow_project_id,
                        flow_media_id=f"img-media-{profile}-{proj_idx}-{img_idx}",
                        flow_workflow_id=None,
                        flow_media_generation_id=None,
                        kind=AssetKind.IMAGE,
                        status="ready",
                        model="imagen-3.0-fast-generate-001",
                        aspect_ratio="16:9",
                        width=1280,
                        height=720,
                        duration_seconds=None,
                        seed=img_idx + 1,
                        metadata_json={},
                        created_at=_iso(img_created),
                    )
                )

                repo.insert_operation(
                    OperationRecord(
                        id=op_id,
                        profile_name=profile,
                        flow_project_id=flow_project_id,
                        command="image",
                        mode=OperationKind.T2I,
                        status=OperationStatus.SUCCEEDED,
                        flow_operation_id=None,
                        flow_batch_id=None,
                        prompt=f"prompt for {profile} project {proj_idx} image {img_idx}",
                        prompt_hash=None,
                        prompt_redacted=False,
                        model="imagen-3.0-fast-generate-001",
                        aspect_ratio="16:9",
                        error_type=None,
                        error_detail=None,
                        started_at=_iso(img_created),
                        completed_at=_iso(img_created + timedelta(seconds=3)),
                    )
                )

                repo.link_operation_asset(
                    operation_id=op_id,
                    asset_id=asset_id,
                    role=OperationAssetRole.OUTPUT,
                    position=0,
                )

                repo.upsert_local_file(
                    LocalFileRecord(
                        id=str(uuid.uuid4()),
                        profile_name=profile,
                        asset_id=asset_id,
                        path=Path(f"/tmp/gflow/{profile}/{flow_project_id}/img_{img_idx}.png"),
                        media_type="image/png",
                        bytes=204800,
                        sha256=None,
                        created_at=_iso(img_created + timedelta(seconds=4)),
                    )
                )

            # 1 video per alice project; bob and carol get none
            if profile == "alice":
                vid_created = proj_created - timedelta(minutes=15)
                vid_asset_id = str(uuid.uuid4())
                vid_op_id = str(uuid.uuid4())

                repo.upsert_asset(
                    AssetRecord(
                        id=vid_asset_id,
                        profile_name=profile,
                        flow_project_id=flow_project_id,
                        flow_media_id=f"vid-media-{profile}-{proj_idx}",
                        flow_workflow_id=None,
                        flow_media_generation_id=None,
                        kind=AssetKind.VIDEO,
                        status="ready",
                        model="veo-2.0-generate-001",
                        aspect_ratio="16:9",
                        width=1280,
                        height=720,
                        duration_seconds=5.0,
                        seed=None,
                        metadata_json={},
                        created_at=_iso(vid_created),
                    )
                )

                repo.insert_operation(
                    OperationRecord(
                        id=vid_op_id,
                        profile_name=profile,
                        flow_project_id=flow_project_id,
                        command="video",
                        mode=OperationKind.T2V,
                        status=OperationStatus.SUCCEEDED,
                        flow_operation_id=None,
                        flow_batch_id=None,
                        prompt=f"video prompt for {profile} project {proj_idx}",
                        prompt_hash=None,
                        prompt_redacted=False,
                        model="veo-2.0-generate-001",
                        aspect_ratio="16:9",
                        error_type=None,
                        error_detail=None,
                        started_at=_iso(vid_created),
                        completed_at=_iso(vid_created + timedelta(seconds=30)),
                    )
                )

                repo.link_operation_asset(
                    operation_id=vid_op_id,
                    asset_id=vid_asset_id,
                    role=OperationAssetRole.OUTPUT,
                    position=0,
                )

                repo.upsert_local_file(
                    LocalFileRecord(
                        id=str(uuid.uuid4()),
                        profile_name=profile,
                        asset_id=vid_asset_id,
                        path=Path(f"/tmp/gflow/{profile}/{flow_project_id}/video_{proj_idx}.mp4"),
                        media_type="video/mp4",
                        bytes=5242880,
                        sha256=None,
                        created_at=_iso(vid_created + timedelta(seconds=31)),
                    )
                )

    return store, repo


def _iso(dt: datetime) -> str:
    """Format *dt* as an ISO-8601 UTC string (same format as the data layer)."""
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
