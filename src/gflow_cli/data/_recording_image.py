"""``OperationRecorder`` part: uploads and generated images (#1249 wave 2).

Moved out of ``recorder.py`` unchanged.
"""

# pyright: reportPrivateUsage=false
# The shared helpers are private to the data package; this module is one of its parts.

from __future__ import annotations

import mimetypes
from typing import TYPE_CHECKING

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
from gflow_cli.data.redaction import (
    redact_metadata,
)
from gflow_cli.data.repository import DataRepository

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest
    from gflow_cli.storage import CloudStorageInfo

    # Both generation requests carry the tool-provenance fields the recorder reads.
    _ToolableRequest = GenerateImageRequest | GenerateVideoRequest


from gflow_cli.data._recorder_core import (
    RecorderCore,
    _file_bytes,
    _file_sha256,
    _new_id,
    _now_utc_iso,
)


class ImageRecordingMixin(RecorderCore):
    """Records uploads and generated images."""

    def record_upload_image(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        project: ProjectInfo,
        asset: AssetInfo,
        image_path: Path,
        cloud_storage_info: CloudStorageInfo | None = None,
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
            ),
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
            ),
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
            ),
        )
        # Upload is synchronous from the recorder's POV: by the time we're here
        # the upload already succeeded, so completed_at = started_at = now.
        repo.update_operation_status(op_id, OperationStatus.SUCCEEDED, _now_utc_iso(), None, None)
        repo.link_operation_asset(op_id, asset_id, OperationAssetRole.OUTPUT, 0)

        repo.upsert_local_file(
            LocalFileRecord(
                id=_new_id(),
                profile_name=profile_name,
                asset_id=asset_id,
                path=image_path.resolve() if cloud_storage_info is None else None,
                media_type=media_type,
                bytes=_file_bytes(image_path) if cloud_storage_info is None else None,
                sha256=_file_sha256(image_path) if cloud_storage_info is None else None,
                storage_provider=cloud_storage_info.provider if cloud_storage_info else None,
                cloud_uri=cloud_storage_info.uri if cloud_storage_info else None,
            ),
        )

    def _persist_generated_image(
        self,
        *,
        repo: DataRepository,
        op_id: str,
        i: int,
        image: GeneratedImage,
        saved_path: Path | None,
        profile_name: str,
        flow_project_id: str,
        cloud_info: CloudStorageInfo | None,
    ) -> None:
        """Upsert one generated image asset + local-file row and link it to the operation.

        ``saved_path`` is ``None`` when Flow generated the image but its download
        failed (#1035): the asset is still recorded, with no local-file row.
        """
        asset_id = _new_id()
        width, height = image.dimensions
        # Persist the Flow-assigned display name (when present) so a generated
        # image can later be referenced by name — the picker's searchable label.
        metadata: dict[str, str] = {"fife_url": image.fife_url}
        # Flow captions can closely paraphrase the generation prompt. Respect
        # the same privacy boundary as prompt storage: redacted history never
        # persists the browser-searchable caption in plaintext.
        if image.display_name and self.prompt_mode == "store":
            metadata["display_name"] = image.display_name
        repo.upsert_asset(
            AssetRecord(
                id=asset_id,
                profile_name=profile_name,
                flow_project_id=flow_project_id,
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
                metadata_json=redact_metadata(metadata),
            ),
        )
        repo.link_operation_asset(op_id, asset_id, OperationAssetRole.OUTPUT, i)
        if saved_path is None:
            return
        is_cloud = cloud_info is not None
        repo.upsert_local_file(
            LocalFileRecord(
                id=_new_id(),
                profile_name=profile_name,
                asset_id=asset_id,
                path=saved_path.resolve() if not is_cloud else None,
                # The name, not str(): for a cloud path str() is the full URI.
                media_type=mimetypes.guess_type(saved_path.name)[0],
                bytes=_file_bytes(saved_path) if not is_cloud else None,
                sha256=_file_sha256(saved_path) if not is_cloud else None,
                storage_provider=cloud_info.provider if cloud_info else None,
                cloud_uri=cloud_info.uri if cloud_info else None,
            ),
        )

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
        cloud_storage_infos: list[CloudStorageInfo | None] | None = None,
        project_created: bool = True,
        status: OperationStatus = OperationStatus.SUCCEEDED,
    ) -> None:
        """Record a generation and its output images.

        ``status=STARTED`` is the #1035 download-failure path: Flow returned the
        images, but not every file was written. ``saved_paths`` then holds only the
        files written so far (a prefix, aligned by index with ``images``), and the
        caller's failure funnel turns this STARTED row into the one FAILED row
        (``record_failed_operation`` with ``flow_media_ids``).
        """
        repo = self.repository

        repo.upsert_profile(profile_name, profile_dir)
        # When generating into a pre-existing project (`--project`), `project.title`
        # is only a placeholder — DON'T overwrite the project's real, user-curated
        # title in the local history DB. Passing title=None lets upsert_project's
        # COALESCE preserve whatever title is already stored.
        repo.upsert_project(
            ProjectRecord(
                id=_new_id(),
                profile_name=profile_name,
                flow_project_id=project.project_id,
                title=project.title if project_created else None,
                source="generated",
            ),
        )

        pf, expanded_prompt = self._resolve_prompts(request)
        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name=profile_name,
                flow_project_id=project.project_id,
                command=f"image {operation_kind}",
                mode=OperationKind(operation_kind),
                status=status,
                flow_operation_id=None,
                flow_batch_id=None,
                prompt=pf.prompt,
                prompt_hash=pf.prompt_hash,
                prompt_redacted=pf.prompt_redacted,
                model=request.model.value,
                aspect_ratio=request.aspect.value,
                error_type=None,
                error_detail=None,
                expanded_prompt=expanded_prompt,
            ),
        )
        # Image generation is recorded AFTER all downloads complete, so the
        # operation is already terminal at insert time. Stamp completed_at so
        # downstream queries like "SELECT * FROM operations WHERE completed_at
        # IS NULL" don't surface successful runs.
        if status == OperationStatus.SUCCEEDED:
            repo.update_operation_status(op_id, status, _now_utc_iso(), None, None)
        metadata = self._generation_metadata(request)
        if metadata:
            repo.set_operation_metadata(op_id, metadata)

        # Link input assets (I2I seed images)
        for i, media_id in enumerate(input_media_ids):
            input_asset = repo.get_asset_by_flow_media_id(profile_name, media_id)
            if input_asset is not None:
                repo.link_operation_asset(op_id, input_asset.id, OperationAssetRole.INPUT, i)

        # Persist each output image
        # By index, never zip: a short `saved_paths` must not drop an image (#1035).
        for i, image in enumerate(images):
            saved_path = saved_paths[i] if i < len(saved_paths) else None
            cloud_info = (
                cloud_storage_infos[i]
                if cloud_storage_infos and i < len(cloud_storage_infos)
                else None
            )
            self._persist_generated_image(
                repo=repo,
                op_id=op_id,
                i=i,
                image=image,
                saved_path=saved_path,
                profile_name=profile_name,
                flow_project_id=project.project_id,
                cloud_info=cloud_info,
            )
