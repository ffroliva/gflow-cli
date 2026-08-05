"""Unit tests for character entity provenance recording (#402)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.video import Aspect as VideoAspect
from gflow_cli.api.video import GenerateVideoRequest
from gflow_cli.api.video import Mode as VideoMode
from gflow_cli.cli_video import video
from gflow_cli.data.models import OperationKind
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore


@pytest.fixture
def tmp_store(tmp_path: Path) -> DataStore:
    return DataStore.open(tmp_path / "test_gflow.db")


def test_recorder_persists_entity_metadata_image(tmp_store: DataStore, tmp_path: Path) -> None:
    repo = DataRepository(tmp_store)
    recorder = OperationRecorder(repo, prompt_mode="store")

    profile_name = "test_profile"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    repo.upsert_profile(profile_name, profile_dir)

    request = GenerateImageRequest(
        prompt="A warrior standing in the rain",
        aspect=ImageAspect.PORTRAIT,
        model=ImageModel.NARWHAL,
        reference_entities=("entity_char_123",),
        reference_entity_names=("Aria",),
    )

    from gflow_cli.api.dto import GeneratedImage, ProjectInfo

    project = ProjectInfo(project_id="proj_1", title="Test Project")
    images = [
        GeneratedImage(
            media_name="media_111",
            workflow_id="wf_1",
            seed=42,
            prompt="A warrior standing in the rain",
            model_name_type="NARWHAL",
            aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
            fife_url="https://example.com/img.png",
            dimensions=(1024, 1024),
        )
    ]
    saved_paths = [tmp_path / "media_111.png"]
    (tmp_path / "media_111.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    recorder.record_generated_images(
        profile_name=profile_name,
        profile_dir=profile_dir,
        project=project,
        operation_kind=OperationKind.T2I,
        request=request,
        images=images,
        saved_paths=saved_paths,
        input_media_ids=(),
    )

    rows = tmp_store.conn.execute(
        "SELECT metadata_json FROM operations WHERE profile_name = ?", (profile_name,)
    ).fetchall()
    assert len(rows) == 1
    metadata = json.loads(rows[0][0] or "{}")
    assert metadata.get("entity_ids") == ["entity_char_123"]
    assert metadata.get("entity_names") == ["Aria"]


def test_recorder_persists_entity_metadata_video(tmp_store: DataStore, tmp_path: Path) -> None:

    repo = DataRepository(tmp_store)
    recorder = OperationRecorder(repo, prompt_mode="store")

    profile_name = "test_profile"
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    repo.upsert_profile(profile_name, profile_dir)

    from gflow_cli.data.models import ProjectRecord

    repo.upsert_project(
        ProjectRecord(
            id="p1",
            profile_name=profile_name,
            flow_project_id="proj_video_1",
            title="Video Proj",
            source="generated",
        )
    )

    request = GenerateVideoRequest(
        prompt="A dragon flying over mountains",
        mode=VideoMode.T2V,
        aspect=VideoAspect.PORTRAIT,
        reference_entities=("entity_char_456",),
        reference_entity_names=("Draco",),
    )

    from gflow_cli.api.video import VideoResult, VideoStatus

    result = VideoResult(
        project_id="proj_video_1",
        status=VideoStatus(media_id="media_video_999", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        flow_operation_id="op_flow_1",
        local_path=tmp_path / "video.mp4",
    )
    (tmp_path / "video.mp4").write_bytes(b"\x00\x00\x00\x1cftypisomfake")

    recorder.record_completed_video(
        profile_name=profile_name,
        _profile_dir=profile_dir,
        request=request,
        result=result,
    )

    rows = tmp_store.conn.execute(
        "SELECT metadata_json FROM operations WHERE profile_name = ?", (profile_name,)
    ).fetchall()
    assert len(rows) == 1
    metadata = json.loads(rows[0][0] or "{}")
    assert metadata.get("entity_ids") == ["entity_char_456"]
    assert metadata.get("entity_names") == ["Draco"]


def test_cli_video_accepts_reference_entity_options() -> None:
    runner = CliRunner()
    result = runner.invoke(
        video,
        [
            "t2v",
            "--help",
        ],
    )
    assert result.exit_code == 0
    assert "--reference-entity" in result.output
    assert "--reference-entity-name" in result.output
