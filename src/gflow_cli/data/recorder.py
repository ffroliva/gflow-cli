from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path

from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStarted
from gflow_cli.config import Settings
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
from gflow_cli.data.redaction import PromptMode, prompt_fields, redact_metadata
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore


def _new_id() -> str:
    return str(uuid.uuid4())


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _file_bytes(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


class OperationRecorder:
    repository: DataRepository
    prompt_mode: PromptMode

    def __init__(self, repository: DataRepository, *, prompt_mode: PromptMode) -> None:
        self.repository = repository
        self.prompt_mode = prompt_mode

    @classmethod
    def open(cls, settings: Settings) -> OperationRecorder:
        store = DataStore.open(settings.resolved_db_path())
        return cls(DataRepository(store), prompt_mode=settings.history_prompts)

    def close(self) -> None:
        self.repository.store.close()

    # ------------------------------------------------------------------
    # Image upload
    # ------------------------------------------------------------------

    def record_upload_image(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        project: ProjectInfo,
        asset: AssetInfo,
        image_path: Path,
    ) -> None:
        repo = self.repository

        repo.upsert_profile(profile_name, profile_dir)
        repo.upsert_project(
            ProjectRecord(
                id=_new_id(),
                profile_name=profile_name,
                flow_project_id=project.project_id,
                title=project.title,
                source="uploaded",
            )
        )

        asset_id = _new_id()
        media_type = mimetypes.guess_type(image_path.name)[0]
        repo.upsert_asset(
            AssetRecord(
                id=asset_id,
                profile_name=profile_name,
                flow_project_id=asset.project_id,
                flow_media_id=asset.name,
                flow_workflow_id=asset.workflow_id,
                flow_media_generation_id=None,
                kind=AssetKind.IMAGE,
                status="ready",
                model=None,
                aspect_ratio=None,
                width=asset.width or None,
                height=asset.height or None,
                duration_seconds=None,
                seed=None,
                metadata_json=redact_metadata({"display_name": asset.display_name}),
            )
        )

        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name=profile_name,
                flow_project_id=asset.project_id,
                command="image upload",
                mode=OperationKind.UPLOAD_IMAGE,
                status=OperationStatus.SUCCEEDED,
                flow_operation_id=None,
                flow_batch_id=None,
                prompt=None,
                prompt_hash=None,
                prompt_redacted=False,
                model=None,
                aspect_ratio=None,
                error_type=None,
                error_detail=None,
            )
        )
        repo.link_operation_asset(op_id, asset_id, OperationAssetRole.OUTPUT, 0)

        repo.upsert_local_file(
            LocalFileRecord(
                id=_new_id(),
                profile_name=profile_name,
                asset_id=asset_id,
                path=image_path.resolve(),
                media_type=media_type,
                bytes=_file_bytes(image_path),
                sha256=_file_sha256(image_path),
            )
        )

    # ------------------------------------------------------------------
    # Generated images (T2I / I2I)
    # ------------------------------------------------------------------

    def record_generated_images(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        project: ProjectInfo,
        request: GenerateImageRequest,
        images: list[GeneratedImage],
        saved_paths: list[Path],
        input_media_ids: list[str],
        operation_kind: str,
    ) -> None:
        repo = self.repository

        repo.upsert_profile(profile_name, profile_dir)
        repo.upsert_project(
            ProjectRecord(
                id=_new_id(),
                profile_name=profile_name,
                flow_project_id=project.project_id,
                title=project.title,
                source="generated",
            )
        )

        pf = prompt_fields(request.prompt, mode=self.prompt_mode)
        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name=profile_name,
                flow_project_id=project.project_id,
                command=f"image {operation_kind}",
                mode=OperationKind(operation_kind),
                status=OperationStatus.SUCCEEDED,
                flow_operation_id=None,
                flow_batch_id=None,
                prompt=pf.prompt,
                prompt_hash=pf.prompt_hash,
                prompt_redacted=pf.prompt_redacted,
                model=request.model.value,
                aspect_ratio=request.aspect.value,
                error_type=None,
                error_detail=None,
            )
        )

        # Link input assets (I2I seed images)
        for i, media_id in enumerate(input_media_ids):
            input_asset = repo.get_asset_by_flow_media_id(profile_name, media_id)
            if input_asset is not None:
                repo.link_operation_asset(op_id, input_asset.id, OperationAssetRole.INPUT, i)

        # Persist each output image
        for i, (image, saved_path) in enumerate(zip(images, saved_paths, strict=False)):
            media_type = mimetypes.guess_type(saved_path.name)[0]
            asset_id = _new_id()
            width, height = image.dimensions
            repo.upsert_asset(
                AssetRecord(
                    id=asset_id,
                    profile_name=profile_name,
                    flow_project_id=project.project_id,
                    flow_media_id=image.media_name,
                    flow_workflow_id=image.workflow_id,
                    flow_media_generation_id=image.media_generation_id,
                    kind=AssetKind.IMAGE,
                    status="ready",
                    model=image.model_name_type,
                    aspect_ratio=image.aspect_ratio,
                    width=width,
                    height=height,
                    duration_seconds=None,
                    seed=image.seed,
                    metadata_json=redact_metadata({"fife_url": image.fife_url}),
                )
            )
            repo.link_operation_asset(op_id, asset_id, OperationAssetRole.OUTPUT, i)
            repo.upsert_local_file(
                LocalFileRecord(
                    id=_new_id(),
                    profile_name=profile_name,
                    asset_id=asset_id,
                    path=saved_path.resolve(),
                    media_type=media_type,
                    bytes=_file_bytes(saved_path),
                    sha256=_file_sha256(saved_path),
                )
            )

    # ------------------------------------------------------------------
    # Video — started / completed
    # Note: Task 7 will introduce a proper "started video" DTO; for now
    # we accept primitive kwargs. Task 8 will wire this through callers.
    # ------------------------------------------------------------------

    def record_started_video(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        request: GenerateVideoRequest,
        started: VideoStarted,
    ) -> None:
        repo = self.repository

        repo.upsert_profile(profile_name, profile_dir)
        if started.project_id is not None:
            repo.upsert_project(
                ProjectRecord(
                    id=_new_id(),
                    profile_name=profile_name,
                    flow_project_id=started.project_id,
                    title="gflow-cli video",
                    source="generated",
                )
            )

        asset_id = _new_id()
        repo.upsert_asset(
            AssetRecord(
                id=asset_id,
                profile_name=profile_name,
                flow_project_id=started.project_id,
                flow_media_id=started.media_id,
                flow_workflow_id=None,
                flow_media_generation_id=None,
                kind=AssetKind.VIDEO,
                status="pending",
                model=request.model.value if request.model is not None else None,
                aspect_ratio=request.aspect.value,
                width=None,
                height=None,
                duration_seconds=float(request.duration) if request.duration is not None else None,
                seed=request.seed,
                metadata_json={},
            )
        )

        pf = prompt_fields(request.prompt, mode=self.prompt_mode)
        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name=profile_name,
                flow_project_id=started.project_id,
                command=f"video {request.mode.value}",
                mode=OperationKind(request.mode.value),
                status=OperationStatus.STARTED,
                flow_operation_id=started.flow_operation_id,
                flow_batch_id=None,
                prompt=pf.prompt,
                prompt_hash=pf.prompt_hash,
                prompt_redacted=pf.prompt_redacted,
                model=request.model.value if request.model is not None else None,
                aspect_ratio=request.aspect.value,
                error_type=None,
                error_detail=None,
            )
        )
        repo.link_operation_asset(op_id, asset_id, OperationAssetRole.OUTPUT, 0)

    def record_completed_video(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        request: GenerateVideoRequest,
        result: VideoResult,
    ) -> None:
        # VideoResult carries status.media_id (the flow_media_id) and local_path
        from datetime import UTC, datetime

        repo = self.repository
        flow_media_id = result.status.media_id

        # Upsert the asset (idempotent) — reuse existing id if already inserted by on_started
        existing_asset = repo.get_asset_by_flow_media_id(profile_name, flow_media_id)
        asset_id = existing_asset.id if existing_asset is not None else _new_id()
        repo.upsert_asset(
            AssetRecord(
                id=asset_id,
                profile_name=profile_name,
                flow_project_id=result.project_id,
                flow_media_id=flow_media_id,
                flow_workflow_id=None,
                flow_media_generation_id=None,
                kind=AssetKind.VIDEO,
                status=result.status.status,
                model=request.model.value if request.model is not None else None,
                aspect_ratio=request.aspect.value,
                width=None,
                height=None,
                duration_seconds=float(request.duration) if request.duration is not None else None,
                seed=request.seed,
                metadata_json={},
            )
        )

        # Update the STARTED operation for this asset to SUCCEEDED
        completed_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        op = repo.get_operation_for_output_asset(
            profile_name, flow_media_id, OperationKind(request.mode.value)
        )
        if op is not None:
            repo.update_operation_status(op.id, OperationStatus.SUCCEEDED, completed_at, None, None)
        else:
            # on_started may have failed — insert a fresh completed operation
            pf = prompt_fields(request.prompt, mode=self.prompt_mode)
            op_id = _new_id()
            repo.insert_operation(
                OperationRecord(
                    id=op_id,
                    profile_name=profile_name,
                    flow_project_id=result.project_id,
                    command=f"video {request.mode.value}",
                    mode=OperationKind(request.mode.value),
                    status=OperationStatus.SUCCEEDED,
                    flow_operation_id=result.flow_operation_id,
                    flow_batch_id=None,
                    prompt=pf.prompt,
                    prompt_hash=pf.prompt_hash,
                    prompt_redacted=pf.prompt_redacted,
                    model=request.model.value if request.model is not None else None,
                    aspect_ratio=request.aspect.value,
                    error_type=None,
                    error_detail=None,
                )
            )
            asset_lookup = repo.get_asset_by_flow_media_id(profile_name, flow_media_id)
            if asset_lookup is not None:
                repo.link_operation_asset(op_id, asset_lookup.id, OperationAssetRole.OUTPUT, 0)

        if result.local_path is not None:
            asset_lookup = repo.get_asset_by_flow_media_id(profile_name, flow_media_id)
            if asset_lookup is not None:
                media_type = mimetypes.guess_type(result.local_path.name)[0]
                repo.upsert_local_file(
                    LocalFileRecord(
                        id=_new_id(),
                        profile_name=profile_name,
                        asset_id=asset_lookup.id,
                        path=result.local_path.resolve(),
                        media_type=media_type,
                        bytes=_file_bytes(result.local_path),
                        sha256=_file_sha256(result.local_path),
                    )
                )
