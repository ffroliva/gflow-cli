import hashlib
import json
from pathlib import Path

from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.video import Aspect as VideoAspect
from gflow_cli.api.video import GenerateVideoRequest, Mode, VideoResult, VideoStarted, VideoStatus
from gflow_cli.data.recorder import OperationRecorder
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.storage import CloudStorageInfo


def test_record_upload_persists_project_asset_and_file(tmp_path: Path) -> None:
    image_path = tmp_path / "seed.png"
    image_path.write_bytes(b"png-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        project = ProjectInfo(project_id="flow-project-1", title="gflow-cli upload")
        asset = AssetInfo(
            name="media-upload-1",
            project_id="flow-project-1",
            workflow_id="workflow-upload-1",
            display_name="seed.png",
            width=640,
            height=480,
        )
        recorder.record_upload_image(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project=project,
            asset=asset,
            image_path=image_path,
        )
        found = recorder.repository.get_asset_by_flow_media_id("default", "media-upload-1")
        assert found is not None
        assert found.flow_project_id == "flow-project-1"
        assert found.local_files[0].path == image_path.resolve()


def test_record_generated_images_persists_generation_metadata(tmp_path: Path) -> None:
    saved = tmp_path / "image.png"
    saved.write_bytes(b"image-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="redacted")
        project = ProjectInfo(project_id="flow-project-1", title="gflow-cli t2i")
        image = GeneratedImage(
            media_name="media-generated-1",
            workflow_id="workflow-generated-1",
            seed=123,
            prompt="prompt text",
            model_name_type="NARWHAL",
            aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
            fife_url="https://flow-content.google/path?Signature=abc",
            dimensions=(1024, 1792),
            media_generation_id="generation-1",
        )
        req = GenerateImageRequest(
            prompt="prompt text",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
        )
        recorder.record_generated_images(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project=project,
            request=req,
            images=[image],
            saved_paths=[saved],
            input_media_ids=[],
            operation_kind="t2i",
        )
        asset_row = store.conn.execute(
            "SELECT flow_media_generation_id, metadata_json "
            "FROM assets WHERE flow_media_id='media-generated-1'"
        ).fetchone()
        operation_row = store.conn.execute(
            "SELECT prompt, prompt_hash, prompt_redacted FROM operations WHERE mode='t2i'"
        ).fetchone()
        assert operation_row["prompt"] is None
        assert operation_row["prompt_hash"]
        assert operation_row["prompt_redacted"] == 1
        assert asset_row["flow_media_generation_id"] == "generation-1"
        assert "Signature=abc" not in asset_row["metadata_json"]


def _generated_image() -> GeneratedImage:
    return GeneratedImage(
        media_name="media-generated-1",
        workflow_id="workflow-generated-1",
        seed=123,
        prompt="expanded prompt",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://flow-content.google/path?Signature=abc",
        dimensions=(1024, 1792),
        media_generation_id="generation-1",
    )


def test_record_generated_images_persists_original_and_expanded_prompt(tmp_path: Path) -> None:
    saved = tmp_path / "image.png"
    saved.write_bytes(b"image-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        project = ProjectInfo(project_id="flow-project-1", title="gflow-cli t2i")
        # The request carries the EXPANDED prompt (what was submitted to Flow)
        # AND the user's original prompt (recorder reads both off the request).
        req = GenerateImageRequest(
            prompt="a richly detailed expanded prompt",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
            original_prompt="cat in space",
        )
        recorder.record_generated_images(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project=project,
            request=req,
            images=[_generated_image()],
            saved_paths=[saved],
            input_media_ids=[],
            operation_kind="t2i",
        )
        row = store.conn.execute(
            "SELECT prompt, expanded_prompt, prompt_redacted FROM operations WHERE mode='t2i'"
        ).fetchone()
        # Original is the recorded prompt; expansion is preserved separately.
        assert row["prompt"] == "cat in space"
        assert row["expanded_prompt"] == "a richly detailed expanded prompt"
        assert row["prompt_redacted"] == 0


def test_expanded_prompt_withheld_when_history_redacted(tmp_path: Path) -> None:
    saved = tmp_path / "image.png"
    saved.write_bytes(b"image-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="redacted")
        project = ProjectInfo(project_id="flow-project-1", title="gflow-cli t2i")
        req = GenerateImageRequest(
            prompt="a richly detailed expanded prompt",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
            original_prompt="cat in space",
        )
        recorder.record_generated_images(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project=project,
            request=req,
            images=[_generated_image()],
            saved_paths=[saved],
            input_media_ids=[],
            operation_kind="t2i",
        )
        row = store.conn.execute(
            "SELECT prompt, prompt_hash, expanded_prompt, prompt_redacted "
            "FROM operations WHERE mode='t2i'"
        ).fetchone()
        # Redacted mode withholds BOTH prompt texts; only the original's hash survives.
        assert row["prompt"] is None
        assert row["prompt_hash"]
        assert row["expanded_prompt"] is None
        assert row["prompt_redacted"] == 1


def test_no_expansion_leaves_expanded_prompt_null(tmp_path: Path) -> None:
    saved = tmp_path / "image.png"
    saved.write_bytes(b"image-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        project = ProjectInfo(project_id="flow-project-1", title="gflow-cli t2i")
        req = GenerateImageRequest(
            prompt="cat in space",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
        )
        recorder.record_generated_images(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project=project,
            request=req,
            images=[_generated_image()],
            saved_paths=[saved],
            input_media_ids=[],
            operation_kind="t2i",
        )
        row = store.conn.execute(
            "SELECT prompt, expanded_prompt FROM operations WHERE mode='t2i'"
        ).fetchone()
        assert row["prompt"] == "cat in space"
        assert row["expanded_prompt"] is None


def test_record_started_video_persists_expanded_prompt(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        request = GenerateVideoRequest(
            prompt="a cinematic expanded video prompt",
            mode=Mode.T2V,
            aspect=VideoAspect.PORTRAIT,
            original_prompt="a dog surfing",
        )
        recorder.record_started_video(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            request=request,
            started=VideoStarted(
                media_id="media-video-1",
                project_id="flow-project-video-1",
                flow_operation_id="operation-video-1",
            ),
        )
        row = store.conn.execute(
            "SELECT prompt, expanded_prompt FROM operations WHERE mode='t2v'"
        ).fetchone()
        assert row["prompt"] == "a dog surfing"
        assert row["expanded_prompt"] == "a cinematic expanded video prompt"


def test_record_started_video_persists_pending_media_and_operation(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        request = GenerateVideoRequest(
            prompt="video prompt",
            mode=Mode.T2V,
            aspect=VideoAspect.PORTRAIT,
        )
        recorder.record_started_video(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            request=request,
            started=VideoStarted(
                media_id="media-video-1",
                project_id="flow-project-video-1",
                flow_operation_id="operation-video-1",
            ),
        )
        asset = recorder.repository.get_asset_by_flow_media_id("default", "media-video-1")
        assert asset is not None
        row = store.conn.execute(
            "SELECT status, flow_operation_id FROM operations WHERE mode='t2v'"
        ).fetchone()
        assert row["status"] == "started"
        assert row["flow_operation_id"] == "operation-video-1"


def test_record_completed_video_updates_media_operation_and_file(tmp_path: Path) -> None:
    saved = tmp_path / "video.mp4"
    saved.write_bytes(b"video-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        request = GenerateVideoRequest(
            prompt="video prompt",
            mode=Mode.T2V,
            aspect=VideoAspect.PORTRAIT,
        )
        recorder.record_started_video(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            request=request,
            started=VideoStarted(
                media_id="media-video-1",
                project_id="flow-project-video-1",
                flow_operation_id="media-video-1",
            ),
        )
        result = VideoResult(
            status=VideoStatus(
                media_id="media-video-1",
                status="MEDIA_GENERATION_STATUS_SUCCESSFUL",
            ),
            local_path=saved,
            project_id="flow-project-video-1",
            flow_operation_id="media-video-1",
        )
        recorder.record_completed_video(
            profile_name="default",
            _profile_dir=tmp_path / "profile_default",
            request=request,
            result=result,
        )
        asset = recorder.repository.get_asset_by_flow_media_id("default", "media-video-1")
        assert asset is not None
        assert asset.flow_project_id == "flow-project-video-1"
        assert asset.local_files[0].path == saved.resolve()
        row = store.conn.execute(
            "SELECT status, completed_at FROM operations WHERE mode='t2v'"
        ).fetchone()
        assert row["status"] == "succeeded"
        assert row["completed_at"] is not None


def test_record_completed_video_with_cloud_storage_uses_cloud_columns(
    tmp_path: Path,
) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        request = GenerateVideoRequest(
            prompt="video prompt",
            mode=Mode.T2V,
            aspect=VideoAspect.PORTRAIT,
        )
        recorder.record_started_video(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            request=request,
            started=VideoStarted(
                media_id="media-video-1",
                project_id="flow-project-video-1",
                flow_operation_id="media-video-1",
            ),
        )
        result = VideoResult(
            status=VideoStatus(
                media_id="media-video-1",
                status="MEDIA_GENERATION_STATUS_SUCCESSFUL",
            ),
            local_path=tmp_path / "cloud-placeholder.mp4",
            project_id="flow-project-video-1",
            flow_operation_id="media-video-1",
        )
        cloud_info = CloudStorageInfo(
            uri="s3://bucket/prefix/videos/2026-05-28/media-video-1.mp4",
            provider="s3",
        )

        recorder.record_completed_video(
            profile_name="default",
            _profile_dir=tmp_path / "profile_default",
            request=request,
            result=result,
            cloud_storage_info=cloud_info,
        )

        row = store.conn.execute(
            "SELECT path, bytes, sha256, storage_provider, cloud_uri FROM local_files"
        ).fetchone()
        # The v1 schema keeps local_files.path NOT NULL for the unique key, so
        # cloud rows store the URI there while hydrated records expose path=None.
        assert row["path"] == cloud_info.uri
        assert row["bytes"] is None
        assert row["sha256"] is None
        assert row["storage_provider"] == "s3"
        assert row["cloud_uri"] == cloud_info.uri
        asset = recorder.repository.get_asset_by_flow_media_id("default", "media-video-1")
        assert asset is not None
        assert asset.local_files[0].path is None
        assert asset.local_files[0].cloud_uri == cloud_info.uri


# ---------------------------------------------------------------------------
# metadata_json.tool — applied-tool provenance (PR2 §8)
# ---------------------------------------------------------------------------


def _applied_tool() -> object:
    from gflow_cli.tools.invocation import AppliedTool

    return AppliedTool(
        name="creative-director",
        version="1",
        model="gemini-2.5-flash",
        config_hash="a" * 64,
        params=(("style", "cinema"),),
    )


def _op_tool_meta(store: DataStore, mode: str) -> dict[str, object]:
    row = store.conn.execute(
        "SELECT metadata_json FROM operations WHERE mode = ?", (mode,)
    ).fetchone()
    assert row["metadata_json"]
    return json.loads(row["metadata_json"])["tool"]


def test_record_generated_images_persists_tool_metadata_store_mode(tmp_path: Path) -> None:
    saved = tmp_path / "image.png"
    saved.write_bytes(b"image-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        req = GenerateImageRequest(
            prompt="a richly detailed expanded prompt",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
            original_prompt="cat",
            tool=_applied_tool(),  # type: ignore[arg-type]
        )
        recorder.record_generated_images(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project=ProjectInfo(project_id="p1", title="t"),
            request=req,
            images=[_generated_image()],
            saved_paths=[saved],
            input_media_ids=[],
            operation_kind="t2i",
        )
        tool = _op_tool_meta(store, "t2i")
        assert tool["name"] == "creative-director"
        assert tool["version"] == "1"
        assert tool["model"] == "gemini-2.5-flash"
        assert tool["params"] == {"style": "cinema"}
        assert tool["config_hash"] == "a" * 64
        # Store mode does NOT carry a params_hash (the raw params are stored).
        assert "params_hash" not in tool


def test_record_generated_images_redacts_tool_metadata(tmp_path: Path) -> None:
    saved = tmp_path / "image.png"
    saved.write_bytes(b"image-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="redacted")
        req = GenerateImageRequest(
            prompt="expanded",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
            original_prompt="cat",
            tool=_applied_tool(),  # type: ignore[arg-type]
        )
        recorder.record_generated_images(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project=ProjectInfo(project_id="p1", title="t"),
            request=req,
            images=[_generated_image()],
            saved_paths=[saved],
            input_media_ids=[],
            operation_kind="t2i",
        )
        tool = _op_tool_meta(store, "t2i")
        # Redacted mode stores only name/version/params_hash/config_hash — never
        # the raw model or free-text params (redact_metadata wouldn't catch them).
        assert tool == {
            "name": "creative-director",
            "version": "1",
            "params_hash": hashlib.sha256(
                json.dumps({"style": "cinema"}, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "config_hash": "a" * 64,
        }
        assert "model" not in tool
        assert "params" not in tool


def test_record_generated_images_without_tool_writes_no_tool_metadata(tmp_path: Path) -> None:
    saved = tmp_path / "image.png"
    saved.write_bytes(b"image-bytes")
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        req = GenerateImageRequest(prompt="cat", aspect=Aspect.PORTRAIT, model=Model.NARWHAL)
        recorder.record_generated_images(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            project=ProjectInfo(project_id="p1", title="t"),
            request=req,
            images=[_generated_image()],
            saved_paths=[saved],
            input_media_ids=[],
            operation_kind="t2i",
        )
        row = store.conn.execute("SELECT metadata_json FROM operations WHERE mode='t2i'").fetchone()
        # No tool applied → metadata_json carries no tool key (NULL or no 'tool').
        meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        assert "tool" not in meta


class TestIsMediaRecorded:
    """Recorder-level half of the #281 pre-download attribution guard.

    ``is_media_recorded`` is the boolean wrapper the CLI layer calls BEFORE
    downloading anything (``cli_image._verify_media_attribution``) — see
    ``tests/cli/test_cli_image.py``.
    """

    def test_false_when_nothing_recorded(self, tmp_path: Path) -> None:
        with DataStore.open(tmp_path / "gflow.db") as store:
            recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
            assert (
                recorder.is_media_recorded(profile_name="default", flow_media_id="media-x")
                is False
            )

    def test_true_after_generated_image_recorded(self, tmp_path: Path) -> None:
        saved = tmp_path / "image.png"
        saved.write_bytes(b"image-bytes")
        with DataStore.open(tmp_path / "gflow.db") as store:
            recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
            project = ProjectInfo(project_id="flow-project-1", title="gflow-cli t2i")
            req = GenerateImageRequest(
                prompt="prompt text", aspect=Aspect.PORTRAIT, model=Model.NARWHAL
            )
            recorder.record_generated_images(
                profile_name="default",
                profile_dir=tmp_path / "profile_default",
                project=project,
                request=req,
                images=[_generated_image()],
                saved_paths=[saved],
                input_media_ids=[],
                operation_kind="t2i",
            )
            assert (
                recorder.is_media_recorded(
                    profile_name="default", flow_media_id="media-generated-1"
                )
                is True
            )

    def test_is_scoped_to_profile(self, tmp_path: Path) -> None:
        """Same flow_media_id recorded under a different profile must not count."""
        saved = tmp_path / "image.png"
        saved.write_bytes(b"image-bytes")
        with DataStore.open(tmp_path / "gflow.db") as store:
            recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
            project = ProjectInfo(project_id="flow-project-1", title="gflow-cli t2i")
            req = GenerateImageRequest(
                prompt="prompt text", aspect=Aspect.PORTRAIT, model=Model.NARWHAL
            )
            recorder.record_generated_images(
                profile_name="default",
                profile_dir=tmp_path / "profile_default",
                project=project,
                request=req,
                images=[_generated_image()],
                saved_paths=[saved],
                input_media_ids=[],
                operation_kind="t2i",
            )
            assert (
                recorder.is_media_recorded(
                    profile_name="other-profile", flow_media_id="media-generated-1"
                )
                is False
            )


def test_record_started_video_persists_tool_metadata(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        request = GenerateVideoRequest(
            prompt="expanded video prompt",
            mode=Mode.T2V,
            aspect=VideoAspect.PORTRAIT,
            original_prompt="a dog",
            tool=_applied_tool(),  # type: ignore[arg-type]
        )
        recorder.record_started_video(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            request=request,
            started=VideoStarted(media_id="m1", project_id="pv1", flow_operation_id="o1"),
        )
        tool = _op_tool_meta(store, "t2v")
        assert tool["name"] == "creative-director"
        assert tool["model"] == "gemini-2.5-flash"
        assert tool["params"] == {"style": "cinema"}
