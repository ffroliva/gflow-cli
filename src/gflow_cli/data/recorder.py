from __future__ import annotations

import hashlib
import json
import mimetypes
import uuid
from datetime import UTC, datetime
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
    SceneClipRecord,
    SceneRecord,
)
from gflow_cli.data.redaction import PromptFields, PromptMode, prompt_fields, redact_metadata
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.scene import Scene
    from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStarted
    from gflow_cli.config import Settings
    from gflow_cli.storage import CloudStorageInfo
    from gflow_cli.tools.invocation import AppliedTool

    # Both generation requests carry the tool-provenance fields the recorder reads.
    _ToolableRequest = GenerateImageRequest | GenerateVideoRequest


def _new_id() -> str:
    return str(uuid.uuid4())


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _now_utc_iso() -> str:
    """UTC timestamp matching the format used elsewhere in the data layer."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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

    def _resolve_prompts(
        self,
        request: _ToolableRequest,
    ) -> tuple[PromptFields, str | None]:
        """Resolve the recorded prompt fields and the expansion-to-store.

        ``request.prompt`` is what was actually submitted to Flow. When
        ``request.original_prompt`` is set (i.e. a ``--tool`` rewrote the
        prompt), the user's *original* prompt is recorded as the operation
        prompt and the submitted prompt is persisted separately as
        ``expanded_prompt`` — withheld when ``history_prompts='redacted'``,
        exactly like the original prompt.

        Reading both off the request (rather than a separate ``original_prompt``
        kwarg) keeps the recorded original in lockstep with the submitted prompt
        — they can no longer drift apart at the call site (PR2 §8 / prior-review
        silent-misrecord hazard).
        """
        sent_prompt = request.prompt
        original_prompt = request.original_prompt
        recorded = original_prompt if original_prompt is not None else sent_prompt
        pf = prompt_fields(recorded, mode=self.prompt_mode)
        expanded = (
            sent_prompt if (original_prompt is not None and self.prompt_mode == "store") else None
        )
        return pf, expanded

    def _tool_metadata(self, tool: AppliedTool | None) -> dict[str, object] | None:
        """Build the redaction-aware ``metadata_json.tool`` payload for a
        generation operation, or ``None`` when no tool was applied.

        ``redact_metadata`` only redacts by key-name / sensitive-URL markers, so
        a free-text option (e.g. ``params.style``) would pass through verbatim.
        We therefore branch on :class:`PromptMode` here: in ``redacted`` mode we
        store only ``{name, version, params_hash, config_hash}`` — never the raw
        ``model``/``params`` — reusing :func:`prompt_fields`' sha256 for the
        params digest (council D7).
        """
        if tool is None:
            return None
        params = tool.params_dict()
        if self.prompt_mode == "redacted":
            params_blob = json.dumps(params, sort_keys=True)
            params_hash = prompt_fields(params_blob, mode="store").prompt_hash
            return {
                "name": tool.name,
                "version": tool.version,
                "params_hash": params_hash,
                "config_hash": tool.config_hash,
            }
        return {
            "name": tool.name,
            "version": tool.version,
            "model": tool.model,
            "params": params,
            "config_hash": tool.config_hash,
        }

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

    # ------------------------------------------------------------------
    # Generated images (T2I / I2I)
    # ------------------------------------------------------------------

    def _persist_generated_image(
        self,
        *,
        repo: DataRepository,
        op_id: str,
        i: int,
        image: GeneratedImage,
        saved_path: Path,
        profile_name: str,
        flow_project_id: str,
        cloud_info: CloudStorageInfo | None,
    ) -> None:
        """Upsert one generated image asset + local-file row and link it to the operation."""
        # Use the saved_path name for mime-type detection; for cloud paths
        # str(saved_path) returns the full URI but .name gives the filename.
        media_type = mimetypes.guess_type(saved_path.name)[0]
        asset_id = _new_id()
        width, height = image.dimensions
        # Persist the Flow-assigned display name (when present) so a generated
        # image can later be referenced by name — the picker's searchable label.
        metadata: dict[str, str] = {"fife_url": image.fife_url}
        if image.display_name:
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
        is_cloud = cloud_info is not None
        repo.upsert_local_file(
            LocalFileRecord(
                id=_new_id(),
                profile_name=profile_name,
                asset_id=asset_id,
                path=saved_path.resolve() if not is_cloud else None,
                media_type=media_type,
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
    ) -> None:
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
                expanded_prompt=expanded_prompt,
            ),
        )
        # Image generation is recorded AFTER all downloads complete, so the
        # operation is already terminal at insert time. Stamp completed_at so
        # downstream queries like "SELECT * FROM operations WHERE completed_at
        # IS NULL" don't surface successful runs.
        repo.update_operation_status(op_id, OperationStatus.SUCCEEDED, _now_utc_iso(), None, None)
        tool_meta = self._tool_metadata(request.tool)
        if tool_meta is not None:
            repo.set_operation_metadata(op_id, {"tool": tool_meta})

        # Link input assets (I2I seed images)
        for i, media_id in enumerate(input_media_ids):
            input_asset = repo.get_asset_by_flow_media_id(profile_name, media_id)
            if input_asset is not None:
                repo.link_operation_asset(op_id, input_asset.id, OperationAssetRole.INPUT, i)

        # Persist each output image
        for i, (image, saved_path) in enumerate(zip(images, saved_paths, strict=False)):
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

    # ------------------------------------------------------------------
    # Scenes
    # ------------------------------------------------------------------

    def record_scene(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        scene: Scene,
        operation_kind: OperationKind = OperationKind.SCENE_CREATE,
        source_workflow_ids: list[str] | None = None,
        source: str = "composed",
    ) -> str:
        """Persist a composed scene; returns the local scene row id (for a later
        :meth:`record_scene_output`). source_workflow_ids (submission order) is
        zipped by position onto the sorted instances; the source id is NOT
        recoverable from read-back alone."""
        repo = self.repository
        src_by_pos = source_workflow_ids or []
        repo.upsert_profile(profile_name, profile_dir)
        repo.upsert_project(
            ProjectRecord(
                id=_new_id(),
                profile_name=profile_name,
                flow_project_id=scene.project_id,
                title=None,
                source="generated",
            ),
        )
        total = sum((w.metadata.end_time - w.metadata.start_time) for w in scene.workflows)
        scene_row_id = _new_id()
        repo.upsert_scene(
            SceneRecord(
                id=scene_row_id,
                profile_name=profile_name,
                flow_project_id=scene.project_id,
                flow_scene_id=scene.scene_id,
                total_duration=total,
                source=source,
            ),
        )
        repo.replace_scene_clips(
            scene_row_id,
            [
                SceneClipRecord(
                    id=_new_id(),
                    scene_id=scene_row_id,
                    position=w.metadata.position,
                    flow_instance_workflow_id=w.workflow_id,
                    flow_source_workflow_id=(src_by_pos[idx] if idx < len(src_by_pos) else None),
                    flow_media_id=w.media_id,
                    start_time=w.metadata.start_time,
                    end_time=w.metadata.end_time,
                    total_duration=w.metadata.total_duration,
                )
                for idx, w in enumerate(scene.workflows)
            ],
        )
        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name=profile_name,
                flow_project_id=scene.project_id,
                command="scene create",
                mode=operation_kind,
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
        repo.update_operation_status(op_id, OperationStatus.SUCCEEDED, _now_utc_iso(), None, None)
        return scene_row_id

    def record_scene_output(self, *, scene_row_id: str, output_path: str) -> None:
        """Attach the rendered extended-video path to a scene already recorded
        by :meth:`record_scene`. Called after a successful server-side concat so
        a render failure never loses the compose record."""
        self.repository.set_scene_output(scene_row_id, output_path)

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
                ),
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
            ),
        )

        pf, expanded_prompt = self._resolve_prompts(request)
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
                expanded_prompt=expanded_prompt,
            ),
        )
        repo.link_operation_asset(op_id, asset_id, OperationAssetRole.OUTPUT, 0)
        tool_meta = self._tool_metadata(request.tool)
        if tool_meta is not None:
            repo.set_operation_metadata(op_id, {"tool": tool_meta})

    def _insert_fallback_video_operation(
        self,
        *,
        repo: DataRepository,
        profile_name: str,
        flow_media_id: str,
        request: GenerateVideoRequest,
        result: VideoResult,
    ) -> None:
        """Insert a completed video operation when on_started failed or was skipped."""
        pf, expanded_prompt = self._resolve_prompts(request)
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
                expanded_prompt=expanded_prompt,
            ),
        )
        asset_lookup = repo.get_asset_by_flow_media_id(profile_name, flow_media_id)
        if asset_lookup is not None:
            repo.link_operation_asset(op_id, asset_lookup.id, OperationAssetRole.OUTPUT, 0)
        tool_meta = self._tool_metadata(request.tool)
        if tool_meta is not None:
            repo.set_operation_metadata(op_id, {"tool": tool_meta})

    def _persist_completed_video_file(
        self,
        *,
        repo: DataRepository,
        profile_name: str,
        flow_media_id: str,
        local_path: Path | None,
        cloud_storage_info: CloudStorageInfo | None,
    ) -> None:
        """Upsert the local-file row for a downloaded (or cloud-stored) video."""
        asset_lookup = repo.get_asset_by_flow_media_id(profile_name, flow_media_id)
        if asset_lookup is None:
            return
        is_cloud = cloud_storage_info is not None
        media_type = (
            mimetypes.guess_type(local_path.name)[0] if local_path is not None else "video/mp4"
        )
        # Persist on-disk path/bytes/hash only for genuinely local files
        # (single-level conditionals so pyright narrows ``local_path``).
        resolved_path = local_path.resolve() if not is_cloud and local_path is not None else None
        file_bytes = _file_bytes(local_path) if not is_cloud and local_path is not None else None
        file_sha256 = _file_sha256(local_path) if not is_cloud and local_path is not None else None
        repo.upsert_local_file(
            LocalFileRecord(
                id=_new_id(),
                profile_name=profile_name,
                asset_id=asset_lookup.id,
                path=resolved_path,
                media_type=media_type,
                bytes=file_bytes,
                sha256=file_sha256,
                storage_provider=cloud_storage_info.provider if cloud_storage_info else None,
                cloud_uri=cloud_storage_info.uri if cloud_storage_info else None,
            ),
        )

    def record_completed_video(
        self,
        *,
        profile_name: str,
        _profile_dir: Path,
        request: GenerateVideoRequest,
        result: VideoResult,
        cloud_storage_info: CloudStorageInfo | None = None,
    ) -> None:
        # VideoResult carries status.media_id (the flow_media_id) and local_path

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
            ),
        )

        # Update the STARTED operation for this asset to SUCCEEDED
        completed_at = _now_utc_iso()
        op = repo.get_operation_for_output_asset(
            profile_name,
            flow_media_id,
            OperationKind(request.mode.value),
        )
        if op is not None:
            repo.update_operation_status(op.id, OperationStatus.SUCCEEDED, completed_at, None, None)
        else:
            # on_started may have failed — insert a fresh completed operation
            self._insert_fallback_video_operation(
                repo=repo,
                profile_name=profile_name,
                flow_media_id=flow_media_id,
                request=request,
                result=result,
            )

        if result.local_path is not None or cloud_storage_info is not None:
            self._persist_completed_video_file(
                repo=repo,
                profile_name=profile_name,
                flow_media_id=flow_media_id,
                local_path=result.local_path,
                cloud_storage_info=cloud_storage_info,
            )

    # ------------------------------------------------------------------
    # Character — started / completed (persist-before-spend saga)
    # ------------------------------------------------------------------

    def record_character_started(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        project_id: str,
        entity_id: str,
        name: str,
    ) -> str:
        """Insert an OperationRecord(mode=CHARACTER, status=STARTED) BEFORE any
        credited generation.  Stores ``entity_id`` and ``name`` in the new
        ``metadata_json`` column so the row is recoverable after a crash.

        Returns the operation row ``id`` for later update via
        :meth:`record_character_completed`.
        """
        repo = self.repository

        repo.upsert_profile(profile_name, profile_dir)
        repo.upsert_project(
            ProjectRecord(
                id=_new_id(),
                profile_name=profile_name,
                flow_project_id=project_id,
                title=None,
                source="generated",
            ),
        )

        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name=profile_name,
                flow_project_id=project_id,
                command="character create",
                mode=OperationKind.CHARACTER,
                status=OperationStatus.STARTED,
                flow_operation_id=entity_id,
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
        # Write entity_id + name into metadata_json immediately so a crash
        # before record_character_completed still leaves a recoverable row.
        repo.set_operation_metadata(op_id, {"entity_id": entity_id, "name": name})
        return op_id

    def record_character_partial(
        self,
        *,
        row_id: str,
        workflow_ids: list[str],
        primary_media_ids: list[str],
    ) -> None:
        """Merge newly-recorded workflow/media ids into a STARTED row.

        Called after each individual commit_workflow so that a crash between
        face-gen and body-gen leaves the row with the face ids already
        persisted — recovery can then skip the face slot.

        The row stays in STARTED status; only ``metadata_json`` is updated.
        """
        import json as _json

        repo = self.repository
        # Read current metadata, merge in new ids.
        row = repo.store.conn.execute(
            "SELECT metadata_json FROM operations WHERE id = ?",
            (row_id,),
        ).fetchone()
        meta: dict[str, object] = {}
        if row and row["metadata_json"]:
            try:
                meta = _json.loads(row["metadata_json"])
            except (ValueError, TypeError):
                meta = {}
        meta["workflow_ids"] = workflow_ids
        meta["primary_media_ids"] = primary_media_ids
        repo.set_operation_metadata(row_id, meta)

    def record_character_completed(
        self,
        *,
        row_id: str,
        workflow_ids: list[str],
        primary_media_ids: list[str],
        voice: str | None = None,
        personality: str | None = None,
        media_metadata: dict[str, object] | None = None,
        image_paths: list[str | None] | None = None,
    ) -> None:
        """Update the STARTED row to SUCCEEDED.

        * ``personality`` is routed through
          :func:`~gflow_cli.data.redaction.prompt_fields` so that in
          ``prompt_mode="redacted"`` mode no plaintext is stored.
        * ``media_metadata`` is routed through
          :func:`~gflow_cli.data.redaction.redact_metadata` so that signed URLs
          (``signature=`` / ``Expires=`` / ``fifeUrl``) are never persisted.
        * ``image_paths`` is the LOCAL on-disk path of each downloaded reference
          image (slot order, parallel to ``primary_media_ids``).  Each non-None
          path is persisted as an asset + local-file row so the character's
          images are queryable like generated images/videos.  These are always
          local file paths — never a signed CDN URL (scenario #16).
        """
        repo = self.repository
        pf = prompt_fields(personality, mode=self.prompt_mode)

        # Collect safe metadata (workflow/media ids + optional redacted fields)
        meta: dict[str, object] = {
            "workflow_ids": workflow_ids,
            "primary_media_ids": primary_media_ids,
        }
        if voice is not None:
            meta["voice"] = voice
        if media_metadata is not None:
            meta["media_metadata"] = redact_metadata(media_metadata)

        repo.update_operation_metadata(
            row_id,
            status=OperationStatus.SUCCEEDED,
            completed_at=_now_utc_iso(),
            prompt=pf.prompt,
            prompt_hash=pf.prompt_hash,
            prompt_redacted=pf.prompt_redacted,
            metadata_json=meta,
        )

        # Persist each downloaded reference image as an asset + local-file row.
        if image_paths:
            self._record_character_local_files(
                row_id=row_id,
                workflow_ids=workflow_ids,
                primary_media_ids=primary_media_ids,
                image_paths=image_paths,
            )

    def _record_character_local_files(
        self,
        *,
        row_id: str,
        workflow_ids: list[str],
        primary_media_ids: list[str],
        image_paths: list[str | None],
    ) -> None:
        """Upsert an asset + local-file row for each downloaded character image.

        Only local file paths are stored — never a signed CDN URL (scenario
        #16).  Slots whose path is ``None`` (not downloaded / recovered) are
        skipped.  The operation row's ``profile_name`` and ``flow_project_id``
        are looked up from the existing STARTED/SUCCEEDED row.
        """
        from pathlib import Path as _Path

        repo = self.repository
        op_row = repo.store.conn.execute(
            "SELECT profile_name, flow_project_id FROM operations WHERE id = ?",
            (row_id,),
        ).fetchone()
        if op_row is None:
            return
        profile_name = op_row["profile_name"]
        project_id = op_row["flow_project_id"]

        op_asset_index = 0
        for slot, path_str in enumerate(image_paths):
            if path_str is None:
                continue
            media_id = primary_media_ids[slot] if slot < len(primary_media_ids) else ""
            workflow_id = workflow_ids[slot] if slot < len(workflow_ids) else None
            path = _Path(path_str)
            media_type = mimetypes.guess_type(path.name)[0] or "image/png"
            asset_id = _new_id()
            repo.upsert_asset(
                AssetRecord(
                    id=asset_id,
                    profile_name=profile_name,
                    flow_project_id=project_id,
                    flow_media_id=media_id,
                    flow_workflow_id=workflow_id,
                    flow_media_generation_id=None,
                    kind=AssetKind.IMAGE,
                    status="ready",
                    model=None,
                    aspect_ratio=None,
                    width=None,
                    height=None,
                    duration_seconds=None,
                    seed=None,
                    metadata_json={},
                ),
            )
            repo.link_operation_asset(row_id, asset_id, OperationAssetRole.OUTPUT, op_asset_index)
            op_asset_index += 1
            repo.upsert_local_file(
                LocalFileRecord(
                    id=_new_id(),
                    profile_name=profile_name,
                    asset_id=asset_id,
                    path=path.resolve(),
                    media_type=media_type,
                    bytes=_file_bytes(path),
                    sha256=_file_sha256(path),
                    storage_provider=None,
                    cloud_uri=None,
                ),
            )
