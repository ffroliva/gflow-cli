"""Value objects for video generation.

This module is pure — no I/O. The video transport drives Flow's editor UI;
Flow's own JavaScript builds and sends the generate request, so this module
no longer carries HTTP body builders (the 401-dead HTTP video path was
retired — see the Phase A plan).
"""

from __future__ import annotations

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


# Flow's R2V ("Elementos") reference-image slot cap. ESTIMATE — spec §10.2 Q6
# was NOT resolved by the Phase 0 spike (§10.5); Phase B confirms the real
# upper bound. R2V is not wired in Phase A, so this value is never exercised
# in production yet.
MAX_REFERENCE_IMAGES = 3


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
    seed: int | None = None
    start_image: Path | None = None  # I2V
    end_image: Path | None = None  # I2V (optional)
    reference_images: tuple[Path, ...] = ()  # R2V

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
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
class VideoResult:
    """Return value of :meth:`generate_video` after Phase B download wiring.

    ``local_path`` is ``None`` when ``download=False`` was passed, or when
    the generation failed — callers should check ``status.succeeded`` first.
    """

    status: VideoStatus
    local_path: Path | None


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
