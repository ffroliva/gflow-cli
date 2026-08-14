"""Value objects for video generation.

This module is pure — no I/O. The video transport drives Flow's editor UI;
Flow's own JavaScript builds and sends the generate request, so this module
no longer carries HTTP body builders (the 401-dead HTTP video path was
retired — see the Phase A plan).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.config import UiMode
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
    The selector for each lives in the transport layer (this module is pure —
    no DOM knowledge).

    **Capability corrections (2026-08-14, verified on two accounts and two
    locales — see docs/superpowers/spikes/2026-08-14-video-model-capability-matrix.md):**
    this docstring previously claimed "the four ``VEO_3_1_*`` models cap at 8s",
    which presumed they render a duration control. They render **none at all** —
    only ``OMNI_FLASH`` shows the 4s/6s/8s/10s row. That mistaken assumption is
    the root cause of issues #451/#288, where ``--duration`` failures looked like
    selector drift. See :meth:`supports_duration`.
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

    def supports_i2v_end_frame(self) -> bool:
        """Whether this model supports i2v with an END frame (first+last
        interpolation).

        ``OMNI_FLASH`` is excluded: Google's official support matrix lists
        "Frames to Video: First + last" as "coming soon" for it (2026-08), and
        there is no wire-level capture proving the
        ``batchAsyncGenerateVideoStartAndEndImage`` route for Omni Flash. The
        ``VEO_3_1_*`` variants have carried end frames on that route since the
        original #125 captures.

        START-frame i2v needs no capability gate: every current model
        supports it. History (issue #125): a 2026-05-30 route-abort capture
        showed ``OMNI_FLASH`` silently dropping frame refs at submit and
        routing to ``batchAsyncGenerateVideoText``, so it was excluded from
        i2v entirely. Re-verified 2026-08-03 with the same probe
        (``capture_i2v_intercept_submit.py --model omni-flash --start-only``,
        request aborted in-browser, zero credits): Flow now routes Omni Flash
        + bound Start frame to ``batchAsyncGenerateVideoStartImage`` with a
        non-null ``startImage`` — confirmed the same day by one live x1 10s
        generation whose output's first frame IS the uploaded start frame.
        """
        return self is not VideoModel.OMNI_FLASH

    def supports_duration(self) -> bool:
        """Whether this model renders a duration control at all (issues #451/#288).

        Verified live on two accounts and two locales (2026-08-14): the classic
        video settings popover is **model-conditional**. ``OMNI_FLASH`` renders
        a `4s / 6s / 8s / 10s` row; the ``VEO_3_1_*`` models render **no
        duration control whatsoever** — so a duration simply cannot be selected
        for them.

        This is why ``--duration`` on a Veo model failed as
        ``UiSelectorDriftError`` (exit 23): the transport hunted a control the
        model never draws. Reproduced identically on playwright 1.59 and 1.61,
        which is why the version bound was correctly exonerated, and the locale
        hypothesis correctly refuted — it was never either.
        """
        return self is VideoModel.OMNI_FLASH


# Default model for ``gflow video i2v`` and direct ``FlowApiClient.generate_video``
# callers when ``model`` is omitted and the request carries a start/end frame.
# ``veo_3_1_lite`` stays the default: it is the cheapest model that supports
# BOTH start-only and start+end i2v. ``omni_flash`` (start-only, 10s capable —
# re-verified 2026-08-03, refs #125) is opt-in via an explicit ``--model``.
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
    (``VEO_3_1_QUALITY`` — per Google Flow's official support page, and
    confirmed live 2026-08-14 on two accounts: selecting Veo 3.1 - Quality greys
    an attached ingredient with "You cannot use image ingredients with this
    model", while Omni Flash / Fast / Lite accept the same asset. A cap of 0 is
    therefore also the answer to "does this model take image ingredients?" —
    there is deliberately no second predicate encoding the same rule).

    Ordering note: Flow flags an ingredient attached BEFORE the model was
    switched, so the model must be chosen first. The transport already does this
    (``configure_video_settings`` runs before ``_attach_media_inputs``).
    Unknown/future models fall back to :data:`MAX_REFERENCE_IMAGES` rather than
    raising, so adding a new ``VideoModel`` member without a cap entry degrades
    to the ceiling instead of a ``KeyError`` at request-build time.
    """
    return _VIDEO_REFERENCE_CAP.get(model, MAX_REFERENCE_IMAGES)


def model_aliases(model: VideoModel) -> list[str]:
    """Sorted CLI aliases that resolve to *model* (for `gflow models`)."""
    return sorted(alias for alias, m in _VIDEO_MODEL_FROM_CLI.items() if m is model)


def max_duration_for(model: VideoModel) -> int:
    """Maximum selectable clip length in seconds.

    ``omni_flash`` = 10. The ``VEO_3_1_*`` models return **0**: verified live on
    two accounts (2026-08-14) they render no duration control at all, so no
    duration is selectable for them — see :meth:`VideoModel.supports_duration`
    and issues #451/#288. Returning 8 here (the old value) contradicted that
    predicate and made ``gflow models`` advertise a duration users cannot set.
    """
    if model is VideoModel.OMNI_FLASH:
        return 10
    return 0 if not model.supports_duration() else 8


# Case-insensitive 8-4-4-4-12 hex with hyphens — Flow's media UUIDs (the same
# shape cli_image's --ref classifier accepts).
_MEDIA_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def is_media_uuid(value: str) -> bool:
    """True when *value* is shaped like a Flow media UUID (#287)."""
    return _MEDIA_UUID_RE.fullmatch(value) is not None


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
    start_image: Path | None = None  # I2V (local file path)
    start_image_ref_name: str | None = None  # I2V (remote asset display name)
    start_image_ref_id: str | None = None  # I2V (in-project asset media UUID, #287)
    end_image: Path | None = None  # I2V (optional local file path)
    end_image_ref_name: str | None = None  # I2V (optional remote asset display name)
    end_image_ref_id: str | None = None  # I2V (optional in-project asset media UUID, #287)
    # Display name of the target project for the media picker's project-menu
    # match (#287: the menu lists projects by NAME, not id). Optional override
    # (--project-name / GFLOW_CLI_PROJECT_NAME); when None the transport
    # derives a name from the live page.
    project_name: str | None = None
    # Picker search hints for UUID frame refs (#287 round 6): Flow's media
    # search does not index UUIDs, but tile alt text carries the generation
    # prompt — the CLI resolves each ref's recorded prompt from the local
    # catalog and passes its first words here; the transport types them into
    # the picker search box and matches results by UUID-in-src.
    search_hints: tuple[str, ...] = ()
    reference_images: tuple[Path, ...] = ()  # R2V (local file paths)
    ref_names: tuple[str, ...] = ()  # R2V (remote asset display names)
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
    # Requested Flow UI arm (#299) from --ui-mode; None → resolve from
    # GFLOW_CLI_UI_MODE / default at the transport. Not sent on the wire.
    # The video pipeline clamps to classic-required (no agentic video driver
    # exists); see _generate_video_locked.
    ui_mode: UiMode | None = None

    def __post_init__(self) -> None:
        self._validate_prompt()
        self._validate_duration()
        self._validate_count()
        self._validate_frame_ref_ids()
        self._validate_mode_symmetry()
        self._validate_r2v_caps()
        self._validate_model_capabilities()
        self._validate_seed()
        self._validate_ui_mode()

    def _has_frame_input(self) -> bool:
        """True when the request carries an i2v start/end frame in any form."""
        return any(
            (
                self.start_image,
                self.start_image_ref_name,
                self.start_image_ref_id,
                self.end_image,
                self.end_image_ref_name,
                self.end_image_ref_id,
            )
        )

    def _validate_model_capabilities(self) -> None:
        """Reject model/feature combinations Flow's UI cannot express (#451/#288).

        Only runs when ``model`` is explicit: with ``model=None`` the picker is
        untouched and Flow's own default applies, so there is nothing to check
        against. Both branches fail HERE — at DTO construction, before any
        browser work — instead of surfacing later as a selector-drift timeout
        that blames the UI for a capability mismatch.
        """
        # Resolve the model the TRANSPORT will actually use, not just the one
        # the caller named. An i2v request with frames and no --model is bound
        # to I2V_DEFAULT_MODEL downstream (drivers/classic.py, _resolve_i2v_model),
        # so an unresolved `model is None` early-return let i2v's own DEFAULT
        # path keep hitting the exact #451/#288 failure this guard exists to
        # prevent. For t2v/r2v with no model, Flow's sticky UI default applies
        # and is genuinely unknowable here — those stay unguarded by design.
        effective = self.model
        if effective is None and self.mode is Mode.I2V and self._has_frame_input():
            effective = I2V_DEFAULT_MODEL
        if effective is None:
            return
        if self.duration is not None and not effective.supports_duration():
            msg = (
                f"model {effective.value!r} has no duration control in Flow's UI, so "
                f"--duration {self.duration} cannot be applied. Only "
                f"{VideoModel.OMNI_FLASH.value!r} exposes a duration (4/6/8/10s); the "
                f"Veo 3.1 models render no duration row at all. Drop --duration to accept "
                f"Flow's default length, or use --model omni-flash."
            )
            raise ValueError(msg)
        # NOTE: the ingredient x model case is deliberately NOT re-checked here.
        # ``_validate_r2v_caps`` already rejects it via ``reference_cap_for() == 0``
        # (VEO_3_1_QUALITY), with a cap-aware message. A second guard would be a
        # second source of truth for the same rule.

    def _validate_ui_mode(self) -> None:
        # #299: no agentic VIDEO driver exists — an explicit agentic request
        # must fail loudly at EVERY producer (the CLI/MCP edges reject earlier
        # with friendlier errors; this catches queue payloads and programmatic
        # use, where a silent classic clamp would spend credits on a render
        # the caller believes is agentic). Env-sourced agentic never reaches
        # the DTO (stays None) and degrades at the transport with a warning.
        if self.ui_mode is not None and self.ui_mode.value == "agentic":
            msg = (
                "ui_mode 'agentic' is not supported for video generation "
                "(no agentic video driver exists; refs #299)"
            )
            raise ValueError(msg)

    def _validate_frame_ref_ids(self) -> None:
        for slot, ref_id, alternatives in (
            ("start", self.start_image_ref_id, (self.start_image, self.start_image_ref_name)),
            ("end", self.end_image_ref_id, (self.end_image, self.end_image_ref_name)),
        ):
            if ref_id is None:
                continue
            if not is_media_uuid(ref_id):
                msg = f"{slot}_image_ref_id {ref_id!r} is not a valid media UUID"
                raise ValueError(msg)
            if any(alt is not None for alt in alternatives):
                msg = (
                    f"at most one of {slot}_image, {slot}_image_ref_name, or "
                    f"{slot}_image_ref_id may be set"
                )
                raise ValueError(msg)

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
        if (
            self.start_image is None
            and self.start_image_ref_name is None
            and self.start_image_ref_id is None
        ):
            msg = "I2V request requires start_image, start_image_ref_name, or start_image_ref_id"
            raise ValueError(msg)
        if self.reference_images or self.ref_names or self.reference_entities:
            msg = "I2V request must not carry reference_images, ref_names, or reference_entities"
            raise ValueError(msg)

    def _validate_r2v_symmetry(self) -> None:
        if not self.reference_images and not self.ref_names and not self.reference_entities:
            msg = "R2V request requires reference_images, ref_names, or reference_entities"
            raise ValueError(msg)
        if self._has_frame_input():
            msg = "R2V request must not carry start/end images"
            raise ValueError(msg)

    def _validate_mode_symmetry(self) -> None:
        if self.mode is Mode.T2V and (
            self._has_frame_input() or self.reference_images or self.ref_names
        ):
            msg = "T2V request must not carry image inputs"
            raise ValueError(msg)
        if self.mode is Mode.I2V:
            self._validate_i2v_symmetry()
        if self.mode is Mode.R2V:
            self._validate_r2v_symmetry()

    def _validate_r2v_caps(self) -> None:
        if len(self.reference_images) + len(self.ref_names) > MAX_REFERENCE_IMAGES:
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
            total_img_refs = len(self.reference_images) + len(self.ref_names)
            if total_img_refs > cap:
                msg = (
                    f"{self.model.value} allows at most {cap} reference image(s); "
                    f"got {total_img_refs}"
                )
                raise ValueError(
                    msg,
                )
            total_refs = total_img_refs + len(self.reference_entities)
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
