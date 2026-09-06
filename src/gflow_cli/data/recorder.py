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
from gflow_cli.data.redaction import (
    PromptFields,
    PromptMode,
    prompt_fields,
    redact_error_detail,
    redact_metadata,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import GFlowError, MediaAttributionError
from gflow_cli.observability import exception_message_hash

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from structlog.typing import FilteringBoundLogger

    from gflow_cli.api.dto import AssetInfo, GeneratedImage, ProjectInfo
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.scene import Scene
    from gflow_cli.api.video import GenerateVideoRequest, VideoResult, VideoStarted
    from gflow_cli.api.video_extend import ExtendStarted
    from gflow_cli.config import Settings
    from gflow_cli.errors import DataIntegrityError
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


def _classify_failure(exc: BaseException) -> tuple[str, str | None]:
    """Map an exception to the persisted ``(error_type, error_detail)`` pair (#341).

    :class:`GFlowError`: ``error_type`` is the last segment of the per-class
    RFC 9457 ``problem_type`` URI (falling back to the class name for the base
    ``about:blank`` or a malformed URI); ``error_detail`` is the
    problem-details detail passed through :func:`redact_error_detail`.
    Anything else: class name plus ``sha256:<digest>`` of the message — never
    the message text (privacy rule shared with
    ``observability.emit_unhandled_event``, same digest for correlation).
    """
    if isinstance(exc, GFlowError):
        error_type = type(exc).__name__
        if exc.problem_type.startswith("http"):
            error_type = exc.problem_type.rsplit("/", 1)[-1] or error_type
        detail = exc.to_problem_details().get("detail", "")
        return error_type, redact_error_detail(detail) if detail else None
    return type(exc).__name__, f"sha256:{exception_message_hash(exc)}"


def record_failed_operation_safe(
    recorder: OperationRecorder | None,
    *,
    logger: FilteringBoundLogger,
    profile_name: str,
    profile_dir: Path,
    command: str,
    mode: OperationKind,
    exc: BaseException,
    request: _ToolableRequest | None = None,
    flow_project_id: str | None = None,
    flow_media_ids: Sequence[str] = (),
) -> None:
    """Best-effort wrapper around :meth:`OperationRecorder.record_failed_operation`.

    The FAILED write runs inside an ``except`` block that is about to re-raise
    the real generation error — ANY fault here must warn and step aside, never
    mask it. The broad catch is deliberate (double-fault guard): a
    ``DataStoreError``/``sqlite3.Error`` from the repository, or an
    ``AttributeError`` from a duck-typed recorder stand-in, must not replace
    the original exception the caller is re-raising. ``recorder`` may be
    ``None`` (recorder never opened) for caller symmetry.
    """
    if recorder is None:
        return
    try:
        recorder.record_failed_operation(
            profile_name=profile_name,
            profile_dir=profile_dir,
            command=command,
            mode=mode,
            exc=exc,
            request=request,
            flow_project_id=flow_project_id,
            flow_media_ids=flow_media_ids,
        )
    except Exception as record_exc:  # noqa: BLE001 — see docstring (double-fault guard)
        logger.warning("failure_record_skipped", error=str(record_exc))


def escalate_asset_collision(
    exc: DataIntegrityError,
    *,
    images: Sequence[GeneratedImage],
    saved_paths: Sequence[Path],
) -> None:
    """Escalate a ``record_generated_images`` write failure to
    :class:`~gflow_cli.errors.MediaAttributionError` when — and only when —
    it is the asset-collision constraint; otherwise return normally so the
    caller's generic ``DataStoreError`` warn-and-continue path still runs.

    ``record_generated_images`` performs several writes (``upsert_asset``,
    ``insert_operation``, ``link_operation_asset``, ...) via
    :class:`~gflow_cli.data.repository.DataRepository`, each tagging its own
    ``DataIntegrityError.route``. Only ``route == "data.upsert_asset"`` means
    the per-profile ``UNIQUE(profile_name, flow_media_id)`` constraint fired
    — i.e. the just-downloaded media may already exist in local history. Any
    OTHER ``DataIntegrityError`` route (e.g. ``data.insert_operation`` or
    ``data.link_operation_asset``) is an unrelated write failure and must NOT
    be mislabeled as a media collision (issue #281/#282 review finding (a)).

    Consolidated from three near-identical ``except DataIntegrityError``
    blocks previously duplicated across ``cli_image.py``, ``image_batch.py``,
    and ``worker/daemon.py`` (finding (b)). Call-site idiom, all three::

        except DataStoreError as exc:
            if isinstance(exc, DataIntegrityError):
                escalate_asset_collision(exc, images=images, saved_paths=saved_paths)
            _warn_persistence_failed_after_success(...)

    A bare ``except DataIntegrityError: ... except DataStoreError: ...``
    pair would NOT work here — a re-raise from inside the first clause
    propagates out of the whole try/except rather than falling into the
    sibling clause, so the "fall through to the warn path" behavior requires
    catching ``DataStoreError`` once and branching inside, as above.

    The colliding index is not recoverable from sqlite's bare UNIQUE-violation
    error, so the raised message names the full candidate set of
    ``flow_media_id``s and saved paths ("one of ...") rather than fingering
    ``images[0]`` / ``saved_paths[0]``, and notes that earlier images in this
    batch/operation may already have been recorded (issue #281/#282 review
    finding, honesty over false precision).
    """
    if exc.route != "data.upsert_asset":
        return
    media_ids = ", ".join(img.media_name for img in images) or "(none)"
    paths = ", ".join(str(p) for p in saved_paths) or "(none)"
    msg = (
        "one of the downloaded images in this batch/operation is suspect — "
        "it may be a pre-existing asset (#281). The colliding index cannot "
        f"be recovered from the database constraint: flow_media_id is one of "
        f"[{media_ids}], local_path is one of [{paths}]. Earlier images in "
        "this batch/operation may already have been recorded."
    )
    raise MediaAttributionError(msg, route="data.escalate_asset_collision") from exc


class OperationRecorder:
    repository: DataRepository
    prompt_mode: PromptMode

    def __init__(self, repository: DataRepository, *, prompt_mode: PromptMode) -> None:
        self.repository = repository
        self.prompt_mode = prompt_mode
        self._owns_store = False

    @classmethod
    def open(cls, settings: Settings) -> OperationRecorder:
        store = DataStore.open(settings.resolved_db_path())
        recorder = cls(DataRepository(store), prompt_mode=settings.history_prompts)
        recorder._owns_store = True
        return recorder

    def close(self) -> None:
        """Close the underlying store — but only when this recorder created it.

        A repository handed in via ``__init__`` (e.g. the worker daemon reusing
        its own long-lived ``DataStore``) is never owned here, so ``close()``
        on an injected recorder is a no-op; only :meth:`open` sets
        ``_owns_store``.
        """
        if self._owns_store:
            self.repository.store.close()

    def is_media_recorded(self, *, profile_name: str, flow_media_id: str) -> bool:
        """Return True if an asset with this ``flow_media_id`` already exists in
        local history for ``profile_name`` (issue #281 pre-download attribution
        guard). Delegates to ``repository.get_asset_by_flow_media_id`` — pure
        boolean convenience wrapper, no new query logic and no behaviour change
        to any existing method.
        """
        return self.repository.get_asset_by_flow_media_id(profile_name, flow_media_id) is not None

    def verify_media_attribution(
        self,
        *,
        profile_name: str,
        images: Sequence[GeneratedImage],
    ) -> None:
        """Pre-download attribution guard (issue #281): raise if the driver
        returned media that already exists in local history for this profile.

        Second defense layer after the agentic driver's own DOM-scrape ambiguity
        check (``agentic.py await_images``): even a transport that never hits that
        check can still hand back a ``flow_media_id`` already recorded for THIS
        profile, meaning it isn't new. Downloading and attributing it to the
        current generation would silently duplicate/misattribute history with a
        pre-existing asset (the 2026-07-10 production incident). Callers invoke
        this after generation returns and BEFORE downloading so nothing is
        fetched for an already-recorded id.

        Consolidated onto the recorder (issue #283) from three near-identical
        module-level copies previously duplicated across ``cli_image.py``,
        ``image_batch.py``, and ``worker/daemon.py`` — all three call sites
        already hold a recorder instance, so the guard belongs on the data
        layer rather than repeated per call site.

        Also checks for an INTRA-list duplicate: the classic transport can
        return the same ``media_name`` more than once for a single batch
        submission (the agentic transport cannot — its DOM-scrape ambiguity
        check in ``await_images`` already rules this out upstream). Two
        "different" images sharing one ``flow_media_id`` would otherwise both
        be attributed to the same asset row, silently losing one of them.
        This check runs BEFORE the local-history lookup since it is cheaper
        and needs no repository query.
        """
        seen: set[str] = set()
        intra_batch_duplicates: list[str] = []
        for img in images:
            if img.media_name in seen and img.media_name not in intra_batch_duplicates:
                intra_batch_duplicates.append(img.media_name)
            seen.add(img.media_name)
        if intra_batch_duplicates:
            msg = (
                "the driver returned the same media more than once in a single "
                "batch — wrong-media attribution (#281); nothing was downloaded: "
                f"{', '.join(intra_batch_duplicates)}"
            )
            raise MediaAttributionError(msg, route="data.verify_media_attribution")

        already_recorded = [
            img.media_name
            for img in images
            if self.is_media_recorded(profile_name=profile_name, flow_media_id=img.media_name)
        ]
        if already_recorded:
            msg = (
                "the driver returned media that already exists in local history — "
                "wrong-media attribution (#281); nothing was downloaded: "
                f"{', '.join(already_recorded)}"
            )
            raise MediaAttributionError(msg, route="data.verify_media_attribution")

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

    def _generation_metadata(
        self,
        request: _ToolableRequest,
    ) -> dict[str, object]:
        """Compose the full ``metadata_json`` payload for a generation operation.

        Built as ONE dict on purpose: :meth:`DataRepository.set_operation_metadata`
        replaces the whole column, so writing tool and entity provenance in two
        sequential calls would silently drop whichever went first.

        ``entity_ids`` / ``entity_names`` answer "which character produced this?"
        (issue #402) — without them a `--reference-entity` generation left no
        trace of the entity anywhere in the catalog, while `--ref` media were
        recorded in full. Order is preserved: it mirrors the attach order the
        transport sent. Both keys are stored verbatim even in ``redacted`` mode —
        they are Flow-side handles the user picked, not prompt text, and
        ``record_character_started`` already stores the same pair unredacted.
        """
        metadata: dict[str, object] = {}
        tool_meta = self._tool_metadata(request.tool)
        if tool_meta is not None:
            metadata["tool"] = tool_meta
        if request.reference_entities:
            metadata["entity_ids"] = list(request.reference_entities)
        if request.reference_entity_names:
            metadata["entity_names"] = list(request.reference_entity_names)
        return metadata

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
        metadata = self._generation_metadata(request)
        if metadata:
            repo.set_operation_metadata(op_id, metadata)

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

    # ------------------------------------------------------------------
    # Failed generations (#341)
    # ------------------------------------------------------------------

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
