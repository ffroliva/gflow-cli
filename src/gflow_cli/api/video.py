"""Value objects for video generation.

This module is pure — no I/O. The video transport drives Flow's editor UI;
Flow's own JavaScript builds and sends the generate request, so this module
no longer carries HTTP body builders (the 401-dead HTTP video path was
retired — see the Phase A plan).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast


class Mode(StrEnum):
    T2V = "t2v"
    I2V = "i2v"
    R2V = "r2v"


class Tier(StrEnum):
    FAST = "fast"
    QUALITY = "quality"


class VideoModel(StrEnum):
    """Flow video model, as exposed in the editor's model picker.

    Verified live (flow-editor-map.json): the picker offers exactly these five.
    Only ``OMNI_FLASH`` exposes a 10s duration; the four ``VEO_3_1_*`` models
    cap at 8s. The selector for each lives in the transport layer (this module
    is pure — no DOM knowledge).
    """

    OMNI_FLASH = "omni_flash"
    VEO_3_1_LITE = "veo_3_1_lite"
    VEO_3_1_FAST = "veo_3_1_fast"
    VEO_3_1_QUALITY = "veo_3_1_quality"
    VEO_3_1_LITE_LOWER_PRIORITY = "veo_3_1_lite_lower_priority"

    @classmethod
    def from_cli(cls, value: str | None) -> VideoModel | None:
        """Map a friendly CLI alias to the model. ``None`` -> ``None`` (use
        Flow's UI default — the picker is not touched)."""
        if value is None:
            return None
        key = value.strip().lower().replace("-", "_").replace(" ", "_")
        mapping = {
            "omni_flash": cls.OMNI_FLASH,
            "omni": cls.OMNI_FLASH,
            "flash": cls.OMNI_FLASH,
            "veo_3_1_lite": cls.VEO_3_1_LITE,
            "veo_lite": cls.VEO_3_1_LITE,
            "lite": cls.VEO_3_1_LITE,
            "veo_3_1_fast": cls.VEO_3_1_FAST,
            "veo_fast": cls.VEO_3_1_FAST,
            "fast": cls.VEO_3_1_FAST,
            "veo_3_1_quality": cls.VEO_3_1_QUALITY,
            "veo_quality": cls.VEO_3_1_QUALITY,
            "quality": cls.VEO_3_1_QUALITY,
            "veo_3_1_lite_lower_priority": cls.VEO_3_1_LITE_LOWER_PRIORITY,
            "veo_lite_lp": cls.VEO_3_1_LITE_LOWER_PRIORITY,
            "lite_lp": cls.VEO_3_1_LITE_LOWER_PRIORITY,
            "lower_priority": cls.VEO_3_1_LITE_LOWER_PRIORITY,
        }
        if key not in mapping:
            raise ValueError(
                f"Unknown video model {value!r}; choose from "
                f"{sorted({m.value for m in cls})} or aliases {sorted(mapping)}"
            )
        return mapping[key]


class Aspect(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SQUARE = "square"

    def wire(self) -> str:
        return f"VIDEO_ASPECT_RATIO_{self.value.upper()}"

    @classmethod
    def from_cli(cls, value: str) -> Aspect:
        mapping = {"9:16": cls.PORTRAIT, "16:9": cls.LANDSCAPE, "1:1": cls.SQUARE}
        if value not in mapping:
            raise ValueError(f"Unsupported aspect ratio {value!r}; choose from {sorted(mapping)}")
        return mapping[value]


# Flow's R2V reference cap is MODEL-DEPENDENT (live-verified): omni_flash allows
# 7 ("Maximum image ingredients reached (7 allowed)"), the veo_3_1_* models allow
# 3. A veo request with >3 refs uploads all but the generate request silently
# keeps only 3. MAX_REFERENCE_IMAGES is the absolute ceiling (omni); the
# model-aware check below enforces the per-model limit when the model is known.
OMNI_REFERENCE_CAP = 7
VEO_REFERENCE_CAP = 3
MAX_REFERENCE_IMAGES = OMNI_REFERENCE_CAP


@dataclass(frozen=True)
class GenerateVideoRequest:
    """Inputs for ONE video generation. Mode is explicit; image inputs are
    local file paths the transport attaches through Flow's catalog UI.

    `__post_init__` validates STRUCTURE only — it does not check that image
    paths exist on disk (that is I/O; this module is pure). Path existence is
    validated by the transport at the boundary.
    """

    prompt: str
    mode: Mode = Mode.T2V
    aspect: Aspect = Aspect.PORTRAIT
    tier: Tier = Tier.FAST  # meaningful for T2V only — I2V/R2V model keys are fixed
    model: VideoModel | None = None  # None -> Flow UI default (picker untouched)
    duration: int | None = None  # seconds: 4/6/8 (or 10, omni_flash only); None -> default
    count: int = 1  # 1-4 outputs; >1 multiplies credit cost
    seed: int | None = None
    start_image: Path | None = None  # I2V
    end_image: Path | None = None  # I2V (optional)
    reference_images: tuple[Path, ...] = ()  # R2V

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if self.duration is not None and self.duration not in (4, 6, 8, 10):
            raise ValueError(f"duration must be one of 4/6/8/10 seconds, got {self.duration}")
        if (
            self.duration == 10
            and self.model is not None
            and self.model is not VideoModel.OMNI_FLASH
        ):
            raise ValueError(
                f"10s duration is only available for the omni_flash model; "
                f"{self.model.value} caps at 8s"
            )
        if not (1 <= self.count <= 4):
            raise ValueError(f"count must be 1-4, got {self.count}")
        if self.mode is Mode.T2V and (self.start_image or self.end_image or self.reference_images):
            raise ValueError("T2V request must not carry image inputs")
        if self.mode is Mode.I2V:
            if self.start_image is None:
                raise ValueError("I2V request requires start_image")
            if self.reference_images:
                raise ValueError("I2V request must not carry reference_images")
        if self.mode is Mode.R2V:
            if not self.reference_images:
                raise ValueError("R2V request requires at least one reference image")
            if self.start_image or self.end_image:
                raise ValueError("R2V request must not carry start/end images")
        if len(self.reference_images) > MAX_REFERENCE_IMAGES:
            raise ValueError(f"at most {MAX_REFERENCE_IMAGES} reference images")
        # Per-model reference cap: omni_flash=7, veo_3_1_*=3 (live-verified). When
        # the model is None (Flow UI default) we can't know it — leave the ceiling.
        if self.mode is Mode.R2V and self.model is not None:
            cap = OMNI_REFERENCE_CAP if self.model is VideoModel.OMNI_FLASH else VEO_REFERENCE_CAP
            if len(self.reference_images) > cap:
                raise ValueError(
                    f"{self.model.value} allows at most {cap} reference images; "
                    f"got {len(self.reference_images)} (omni_flash allows {OMNI_REFERENCE_CAP})"
                )
        if self.seed is not None and not (0 <= self.seed <= 2**31 - 1):
            raise ValueError("seed out of range")


@dataclass(frozen=True)
class VideoStatus:
    """Terminal-or-not status of one in-flight video generation."""

    media_id: str
    status: str  # a MEDIA_GENERATION_STATUS_* wire value
    failure_reasons: tuple[str, ...] = ()
    error_message: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            "MEDIA_GENERATION_STATUS_SUCCESSFUL",
            "MEDIA_GENERATION_STATUS_FAILED",
        }

    @property
    def succeeded(self) -> bool:
        return self.status == "MEDIA_GENERATION_STATUS_SUCCESSFUL"


@dataclass(frozen=True)
class VideoStarted:
    """Fired as soon as a media_id/project_id/operation_id are known, BEFORE
    polling completes — allows a recorder to insert a STARTED row even if the
    long poll later fails.
    """

    media_id: str
    project_id: str | None = None
    flow_operation_id: str | None = None


@dataclass(frozen=True)
class VideoResult:
    """Return value of :meth:`generate_video` after Phase B download wiring.

    ``local_path`` is ``None`` when ``download=False`` was passed, or when
    the generation failed — callers should check ``status.succeeded`` first.

    ``project_id`` and ``flow_operation_id`` are populated by the transport
    when available, for use by the data-layer recorder (Task 8).
    """

    status: VideoStatus
    local_path: Path | None
    project_id: str | None = None
    flow_operation_id: str | None = None


# Callback type: invoked by the transport the moment a media_id becomes known,
# before polling completes. May be sync or async.
VideoStartedCallback = Callable[[VideoStarted], Awaitable[None] | None]


def operation_name_from_generate_response(response_json: dict[str, Any]) -> str | None:
    """Return the operation name from ``operations[0].operation.name`` in a
    batchAsyncGenerateVideo* response, or None if absent.

    The T2V response body carries both ``media[0].name`` AND
    ``operations[0].operation.name``. The spec stores them SEPARATELY even when
    they currently happen to match — use :func:`media_name_from_generate_response`
    for the media id and this function for the operation id.
    """
    operations = response_json.get("operations")
    if not isinstance(operations, list) or not operations:
        return None
    first: dict[str, Any] = cast("dict[str, Any]", operations[0])
    operation: dict[str, Any] | None = cast(
        "dict[str, Any] | None", first.get("operation")
    )
    if not isinstance(operation, dict):
        return None
    name_val: str | None = cast("str | None", operation.get("name"))
    return name_val if name_val is not None else None


def media_name_from_generate_response(response_json: dict[str, Any]) -> str:
    """Return `media[0].name` from a batchAsyncGenerateVideo* response.

    Shapes: captures 02 (T2V), 08 (I2V), 09 (R2V). The T2V response also
    carries a top-level `operations[]`; this parser deliberately reads
    `media[0].name` (spec §2.4 — the candidate ids collapse to one uuid).
    """
    try:
        media = response_json["media"]
        return str(media[0]["name"])
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"generate response carries no media[0].name: {e}") from e


def parse_video_status(response_json: dict[str, Any], *, media_id: str) -> VideoStatus:
    """Parse one batchCheckAsyncVideoGenerationStatus response into a VideoStatus.

    Selects the `media[]` entry whose `name == media_id`, then reads
    `mediaMetadata.mediaStatus.{mediaGenerationStatus, failureReasons,
    error.message}`. Shapes: captures 10 (SUCCESSFUL), 11 (FAILED).
    Raises ValueError if `media_id` is absent or the status is malformed.
    """
    _media = response_json.get("media")
    if not isinstance(_media, list):
        raise ValueError("status response has no media[] array")
    media: list[dict[str, Any]] = cast(list[dict[str, Any]], _media)
    for item in media:
        if item.get("name") != media_id:
            continue
        meta = cast(dict[str, Any], item.get("mediaMetadata") or {})
        media_status = cast(dict[str, Any], meta.get("mediaStatus") or {})
        status = media_status.get("mediaGenerationStatus")
        if not isinstance(status, str):
            raise ValueError(f"status entry for {media_id} has no mediaGenerationStatus")
        reasons = tuple(cast(list[str], media_status.get("failureReasons") or []))
        error_entry = cast(dict[str, Any], media_status.get("error") or {})
        raw_msg = error_entry.get("message")
        error_message: str | None = str(raw_msg) if raw_msg is not None else None
        return VideoStatus(
            media_id=media_id,
            status=status,
            failure_reasons=reasons,
            error_message=error_message,
        )
    raise ValueError(f"media_id {media_id!r} not found in status response")
