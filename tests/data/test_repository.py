from pathlib import Path

import pytest

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
from gflow_cli.data.repository import DataRepository, verified_local_path
from gflow_cli.data.store import DataStore
from gflow_cli.errors import DataIntegrityError


def test_upserts_project_asset_operation_and_file(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/profile_default"))
        project = repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="gflow-cli t2i",
                source="generated",
            )
        )
        asset = repo.upsert_asset(
            AssetRecord(
                id="asset-local",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                flow_media_id="media-1",
                flow_workflow_id="workflow-1",
                flow_media_generation_id="generation-1",
                kind=AssetKind.IMAGE,
                status="ready",
                model="NARWHAL",
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                width=1024,
                height=1792,
                duration_seconds=None,
                seed=123,
                metadata_json={},
            )
        )
        operation = repo.insert_operation(
            OperationRecord(
                id="operation-local",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                command="gflow image t2i",
                mode=OperationKind.T2I,
                status=OperationStatus.SUCCEEDED,
                flow_operation_id=None,
                flow_batch_id=None,
                prompt="a prompt",
                prompt_hash=None,
                prompt_redacted=False,
                model="NARWHAL",
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                error_type=None,
                error_detail=None,
            )
        )
        repo.link_operation_asset(operation.id, asset.id, OperationAssetRole.OUTPUT, 0)
        repo.upsert_local_file(
            LocalFileRecord(
                id="file-local",
                profile_name="default",
                asset_id=asset.id,
                path=tmp_path / "media-1.png",
                media_type="image/png",
                bytes=10,
                sha256="a" * 64,
            )
        )

        found = repo.get_asset_by_flow_media_id("default", "media-1")
        assert found is not None
        assert found.flow_project_id == "flow-project-1"
        assert found.local_files[0].path == tmp_path / "media-1.png"


@pytest.mark.parametrize("raw_metadata", ["{broken", "[]", '"caption"'])
def test_asset_lookup_normalizes_malformed_or_non_object_metadata(
    tmp_path: Path, raw_metadata: str
) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/profile_default"))
        repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-1",
                profile_name="default",
                flow_project_id="project-1",
                flow_media_id="media-1",
            )
        )
        store.conn.execute(
            "UPDATE assets SET metadata_json = ? WHERE id = ?",
            (raw_metadata, "asset-1"),
        )

        found = repo.get_asset_by_flow_media_id("default", "media-1")

        assert found is not None
        assert found.metadata_json == {}


def test_verified_local_path_requires_recorded_sha256(tmp_path: Path) -> None:
    path = tmp_path / "legacy.png"
    path.write_bytes(b"same-size replacement")
    record = LocalFileRecord(
        id="file-1",
        profile_name="default",
        asset_id="asset-1",
        path=path,
        media_type="image/png",
        bytes=path.stat().st_size,
        sha256=None,
    )

    assert verified_local_path(record) is None


def test_operation_asset_position_is_unique(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/profile_default"))
        project = repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="title",
                source="generated",
            )
        )
        asset_one = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-1",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                flow_media_id="media-1",
            )
        )
        asset_two = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-2",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                flow_media_id="media-2",
            )
        )
        operation = repo.insert_operation(
            OperationRecord.minimal(
                id="operation-1",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                mode=OperationKind.I2I,
            )
        )
        repo.link_operation_asset(operation.id, asset_one.id, OperationAssetRole.INPUT, 0)
        with pytest.raises(DataIntegrityError):
            repo.link_operation_asset(operation.id, asset_two.id, OperationAssetRole.INPUT, 0)


def test_upsert_project_natural_key_conflict_is_idempotent(tmp_path: Path) -> None:
    """A second upsert with the same (profile_name, flow_project_id) but a
    fresh random ``id`` must dedupe on the natural key instead of crashing.

    Regression for the live ``UNIQUE constraint failed:
    projects.profile_name, projects.flow_project_id`` bug that blocked any
    second operation in an already-recorded project.
    """
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        repo.upsert_project(
            ProjectRecord(
                id="project-a",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="A",
                source="generated",
            )
        )

        # Second upsert: same natural key, DIFFERENT (fresh) id. Must NOT raise.
        repo.upsert_project(
            ProjectRecord(
                id="project-b",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="B",
                source="generated",
            )
        )

        rows = store.conn.execute(
            "SELECT id, title FROM projects WHERE profile_name = ? AND flow_project_id = ?",
            ("default", "flow-project-1"),
        ).fetchall()
        # Exactly one project row for the natural key.
        assert len(rows) == 1
        # The original row id is preserved (natural-key dedupe, not a new row).
        assert rows[0]["id"] == "project-a"
        # Mutable field (title) is updated on repeat.
        assert rows[0]["title"] == "B"


def test_upsert_project_repeat_preserves_existing_title_when_none(tmp_path: Path) -> None:
    """A repeat upsert that passes ``title=None`` must NOT clobber an existing
    non-null title (COALESCE keeps the stored value)."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        repo.upsert_project(
            ProjectRecord(
                id="project-a",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="Original Title",
                source="generated",
            )
        )
        repo.upsert_project(
            ProjectRecord(
                id="project-b",
                profile_name="default",
                flow_project_id="flow-project-1",
                title=None,
                source="recorded",
            )
        )

        row = store.conn.execute(
            "SELECT id, title, source, created_at FROM projects "
            "WHERE profile_name = ? AND flow_project_id = ?",
            ("default", "flow-project-1"),
        ).fetchone()
        assert row["id"] == "project-a"
        # Existing non-null title preserved despite the None on repeat.
        assert row["title"] == "Original Title"
        # Mutable source still updated.
        assert row["source"] == "recorded"


def test_upsert_asset_natural_key_conflict_raises_data_integrity_error(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/default"))
        repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="title",
                source="generated",
            )
        )
        repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-a",
                profile_name="default",
                flow_project_id="flow-project-1",
                flow_media_id="media-1",
            )
        )
        with pytest.raises(DataIntegrityError):
            repo.upsert_asset(
                AssetRecord.minimal_image(
                    id="asset-b",
                    profile_name="default",
                    flow_project_id="flow-project-1",
                    flow_media_id="media-1",
                )
            )


def test_resolve_seed_image_returns_project_media_and_path(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", tmp_path / "profile_default")
        project = repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="title",
                source="generated",
            )
        )
        asset = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-local",
                profile_name="default",
                flow_project_id=project.flow_project_id,
                flow_media_id="media-image-1",
            )
        )
        image_path = tmp_path / "image.png"
        image_path.write_bytes(b"image-bytes")
        repo.upsert_local_file(
            LocalFileRecord(
                id="file-local",
                profile_name="default",
                asset_id=asset.id,
                path=image_path,
                media_type="image/png",
                bytes=11,
                sha256="b" * 64,
            )
        )
        seed = repo.resolve_seed_image("default", "media-image-1")
        assert seed is not None
        assert seed.flow_project_id == "flow-project-1"
        assert seed.flow_media_id == "media-image-1"
        assert seed.local_path == image_path.resolve()


def test_seed_read_api_resolves_by_path_latest_project_and_candidate(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", tmp_path / "profile_default")
        repo.upsert_project(
            ProjectRecord(
                id="project-local",
                profile_name="default",
                flow_project_id="flow-project-1",
                title="title",
                source="generated",
            )
        )
        image_path = tmp_path / "latest.png"
        image_path.write_bytes(b"image-bytes")
        asset = repo.upsert_asset(
            AssetRecord.minimal_image(
                id="asset-latest",
                profile_name="default",
                flow_project_id="flow-project-1",
                flow_media_id="media-latest",
            )
        )
        repo.upsert_local_file(
            LocalFileRecord(
                id="file-latest",
                profile_name="default",
                asset_id=asset.id,
                path=image_path,
                media_type="image/png",
                bytes=11,
                sha256=None,
            )
        )
        by_path = repo.resolve_seed_image_by_path("default", image_path)
        latest = repo.resolve_latest_image("default", None, None, None)
        assert by_path is not None
        assert latest is not None
        assert by_path.flow_media_id == "media-latest"
        assert latest.flow_media_id == "media-latest"
        project_images = repo.list_project_images("default", "flow-project-1")
        assert [item.flow_media_id for item in project_images] == ["media-latest"]
        assert repo.candidate_image_exists("default", "media-latest") is True


def test_get_asset_by_any_id_resolves_media_workflow_and_asset_ids(tmp_path: Path) -> None:
    """PR #237: UUID→asset resolution must work for every id kind and hydrate
    the fields the MCP display-name lookup relies on (flow_workflow_id,
    metadata_json) — the original submission constructed AssetLookup with
    fields the dataclass did not declare (TypeError)."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        repo = DataRepository(store)
        repo.upsert_profile("default", Path("C:/profiles/profile_default"))
        repo.upsert_project(
            ProjectRecord(
                id="project-any",
                profile_name="default",
                flow_project_id="flow-project-any",
                title="t",
                source="generated",
            )
        )
        repo.upsert_asset(
            AssetRecord(
                id="asset-any",
                profile_name="default",
                flow_project_id="flow-project-any",
                flow_media_id="media-any",
                flow_workflow_id="workflow-any",
                flow_media_generation_id="generation-any",
                kind=AssetKind.IMAGE,
                status="ready",
                model="NARWHAL",
                aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
                width=1024,
                height=1792,
                duration_seconds=None,
                seed=1,
                metadata_json={"display_name": "A cozy cabin"},
            )
        )

        for ref in ("media-any", "workflow-any", "asset-any"):
            found = repo.get_asset_by_any_id("default", ref)
            assert found is not None, ref
            assert found.flow_media_id == "media-any"
            assert found.flow_workflow_id == "workflow-any"
            assert found.metadata_json == {"display_name": "A cozy cabin"}

        assert repo.get_asset_by_any_id("default", "nope") is None
        assert repo.get_asset_by_any_id("other-profile", "media-any") is None
