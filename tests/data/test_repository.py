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
from gflow_cli.data.repository import DataRepository
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


def test_upsert_project_natural_key_conflict_raises_data_integrity_error(tmp_path: Path) -> None:
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
        with pytest.raises(DataIntegrityError):
            repo.upsert_project(
                ProjectRecord(
                    id="project-b",
                    profile_name="default",
                    flow_project_id="flow-project-1",
                    title="B",
                    source="generated",
                )
            )


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
