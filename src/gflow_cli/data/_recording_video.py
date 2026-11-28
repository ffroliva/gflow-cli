"""``OperationRecorder`` part: scenes, extends, videos and failures (#1249 wave 2).

Moved out of ``recorder.py`` unchanged.
"""

# pyright: reportPrivateUsage=false
# The shared helpers are private to the data package; this module is one of its parts.

from __future__ import annotations

import mimetypes
from typing import TYPE_CHECKING

from gflow_cli.api.video import EditVideoRequest
from gflow_cli.data.models import (
    AssetKind,
    AssetLookup,
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
    from gflow_cli.api.transports.migrated_extend import ExtendOutcome
    from gflow_cli.api.video import (
        GenerateVideoRequest,
        VideoResult,
        VideoStarted,
        VideoStatus,
    )
    from gflow_cli.api.video_extend import ExtendStarted
    from gflow_cli.storage import AnyPath, CloudStorageInfo

    # All generation/edit requests carry the tool-provenance fields the recorder reads.
    _ToolableRequest = GenerateImageRequest | GenerateVideoRequest | EditVideoRequest
    _VideoRequest = GenerateVideoRequest | EditVideoRequest


from gflow_cli.data._recorder_core import (
    RecorderCore,
    _classify_failure,
    _file_bytes,
    _file_sha256,
    _new_id,
    _now_utc_iso,
)


def _video_req_fields(
    request: _VideoRequest,
) -> tuple[str, str | None, float | None, int | None]:
    """Return ``(mode_value, aspect_value, duration_seconds, seed)`` for video/edit requests."""
    if isinstance(request, EditVideoRequest):
        return (
            OperationKind.EDIT.value,
            request.aspect.value if request.aspect is not None else None,
            None,
            None,
        )
    return (
        request.mode.value,
        request.aspect.value,
        float(request.duration) if request.duration is not None else None,
        request.seed,
    )


def _is_in_place_edit(request: object, flow_media_id: str) -> bool:
    """Flow returned the edited clip under the source's own media id."""
    return isinstance(request, EditVideoRequest) and request.source_media_id == flow_media_id


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
        source_media_id: str | None = None,
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

        An extend that grows ``source_media_id`` in place (#1184) writes nothing here: the
        clip already exists, and :meth:`record_extended_in_place` notes it after success.
        """
        if started.media_id == source_media_id:
            return
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

    def record_extended_in_place(
        self,
        profile_name: str,
        media_id: str,
        workflow_id: str,
        model_key: str | None,
        local_path: Path | None = None,
        cloud_storage_info: CloudStorageInfo | None = None,
    ) -> bool:
        """Note a finished extend that grew ``media_id`` in place (#1184).

        Returns ``False`` for a clip the catalog does not hold, which is left unrecorded
        here; :meth:`record_migrated_extend` writes a row for such a clip instead."""
        if not self.repository.note_asset_extended(
            profile_name, media_id, model_key=model_key, workflow_id=workflow_id
        ):
            return False
        if local_path is not None or cloud_storage_info is not None:
            if cloud_storage_info is None and local_path is not None:
                from gflow_cli.storage import cloud_info_from_path

                cloud_storage_info = cloud_info_from_path(local_path)
            self._persist_completed_video_file(
                repo=self.repository,
                profile_name=profile_name,
                flow_media_id=media_id,
                local_path=local_path,
                cloud_storage_info=cloud_storage_info,
                supersede_existing=True,
            )
        return True

    def record_migrated_extend(  # noqa: PLR0913
        self,
        target: AnyPath | None,
        *,
        profile_name: str,
        profile_dir: Path | None,
        media_id: str,
        project_id: str | None,
        outcome: ExtendOutcome,
    ) -> None:
        """Catalog one finished flow.google.com extend of ``media_id`` (CLI and worker).

        An extend that grew the source in place (#1184) updates its row and file; a source
        the catalog does not hold yet (made in Flow's UI, or another machine) gets a row
        first, so the extended file is listed. An extend that made a separate clip is
        recorded as a completed (or failed) video of its own.
        """
        from pathlib import Path as _Path

        from gflow_cli.api.video import VideoResult, VideoStatus
        from gflow_cli.storage import cloud_info_from_path

        record = outcome.record
        in_place = record.media_id == media_id
        if in_place and target is None:
            # The extend did not finish, but the source is a finished clip: a row written
            # pending at submit for a clip uploaded in this run must not stay pending.
            self.repository.settle_pending_asset(
                profile_name, media_id, "MEDIA_GENERATION_STATUS_SUCCESSFUL"
            )
            return
        cloud_info = cloud_info_from_path(target) if target is not None else None
        local_path = target if isinstance(target, _Path) else None
        if in_place and self.record_extended_in_place(
            profile_name,
            media_id,
            record.workflow_id,
            outcome.model_key,
            local_path=local_path,
            cloud_storage_info=cloud_info,
        ):
            return
        succeeded = target is not None or record.is_done
        self.record_completed_video(
            profile_name=profile_name,
            _profile_dir=profile_dir or _Path(),
            result=VideoResult(
                status=VideoStatus(
                    media_id=record.media_id,
                    status="MEDIA_GENERATION_STATUS_SUCCESSFUL"
                    if succeeded
                    else "MEDIA_GENERATION_STATUS_FAILED",
                ),
                local_path=local_path,
                project_id=project_id,
                workflow_id=record.workflow_id,
            ),
            cloud_storage_info=cloud_info,
            model=outcome.model_key,
            aspect_ratio=outcome.aspect,
        )
        if in_place:
            self.repository.note_asset_extended(
                profile_name, media_id, model_key=outcome.model_key, workflow_id=record.workflow_id
            )

    def record_started_video(
        self,
        *,
        profile_name: str,
        profile_dir: Path,
        request: _VideoRequest,
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
        mode_value, aspect_value, duration_seconds, seed = _video_req_fields(request)
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
                aspect_ratio=aspect_value,
                width=None,
                height=None,
                duration_seconds=duration_seconds,
                seed=seed,
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
                command=f"video {mode_value}",
                mode=OperationKind(mode_value),
                status=OperationStatus.STARTED,
                flow_operation_id=started.flow_operation_id,
                flow_batch_id=None,
                prompt=pf.prompt,
                prompt_hash=pf.prompt_hash,
                prompt_redacted=pf.prompt_redacted,
                model=request.model.value if request.model is not None else None,
                aspect_ratio=aspect_value,
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
        request: _VideoRequest,
        result: VideoResult,
        terminal: tuple[OperationStatus, str | None, str | None] = (
            OperationStatus.SUCCEEDED,
            None,
            None,
        ),
        asset_id: str | None = None,
    ) -> None:
        """Insert a terminal video operation when on_started failed or was skipped.

        ``terminal`` is ``(status, error_type, error_detail)`` — SUCCEEDED by
        default, FAILED/"generation-failed" when the poll completed with
        ``succeeded=False`` (#341).
        """
        mode_value, aspect_value, _, _ = _video_req_fields(request)
        pf, expanded_prompt = self._resolve_prompts(request)
        op_id = _new_id()
        repo.insert_operation(
            OperationRecord(
                id=op_id,
                profile_name=profile_name,
                flow_project_id=result.project_id,
                command=f"video {mode_value}",
                mode=OperationKind(mode_value),
                status=terminal[0],
                flow_operation_id=result.flow_operation_id,
                flow_batch_id=None,
                prompt=pf.prompt,
                prompt_hash=pf.prompt_hash,
                prompt_redacted=pf.prompt_redacted,
                model=request.model.value if request.model is not None else None,
                aspect_ratio=aspect_value,
                error_type=terminal[1],
                error_detail=terminal[2],
                completed_at=_now_utc_iso(),
                expanded_prompt=expanded_prompt,
            ),
        )
        if asset_id is None:  # a caller that has not just written the asset row
            asset_lookup = repo.get_asset_by_flow_media_id(profile_name, flow_media_id)
            asset_id = asset_lookup.id if asset_lookup is not None else None
        if asset_id is not None:
            repo.link_operation_asset(op_id, asset_id, OperationAssetRole.OUTPUT, 0)
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
        supersede_existing: bool = False,
        asset_id: str | None = None,
    ) -> None:
        """Upsert the local-file row for a downloaded (or cloud-stored) video.

        ``asset_id`` is the row the caller has just written; without it the row is read.
        """
        if asset_id is None:
            asset_lookup = repo.get_asset_by_flow_media_id(profile_name, flow_media_id)
            if asset_lookup is None:
                return
            asset_id = asset_lookup.id
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
                asset_id=asset_id,
                path=resolved_path,
                media_type=media_type,
                bytes=file_bytes,
                sha256=file_sha256,
                storage_provider=cloud_storage_info.provider if cloud_storage_info else None,
                cloud_uri=cloud_storage_info.uri if cloud_storage_info else None,
            ),
            supersede_existing=supersede_existing,
        )

    def _resolve_video_terminal_outcome(
        self,
        status: VideoStatus,
    ) -> tuple[OperationStatus, str | None, str | None]:
        """Map a VideoStatus to an operation terminal state and failure detail (#341)."""
        if status.succeeded:
            return (OperationStatus.SUCCEEDED, None, None)
        reasons = ", ".join(status.failure_reasons) or status.error_message or "unknown reason"
        return (
            OperationStatus.FAILED,
            "generation-failed",
            redact_error_detail(reasons),
        )

    def _build_completed_video_asset(
        self,
        *,
        profile_name: str,
        flow_media_id: str,
        result: VideoResult,
        existing_asset: AssetLookup | None,
        request: _VideoRequest | None,
        model: str | None,
        aspect_ratio: str | None,
    ) -> AssetRecord:
        """Build the AssetRecord for a newly completed video generation."""
        if existing_asset is not None:
            asset_id = existing_asset.id
            project_id = result.project_id or existing_asset.flow_project_id
            workflow_id = result.workflow_id or existing_asset.flow_workflow_id
            metadata = existing_asset.metadata_json or {}
        else:
            asset_id = _new_id()
            # An empty id is stored as NULL, never as "" (IS NULL checks rely on it).
            project_id = result.project_id or None
            workflow_id = result.workflow_id or None
            metadata = {}
        if result.transport is not None:
            metadata = {**metadata, "transport": result.transport}
        if result.visibility is not None:  # the path the run took, when the client saw it
            metadata = {
                **metadata,
                "route": result.route,
                "visibility": result.visibility,
                "retried": result.retried,
            }

        if request is not None:
            _, aspect, duration, seed = _video_req_fields(request)
            model_value = request.model.value if request.model is not None else model
            if aspect is None:
                aspect = aspect_ratio
        else:
            model_value = model
            aspect = aspect_ratio or "16:9"
            duration = None
            seed = None

        return AssetRecord(
            id=asset_id,
            profile_name=profile_name,
            flow_project_id=project_id,
            flow_media_id=flow_media_id,
            # Never clobber the id the start recorded with None (#898).
            flow_workflow_id=workflow_id,
            flow_media_generation_id=None,
            kind=AssetKind.VIDEO,
            status=result.status.status,
            model=model_value,
            aspect_ratio=aspect,
            width=None,
            height=None,
            duration_seconds=duration,
            seed=seed,
            metadata_json=metadata,
        )

    def _persist_completed_video_asset(
        self,
        *,
        repo: DataRepository,
        profile_name: str,
        flow_media_id: str,
        result: VideoResult,
        request: _VideoRequest | None,
        model: str | None,
        aspect_ratio: str | None,
    ) -> str:
        """Upsert the completed video asset or update status for extend in-place; its id."""
        existing_asset = repo.get_asset_by_flow_media_id(profile_name, flow_media_id)
        # An edit Flow wrote in place is the source clip's row: its aspect, length and seed
        # stay as recorded (the edit request carries none of them); only the status moves.
        if existing_asset is not None and (
            request is None or _is_in_place_edit(request, flow_media_id)
        ):
            # A failed in-place edit leaves the source clip as it was (as an extend does).
            if request is None or result.status.succeeded:
                repo.update_asset_status(
                    profile_name,
                    flow_media_id,
                    result.status.status,
                    workflow_id=result.workflow_id,
                )
            return existing_asset.id

        record = self._build_completed_video_asset(
            profile_name=profile_name,
            flow_media_id=flow_media_id,
            result=result,
            existing_asset=existing_asset,
            request=request,
            model=model,
            aspect_ratio=aspect_ratio,
        )
        repo.upsert_asset(record)
        return record.id

    def _persist_completed_video_operation(
        self,
        *,
        repo: DataRepository,
        profile_name: str,
        flow_media_id: str,
        request: _VideoRequest,
        result: VideoResult,
        asset_id: str,
    ) -> None:
        """Update the STARTED operation to terminal SUCCEEDED/FAILED, or insert fallback."""
        terminal = self._resolve_video_terminal_outcome(result.status)
        completed_at = _now_utc_iso()
        mode_value, _, _, _ = _video_req_fields(request)
        op = repo.get_operation_for_output_asset(
            profile_name,
            flow_media_id,
            OperationKind(mode_value),
        )
        # Only this run's STARTED row: an in-place edit shares its output asset with every
        # earlier edit of the clip, whose terminal rows are history, never to be rewritten.
        if op is not None and (
            op.status == OperationStatus.STARTED or not _is_in_place_edit(request, flow_media_id)
        ):
            repo.update_operation_status(op.id, terminal[0], completed_at, terminal[1], terminal[2])
            return

        # on_started may have failed (or an in-place edit has no row of its own) — insert
        self._insert_fallback_video_operation(
            repo=repo,
            profile_name=profile_name,
            flow_media_id=flow_media_id,
            request=request,
            result=result,
            terminal=terminal,
            asset_id=asset_id,
        )

    def record_completed_video(
        self,
        *,
        profile_name: str,
        _profile_dir: Path,
        request: _VideoRequest | None = None,
        result: VideoResult,
        cloud_storage_info: CloudStorageInfo | None = None,
        model: str | None = None,
        aspect_ratio: str | None = None,
    ) -> None:
        # VideoResult carries status.media_id (the flow_media_id) and local_path
        repo = self.repository
        flow_media_id = result.status.media_id

        # One read of the asset row per completion: its id is passed down, not re-read.
        asset_id = self._persist_completed_video_asset(
            repo=repo,
            profile_name=profile_name,
            flow_media_id=flow_media_id,
            result=result,
            request=request,
            model=model,
            aspect_ratio=aspect_ratio,
        )

        if request is not None:
            self._persist_completed_video_operation(
                repo=repo,
                profile_name=profile_name,
                flow_media_id=flow_media_id,
                request=request,
                result=result,
                asset_id=asset_id,
            )

        if result.local_path is not None or cloud_storage_info is not None:
            in_place_edit = request is not None and _is_in_place_edit(request, flow_media_id)
            self._persist_completed_video_file(
                repo=repo,
                profile_name=profile_name,
                flow_media_id=flow_media_id,
                local_path=result.local_path,
                cloud_storage_info=cloud_storage_info,
                supersede_existing=in_place_edit,
                asset_id=asset_id,
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
            elif op.status == OperationStatus.FAILED and not _is_in_place_edit(request, media_id):
                # Already terminal — do not duplicate. An in-place edit's FAILED row may be
                # an earlier edit of the same clip, so this run's failure is still written.
                recorded = True
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
            aspect_ratio = request.aspect.value if request.aspect is not None else None
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
