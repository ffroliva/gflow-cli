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
