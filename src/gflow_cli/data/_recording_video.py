"""``OperationRecorder`` part: scenes, extends, videos and failures (#1249 wave 2).

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
    SceneClipRecord,
    SceneRecord,
)
from gflow_cli.data.redaction import (
    prompt_fields,
    redact_error_detail,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.errors import MediaDownloadError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.scene import Scene
    from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStarted
    from gflow_cli.api.video_extend import ExtendStarted
    from gflow_cli.storage import CloudStorageInfo

    # Both generation requests carry the tool-provenance fields the recorder reads.
    _ToolableRequest = GenerateImageRequest | GenerateVideoRequest


from gflow_cli.data._recorder_core import (
    RecorderCore,
    _classify_failure,
    _file_bytes,
    _file_sha256,
    _new_id,
    _now_utc_iso,
)


class VideoRecordingMixin(RecorderCore):
    """Records scenes, extends, videos and failures."""

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

    def record_started_extend(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        project_id: str,
        aspect: str,
        started: ExtendStarted,
    ) -> None:
        """Persist an extend segment at SUBMIT time.

        Deliberately not routed through :meth:`record_started_video`: that takes a
        ``GenerateVideoRequest`` whose ``model`` is a ``VideoModel`` enum, while an
        extend key is resolved from the account's capability listing at runtime and
        is a plain string. Coercing one into the other would put a value in the
        catalog that the enum says cannot exist.

        Called before the ~2 minute poll because Flow bills on acceptance. A row
        written only after download would leave an interrupted run's paid media
        invisible to ``gflow data`` — and ``data sync`` cannot recover it, since
        sync reconciles rows that already exist rather than creating them.
        """
        repo = self.repository
        repo.upsert_profile(profile_name, profile_dir)
        repo.upsert_project(
            ProjectRecord(
                id=_new_id(),
                profile_name=profile_name,
                flow_project_id=project_id,
                title="gflow-cli video",
                source="generated",
            ),
        )
        repo.upsert_asset(
            AssetRecord(
                id=_new_id(),
                profile_name=profile_name,
                flow_project_id=project_id,
                flow_media_id=started.media_id,
                flow_workflow_id=started.workflow_id or None,
                flow_media_generation_id=None,
                kind=AssetKind.VIDEO,
                status="pending",
                model=started.model_key,
                aspect_ratio=aspect,
                width=None,
                height=None,
                duration_seconds=None,
                seed=None,
                # Cost only. The prompt is deliberately NOT stored here: a user
                # on history_prompts=redacted asked for prompts not to be kept,
                # and no other asset row carries one — every sibling write is
                # redact_metadata(...) or {}.
                metadata_json={"unit_cost": started.unit_cost},
            ),
        )

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

        # A start for a media id the catalog already holds adds nothing: the generation
        # is recorded. Re-writing the row would reset a completed clip to "pending" and
        # drop its metadata, and a second STARTED operation would never be resolved.
        # Before #898 this raised DataIntegrityError (a fresh id for a recorded media id
        # violates UNIQUE(profile_name, flow_media_id)).
        if repo.get_asset_by_flow_media_id(profile_name, started.media_id) is not None:
            return
        asset_id = _new_id()
        repo.upsert_asset(
            AssetRecord(
                id=asset_id,
                profile_name=profile_name,
                flow_project_id=started.project_id,
                flow_media_id=started.media_id,
                flow_workflow_id=started.workflow_id,
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
        metadata = self._generation_metadata(request)
        if metadata:
            repo.set_operation_metadata(op_id, metadata)

    def _insert_fallback_video_operation(
        self,
        *,
        repo: DataRepository,
        profile_name: str,
        flow_media_id: str,
        request: GenerateVideoRequest,
        result: VideoResult,
        terminal: tuple[OperationStatus, str | None, str | None] = (
            OperationStatus.SUCCEEDED,
            None,
            None,
        ),
    ) -> None:
        """Insert a terminal video operation when on_started failed or was skipped.

        ``terminal`` is ``(status, error_type, error_detail)`` — SUCCEEDED by
        default, FAILED/"generation-failed" when the poll completed with
        ``succeeded=False`` (#341).
        """
        pf, expanded_prompt = self._resolve_prompts(request)
        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name=profile_name,
                flow_project_id=result.project_id,
                command=f"video {request.mode.value}",
                mode=OperationKind(request.mode.value),
                status=terminal[0],
                flow_operation_id=result.flow_operation_id,
                flow_batch_id=None,
                prompt=pf.prompt,
                prompt_hash=pf.prompt_hash,
                prompt_redacted=pf.prompt_redacted,
                model=request.model.value if request.model is not None else None,
                aspect_ratio=request.aspect.value,
                error_type=terminal[1],
                error_detail=terminal[2],
                completed_at=_now_utc_iso(),
                expanded_prompt=expanded_prompt,
            ),
        )
        asset_lookup = repo.get_asset_by_flow_media_id(profile_name, flow_media_id)
        if asset_lookup is not None:
            repo.link_operation_asset(op_id, asset_lookup.id, OperationAssetRole.OUTPUT, 0)
        metadata = self._generation_metadata(request)
        if metadata:
            repo.set_operation_metadata(op_id, metadata)

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
                # Never clobber the id the start recorded with None (#898).
                flow_workflow_id=result.workflow_id
                or (existing_asset.flow_workflow_id if existing_asset is not None else None),
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

        # Update the STARTED operation for this asset to its terminal state.
        # #341 review finding: a poll that COMPLETES with succeeded=False (Flow
        # rejected/failed the render — failure_reasons set) is a failed
        # generation, not a success; recording it SUCCEEDED made the most
        # common real-world failure class invisible to `data list errors`.
        completed_at = _now_utc_iso()
        if result.status.succeeded:
            terminal = (OperationStatus.SUCCEEDED, None, None)
        else:
            reasons = (
                ", ".join(result.status.failure_reasons)
                or result.status.error_message
                or "unknown reason"
            )
            terminal = (
                OperationStatus.FAILED,
                "generation-failed",
                redact_error_detail(reasons),
            )
        op = repo.get_operation_for_output_asset(
            profile_name,
            flow_media_id,
            OperationKind(request.mode.value),
        )
        if op is not None:
            repo.update_operation_status(op.id, terminal[0], completed_at, terminal[1], terminal[2])
        else:
            # on_started may have failed — insert a fresh terminal operation
            self._insert_fallback_video_operation(
                repo=repo,
                profile_name=profile_name,
                flow_media_id=flow_media_id,
                request=request,
                result=result,
                terminal=terminal,
            )

        if result.local_path is not None or cloud_storage_info is not None:
            self._persist_completed_video_file(
                repo=repo,
                profile_name=profile_name,
                flow_media_id=flow_media_id,
                local_path=result.local_path,
                cloud_storage_info=cloud_storage_info,
            )

    def record_failed_operation(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        command: str,
        mode: OperationKind,
        exc: BaseException,
        request: _ToolableRequest | None = None,
        flow_project_id: str | None = None,
        flow_media_ids: Sequence[str] = (),
    ) -> None:
        """Persist a terminal FAILED operation for a generation error (#341).

        ``error_type`` is the last segment of the exception's RFC 9457
        ``problem_type`` URI (``waf-rejection``, ``content-policy``, ...) —
        the taxonomy already declared per :class:`~gflow_cli.errors.GFlowError`
        subclass, so no parallel slug map can drift. Non-``GFlowError``
        exceptions store the class name and a SHA-256 hash of ``str(exc)``
        (never the message itself — it may carry tokens; same privacy rule as
        ``observability.emit_unhandled_event``).

        ``flow_media_ids`` carries EVERY media id the failed call announced
        via ``on_started`` (a ``count>1`` video fires the callback per output).
        Each one's STARTED row is updated to FAILED — mirroring
        ``record_completed_video`` — and an already-FAILED row counts as
        recorded. Only when none of them resolved to a row is a fresh FAILED
        row inserted. SUCCEEDED rows are never touched, and the character saga
        (``services/character_create.py``) deliberately keeps its STARTED rows
        for resume — do not wire this into it.

        Callers must re-raise the original exception after recording; use
        :func:`record_failed_operation_safe` so a data-layer fault here can
        never mask the generation error.
        """
        error_type, error_detail = _classify_failure(exc)
        completed_at = _now_utc_iso()
        repo = self.repository
        repo.upsert_profile(profile_name, profile_dir)
        recorded = False
        for media_id in flow_media_ids:
            op = repo.get_operation_for_output_asset(profile_name, media_id, mode)
            if op is None:
                continue
            if op.status == OperationStatus.STARTED:
                repo.update_operation_status(
                    op.id, OperationStatus.FAILED, completed_at, error_type, error_detail
                )
                recorded = True
            elif op.status == OperationStatus.FAILED:
                recorded = True  # already terminal — do not duplicate
        if isinstance(exc, MediaDownloadError) and exc.media_id:
            # #896: Flow said DONE and billed; only the transfer failed. Without this the
            # asset reads "pending" forever and nothing distinguishes it from a failed
            # generation. Same vocabulary record_completed_video writes. Runs after the
            # operation write, which matters more if a busy store fails one of the two.
            repo.update_asset_status(
                profile_name, exc.media_id, "MEDIA_GENERATION_STATUS_SUCCESSFUL"
            )
        if recorded:
            return

        if request is not None:
            pf, expanded_prompt = self._resolve_prompts(request)
            model = request.model.value if request.model is not None else None
            aspect_ratio = request.aspect.value
        else:
            pf, expanded_prompt = prompt_fields(None, mode=self.prompt_mode), None
            model = None
            aspect_ratio = None

        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name=profile_name,
                flow_project_id=flow_project_id,
                command=command,
                mode=mode,
                status=OperationStatus.FAILED,
                flow_operation_id=None,
                flow_batch_id=None,
                prompt=pf.prompt,
                prompt_hash=pf.prompt_hash,
                prompt_redacted=pf.prompt_redacted,
                model=model,
                aspect_ratio=aspect_ratio,
                error_type=error_type,
                error_detail=error_detail,
                completed_at=completed_at,
                expanded_prompt=expanded_prompt,
            ),
        )
        # A FAILED row carrying entity_ids is the negative case the catalog was
        # missing (#402): an entity-attach that tripped the wire backstop is now
        # distinguishable from a run that never requested an entity at all.
        metadata = self._generation_metadata(request) if request is not None else {}
        if metadata:
            repo.set_operation_metadata(op_id, metadata)
