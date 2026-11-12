from __future__ import annotations

from typing import TYPE_CHECKING

from gflow_cli.data.models import (
    OperationKind,
    OperationStatus,
)
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import MediaAttributionError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from structlog.typing import FilteringBoundLogger

    from gflow_cli.api.dto import GeneratedImage, ProjectInfo
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest
    from gflow_cli.config import Settings
    from gflow_cli.errors import DataIntegrityError

    # Both generation requests carry the tool-provenance fields the recorder reads.
    _ToolableRequest = GenerateImageRequest | GenerateVideoRequest


from gflow_cli.data._recorder_core import (
    _classify_failure as _classify_failure,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.data._recording_character import CharacterRecordingMixin
from gflow_cli.data._recording_image import ImageRecordingMixin
from gflow_cli.data._recording_video import VideoRecordingMixin


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


def record_undownloaded_images_safe(
    recorder: OperationRecorder | None,
    *,
    logger: FilteringBoundLogger,
    profile_name: str,
    profile_dir: Path,
    project: ProjectInfo,
    request: GenerateImageRequest,
    images: Sequence[GeneratedImage],
    saved_paths: Sequence[Path],
    operation_kind: str,
    input_media_ids: Sequence[str] = (),
    project_created: bool = True,
) -> list[str]:
    """Keep images Flow generated when their download fails (#1035); return their ids.

    Writes them as a STARTED operation with only the files written so far. The
    caller passes the returned ids as ``flow_media_ids`` to
    :func:`record_failed_operation_safe`, which turns that row FAILED — one
    operation, never a SUCCEEDED + FAILED pair. Runs inside the caller's
    ``except`` before it re-raises, so any fault here only warns (same
    double-fault guard as :func:`record_failed_operation_safe`).
    """
    from gflow_cli.storage import cloud_info_from_path

    media_ids = [image.media_name for image in images]
    logger.warning(
        "image.download_failed_after_generation",
        flow_media_ids=media_ids,
        flow_project_id=project.project_id,
        saved=len(saved_paths),
    )
    if recorder is None:
        return media_ids
    try:
        recorder.record_generated_images(
            profile_name=profile_name,
            profile_dir=profile_dir,
            project=project,
            project_created=project_created,
            request=request,
            images=list(images),
            saved_paths=list(saved_paths),
            cloud_storage_infos=[cloud_info_from_path(p) for p in saved_paths],
            input_media_ids=list(input_media_ids),
            operation_kind=operation_kind,
            status=OperationStatus.STARTED,
        )
    except Exception as record_exc:  # noqa: BLE001 — double-fault guard, see docstring
        logger.warning("failure_record_skipped", error=str(record_exc))
    return media_ids


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


class OperationRecorder(ImageRecordingMixin, VideoRecordingMixin, CharacterRecordingMixin):
    @classmethod
    def open(cls, settings: Settings) -> OperationRecorder:
        store = DataStore.open(settings.resolved_db_path())
        recorder = cls(DataRepository(store), prompt_mode=settings.history_prompts)
        recorder._owns_store = True
        return recorder
