"""``OperationRecorder`` part: character creation (#1249 wave 2).

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
    prompt_fields,
    redact_metadata,
)

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest

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


class CharacterRecordingMixin(RecorderCore):
    """Records character creation."""

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

        # Collect safe metadata (workflow/media ids + optional redacted fields). The
        # write replaces the column, so the STARTED row's identity is carried over (#737).
        started = repo.get_operation_metadata(row_id)
        meta: dict[str, object] = {
            **{k: started[k] for k in ("entity_id", "name") if k in started},
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

    def record_character_abandoned(self, *, row_id: str, exc: BaseException) -> None:
        """Flip a zero-progress character STARTED row to FAILED (#703).

        Only the saga's rollback path calls this: the run failed before the
        first slot committed, so the row records no workflow id and buys no
        resume.  Leaving it STARTED keeps ``find_incomplete_character``
        pointing at an entity the saga has just deleted.  Classification goes
        through the same ``_classify_failure`` taxonomy as every other FAILED
        row, so nothing parallel can drift.
        """
        error_type, error_detail = _classify_failure(exc)
        self.repository.update_operation_status(
            row_id, OperationStatus.FAILED, _now_utc_iso(), error_type, error_detail
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
            # Idempotent on the (profile_name, flow_media_id) business key: under the
            # agentic cohort the classic slot-add control is absent, so two slots can
            # report the SAME flow_media_id. Reuse the existing asset id (mirrors
            # record_completed_video) so upsert_asset UPDATEs instead of violating
            # UNIQUE(profile_name, flow_media_id). See spike 2026-07-09.
            existing_asset = (
                repo.get_asset_by_flow_media_id(profile_name, media_id) if media_id else None
            )
            asset_id = existing_asset.id if existing_asset is not None else _new_id()
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
