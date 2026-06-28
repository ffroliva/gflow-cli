"""Value objects for video generation.

This module is pure — no I/O. The video transport drives Flow's editor UI;
Flow's own JavaScript builds and sends the generate request, so this module
no longer carries HTTP body builders (the 401-dead HTTP video path was
retired — see the Phase A plan).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.tools.invocation import AppliedTool

# Type alias used with cast() in response parsers — avoids repeating the
# string-form annotation "dict[str, Any]" on every call (SonarCloud S1192).
_StrAnyDict = dict[str, Any]


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
        if key not in _VIDEO_MODEL_FROM_CLI:
            msg = (
                f"Unknown video model {value!r}; choose from "
                f"{sorted({m.value for m in cls})} or aliases {sorted(_VIDEO_MODEL_FROM_CLI)}"
            )
            raise ValueError(
                msg,
            )
        return _VIDEO_MODEL_FROM_CLI[key]

    def supports_i2v_interpolation(self) -> bool:
        """Whether this model supports image-to-video with start (and optional
        end) frame references.

        Verified empirically (issue #125, 2026-05-30) via the
        ``scripts/dev/capture_i2v_intercept_submit.py`` probe with
        ``page.route(..., abort)``: ``OMNI_FLASH`` causes Flow's frontend to
        silently drop frame refs at submit and route to
        ``batchAsyncGenerateVideoText`` with ``image_inputs: null``. The four
        ``VEO_3_1_*`` variants preserve the refs and route to
        ``batchAsyncGenerateVideoStartImage`` /
        ``batchAsyncGenerateVideoStartAndEndImage``.
        """
        return self is not VideoModel.OMNI_FLASH


# Default model for ``gflow video i2v`` and direct ``FlowApiClient.generate_video``
# callers when ``model`` is omitted and the request carries a start/end frame.
# ``omni_flash`` is excluded because it silently drops frame refs at submit
# time (issue #125). ``veo_3_1_lite`` is the cheapest interpolation-capable
# model, matching the price tier ``omni_flash`` previously occupied for t2v.
I2V_DEFAULT_MODEL: VideoModel = VideoModel.VEO_3_1_LITE


# Module-level alias map — friendly CLI strings -> VideoModel. Hoisted out of
# `VideoModel.from_cli` (defined after the class so the members resolve) so
# `gflow models` can enumerate the aliases without duplicating them.
_VIDEO_MODEL_FROM_CLI: dict[str, VideoModel] = {
    "omni_flash": VideoModel.OMNI_FLASH,
    "omni": VideoModel.OMNI_FLASH,
    "flash": VideoModel.OMNI_FLASH,
    "veo_3_1_lite": VideoModel.VEO_3_1_LITE,
    "veo_lite": VideoModel.VEO_3_1_LITE,
    "lite": VideoModel.VEO_3_1_LITE,
    "veo_3_1_fast": VideoModel.VEO_3_1_FAST,
    "veo_fast": VideoModel.VEO_3_1_FAST,
    "fast": VideoModel.VEO_3_1_FAST,
    "veo_3_1_quality": VideoModel.VEO_3_1_QUALITY,
    "veo_quality": VideoModel.VEO_3_1_QUALITY,
    "quality": VideoModel.VEO_3_1_QUALITY,
    "veo_3_1_lite_lower_priority": VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
    "veo_lite_lp": VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
    "lite_lp": VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
    "lower_priority": VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
}


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
            msg = f"Unsupported aspect ratio {value!r}; choose from {sorted(mapping)}"
            raise ValueError(msg)
        return mapping[value]


# Flow's R2V reference cap is MODEL-DEPENDENT (live-verified + Google's
# official support page): omni_flash allows 7 ("Maximum image ingredients
# reached (7 allowed)"); veo_3_1_lite / veo_3_1_fast / veo_3_1_lite_lower_priority
# allow 3; veo_3_1_quality does NOT support R2V at all (Google Flow help page
# "Ingredients/References to Video" row = "No"). A request that exceeds the cap
# uploads all refs but the generate call silently keeps only N — so we reject
# up front. MAX_REFERENCE_IMAGES is the absolute ceiling (omni) used when the
# model is unknown; the model-aware check below enforces the exact per-model
# limit (incl. the special cap=0 for QUALITY) when the model is known.
MAX_REFERENCE_IMAGES = 7
_VIDEO_REFERENCE_CAP: Mapping[VideoModel, int] = MappingProxyType(
    {
        VideoModel.OMNI_FLASH: 7,
        VideoModel.VEO_3_1_LITE: 3,
        VideoModel.VEO_3_1_FAST: 3,
        VideoModel.VEO_3_1_LITE_LOWER_PRIORITY: 3,
        VideoModel.VEO_3_1_QUALITY: 0,  # R2V unsupported per Google Flow docs
    },
)


def reference_cap_for(model: VideoModel) -> int:
    """Maximum number of R2V reference images *model* accepts.

    Returns 0 for models that do not support R2V at all
    (``VEO_3_1_QUALITY`` — per Google Flow's official support page).
    Unknown/future models fall back to :data:`MAX_REFERENCE_IMAGES` rather than
    raising, so adding a new ``VideoModel`` member without a cap entry degrades
    to the ceiling instead of a ``KeyError`` at request-build time.
    """
    return _VIDEO_REFERENCE_CAP.get(model, MAX_REFERENCE_IMAGES)


def model_aliases(model: VideoModel) -> list[str]:
    """Sorted CLI aliases that resolve to *model* (for `gflow models`)."""
    return sorted(alias for alias, m in _VIDEO_MODEL_FROM_CLI.items() if m is model)


def max_duration_for(model: VideoModel) -> int:
    """Maximum clip length in seconds: omni_flash=10, veo_3_1_*=8."""
    return 10 if model is VideoModel.OMNI_FLASH else 8


def aspect_choices() -> dict[str, str]:
    """Map each accepted CLI aspect ratio to its wire value."""
    return {
        "9:16": Aspect.PORTRAIT.wire(),
        "16:9": Aspect.LANDSCAPE.wire(),
        "1:1": Aspect.SQUARE.wire(),
    }


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
    reference_entities: tuple[str, ...] = ()  # R2V — Flow CHARACTER entity ids
    reference_entity_names: tuple[
        str, ...
    ] = ()  # R2V — character DISPLAY names (UI picker selection)
    reference_audio: str | None = None  # R2V — voice resource mediaId (e.g. "alnilam")
    # Tool provenance (recorded, never sent on the wire). ``original_prompt`` is
    # the user's pre-tool text when a ``--tool`` rewrote ``prompt``; ``tool`` is
    # the applied-tool snapshot for ``operations.metadata_json.tool``. (PR2 §8)
    original_prompt: str | None = None
    tool: AppliedTool | None = None

    def __post_init__(self) -> None:
        self._validate_prompt()
        self._validate_duration()
        self._validate_count()
        self._validate_mode_symmetry()
        self._validate_r2v_caps()
        self._validate_seed()

    def _validate_prompt(self) -> None:
        if not self.prompt.strip():
            msg = "prompt must not be empty"
            raise ValueError(msg)

    def _validate_duration(self) -> None:
        if self.duration is not None and self.duration not in (4, 6, 8, 10):
            msg = f"duration must be one of 4/6/8/10 seconds, got {self.duration}"
            raise ValueError(msg)
        if (
            self.duration == 10
            and self.model is not None
            and self.model is not VideoModel.OMNI_FLASH
        ):
            msg = (
                f"10s duration is only available for the omni_flash model; "
                f"{self.model.value} caps at 8s"
            )
            raise ValueError(
                msg,
            )

    def _validate_count(self) -> None:
        if not (1 <= self.count <= 4):
            msg = f"count must be 1-4, got {self.count}"
            raise ValueError(msg)

    def _validate_i2v_symmetry(self) -> None:
        if self.start_image is None:
            msg = "I2V request requires start_image"
            raise ValueError(msg)
        if self.reference_images or self.reference_entities:
            msg = "I2V request must not carry reference_images or reference_entities"
            raise ValueError(msg)

    def _validate_r2v_symmetry(self) -> None:
        if not self.reference_images and not self.reference_entities:
            msg = "R2V request requires reference_images or reference_entities"
            raise ValueError(msg)
        if self.start_image or self.end_image:
            msg = "R2V request must not carry start/end images"
            raise ValueError(msg)

    def _validate_mode_symmetry(self) -> None:
        if self.mode is Mode.T2V and (self.start_image or self.end_image or self.reference_images):
            msg = "T2V request must not carry image inputs"
            raise ValueError(msg)
        if self.mode is Mode.I2V:
            self._validate_i2v_symmetry()
        if self.mode is Mode.R2V:
            self._validate_r2v_symmetry()

    def _validate_r2v_caps(self) -> None:
        if len(self.reference_images) > MAX_REFERENCE_IMAGES:
            msg = f"at most {MAX_REFERENCE_IMAGES} reference images"
            raise ValueError(msg)
        # Per-model reference cap (live-verified): omni_flash=7, veo lite/fast/lite_lp=3,
        # veo_quality=0 (R2V unsupported per Google docs). When the model is None
        # (Flow UI default) we can't know it — leave the absolute ceiling above.
        if self.mode is Mode.R2V and self.model is not None:
            cap = reference_cap_for(self.model)
            if cap == 0:
                msg = f"{self.model.value} does not support R2V (reference-to-video)"
                raise ValueError(msg)
            if len(self.reference_images) > cap:
                msg = (
                    f"{self.model.value} allows at most {cap} reference image(s); "
                    f"got {len(self.reference_images)}"
                )
                raise ValueError(
                    msg,
                )
            total_refs = len(self.reference_images) + len(self.reference_entities)
            if total_refs > cap:
                msg = (
                    f"reference cap exceeded: {total_refs} refs (images+entities) "
                    f"> {cap} for {self.model.value}"
                )
                raise ValueError(msg)

    def _validate_seed(self) -> None:
        if self.seed is not None and not (0 <= self.seed <= 2**31 - 1):
            msg = "seed out of range"
            raise ValueError(msg)


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
    first: dict[str, Any] = cast(_StrAnyDict, operations[0])
    operation: dict[str, Any] | None = cast("dict[str, Any] | None", first.get("operation"))
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
        msg = f"generate response carries no media[0].name: {e}"
        raise ValueError(msg) from e


def parse_video_status(response_json: dict[str, Any], *, media_id: str) -> VideoStatus:
    """Parse one batchCheckAsyncVideoGenerationStatus response into a VideoStatus.

    Selects the `media[]` entry whose `name == media_id`, then reads
    `mediaMetadata.mediaStatus.{mediaGenerationStatus, failureReasons,
    error.message}`. Shapes: captures 10 (SUCCESSFUL), 11 (FAILED).
    Raises ValueError if `media_id` is absent or the status is malformed.
    """
    _media = response_json.get("media")
    if not isinstance(_media, list):
        msg = "status response has no media[] array"
        raise ValueError(msg)
    media: list[dict[str, Any]] = cast("list[dict[str, Any]]", _media)
    for item in media:
        if item.get("name") != media_id:
            continue
        meta = cast(_StrAnyDict, item.get("mediaMetadata") or {})
        media_status = cast(_StrAnyDict, meta.get("mediaStatus") or {})
        status = media_status.get("mediaGenerationStatus")
        if not isinstance(status, str):
            msg = f"status entry for {media_id} has no mediaGenerationStatus"
            raise ValueError(msg)
        reasons = tuple(cast("list[str]", media_status.get("failureReasons") or []))
        error_entry = cast(_StrAnyDict, media_status.get("error") or {})
        raw_msg = error_entry.get("message")
        error_message: str | None = str(raw_msg) if raw_msg is not None else None
        return VideoStatus(
            media_id=media_id,
            status=status,
            failure_reasons=reasons,
            error_message=error_message,
        )
    msg = f"media_id {media_id!r} not found in status response"
    raise ValueError(msg)
