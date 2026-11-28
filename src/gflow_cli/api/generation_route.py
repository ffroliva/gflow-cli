"""Domain generation route predicates and measured allowlists.

This pure domain module determines whether an image, video, or upscale request can
safely be submitted directly over HTTP without driving the Flow user interface, and
plans the required browser visibility before any browser context is launched.
"""

from __future__ import annotations

from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple, cast

from gflow_cli.api.image import (
    Aspect as ImageAspect,
)
from gflow_cli.api.image import (
    GenerateImageRequest,
)
from gflow_cli.api.image import (
    Model as ImageModel,
)
from gflow_cli.api.submit_policy import BrowserVisibility, VisibilitySetting
from gflow_cli.api.video import (
    I2V_DEFAULT_MODEL,
    EditVideoRequest,
    GenerateVideoRequest,
    Mode,
    VideoModel,
)
from gflow_cli.api.video import (
    Aspect as VideoAspect,
)
from gflow_cli.api.video_extend import ExtendVideoRequest
from gflow_cli.config import UiMode

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "IMAGE_WIRE_MODELS",
    "MEASURED_UPSCALE_SOURCES",
    "MEASURED_VIDEO_KEYS",
    "VIDEO_FAMILY_MODELS",
    "VIDEO_RESOLUTION_ENUMS",
    "Route",
    "VideoWireTarget",
    "choose_image_route",
    "choose_upscale_route",
    "choose_video_route",
    "direct_can_serve",
    "is_bare_t2v",
    "plan_visibility",
    "selected_video_request",
    "video_target",
]


class Route(StrEnum):
    """Destination transport route for a generation or upscale request."""

    DIRECT = "direct"
    UI = "ui"


class VideoWireTarget(NamedTuple):
    """Wire model identifier and aspect ratio slot index for direct video POST."""

    wire_key: str
    aspect_slot: int


#: Image models with measured wire bodies on flow.google.com.
IMAGE_WIRE_MODELS: dict[ImageModel, str] = {
    ImageModel.NARWHAL: "BELUGA",
    ImageModel.HARBOR_SEAL: "HARBOR_SEAL",
    ImageModel.GEM_PIX_2: "GEM_PIX_2",
}

#: Exact (model, mode, duration, resolution, aspect) tuples measured on flow.google.com.
MEASURED_VIDEO_KEYS: dict[
    tuple[VideoModel | None, Mode, int | None, str | None, VideoAspect],
    VideoWireTarget,
] = {
    (VideoModel.OMNI_1_2_FLASH, Mode.T2V, 4, "360p", VideoAspect.LANDSCAPE): VideoWireTarget(
        "macaron_cookie_t2v_4s_360p", 2
    ),
    (VideoModel.OMNI_1_2_FLASH, Mode.T2V, 4, "360p", VideoAspect.PORTRAIT): VideoWireTarget(
        "macaron_cookie_t2v_4s_360p", 1
    ),
    (VideoModel.VEO_3_1_LITE, Mode.I2V, None, None, VideoAspect.PORTRAIT): VideoWireTarget(
        "veo_3_1_i2v_lite", 1
    ),
}

#: The composer's ``videoModelFamilyId`` (``localStorage['flow-prompt-box-settings']``, spike
#: 2026-11-27-selected-video-model) per model. A family not listed keys nothing: the UI path.
VIDEO_FAMILY_MODELS: dict[str, VideoModel] = {"macaron_cookie": VideoModel.OMNI_1_2_FLASH}
#: The composer's ``videoResolution`` enum, as measured (4 is 360p); any other value is unmapped.
VIDEO_RESOLUTION_ENUMS: dict[int, str] = {4: "360p"}

#: Source clips measured for direct 1080p upscale on flow.google.com, as the catalog records
#: them: (CLI model value, aspect), and only a 4 s source.
MEASURED_UPSCALE_SOURCES: frozenset[tuple[str, str]] = frozenset({("omni_1_2_flash", "landscape")})
MEASURED_UPSCALE_DURATION_S = 4
#: The catalog's other spelling of 16:9.
_ASPECT_ALIASES = {"16:9": "landscape"}


def direct_can_serve(request: GenerateImageRequest) -> bool:
    """True only for an image request whose every wire slot is measured.

    T2I with BELUGA (Nano Banana 2.1), any aspect, count 1-4, or one in-project reference or one
    named character (count 1-4). HARBOR_SEAL and GEM_PIX_2 were measured only as portrait
    text-to-image with count 1.

    An unresolvable credit cap must be passed as an explicit cap (route UI), never as "no cap".
    """
    return (
        request.model in IMAGE_WIRE_MODELS
        and _model_shape_measured(request)
        and _references_measured(request)
        and _characters_measured(request)
        and not request.ref_paths
        and not request.instructions
        and request.thinking_level is None
        and "@" not in request.prompt
        and request.ui_mode is not UiMode.AGENTIC
    )


def _model_shape_measured(request: GenerateImageRequest) -> bool:
    if request.model is ImageModel.NARWHAL:
        return True
    return (
        request.aspect is ImageAspect.PORTRAIT
        and request.count == 1
        and not request.refs
        and not request.reference_entities
        and not request.reference_entity_names
    )


def _references_measured(request: GenerateImageRequest) -> bool:
    if not request.refs:
        return True
    (ref, *more) = request.refs
    return not more and ref.in_project and bool(ref.display_name) and not ref.local_path


def _characters_measured(request: GenerateImageRequest) -> bool:
    ids, names = request.reference_entities, request.reference_entity_names
    if not ids and not names:
        return True
    return len(ids) <= 1 and len(names) == 1 and bool(names[0]) and not request.refs


def choose_image_route(
    request: GenerateImageRequest,
    *,
    flow_host: str,
    proxy_configured: bool,
) -> Route:
    """Decide whether an image generation request can be served directly over HTTP.

    An unresolvable credit cap must be passed as an explicit cap (route UI), never as "no cap".
    """
    if not direct_can_serve(request):
        return Route.UI
    if flow_host == "labs.google":
        return Route.UI
    if proxy_configured:
        return Route.UI
    return Route.DIRECT


def _is_environment_supported(*, flow_host: str, proxy_configured: bool) -> bool:
    return flow_host != "labs.google" and not proxy_configured


def _has_credit_cap(effective_max_credits: int | None, request_cap: int | None) -> bool:
    return effective_max_credits is not None or request_cap is not None


def _has_video_references(request: GenerateVideoRequest) -> bool:
    return bool(
        request.reference_images
        or request.ref_names
        or request.reference_entities
        or request.reference_entity_names
        or request.reference_audio is not None
    )


def _has_end_frame(request: GenerateVideoRequest) -> bool:
    return bool(
        request.end_image
        or request.end_image_ref_name
        or request.end_image_ref_id
        or request.end_image_ref_display_name
    )


def _resolve_video_model(request: GenerateVideoRequest) -> VideoModel | None:
    if request.mode == Mode.I2V:
        # A local start frame only: an in-project one carries no bytes for the crop box.
        if request.start_image is None or _has_end_frame(request):
            return None
        return request.model if request.model is not None else I2V_DEFAULT_MODEL
    if request.mode == Mode.T2V:
        return request.model
    return None


def _single_plain_clip(request: GenerateVideoRequest) -> bool:
    return (
        request.count == 1
        and not _has_video_references(request)
        and request.ui_mode is not UiMode.AGENTIC
    )


def video_target(request: GenerateVideoRequest) -> VideoWireTarget | None:
    """The measured wire key and aspect slot for ``request``'s shape, or ``None`` (the UI path).

    Shape only: one clip, no references, not the agentic UI, an i2v start frame that is a
    local file, and an exact measured tuple. The host, the proxy and the credit cap are the
    caller's (:func:`choose_video_route`).
    """
    if not _single_plain_clip(request):
        return None
    model = _resolve_video_model(request)
    if model is None:
        return None
    return MEASURED_VIDEO_KEYS.get(
        (model, request.mode, request.duration, request.resolution, request.aspect)
    )


def is_bare_t2v(request: GenerateVideoRequest) -> bool:
    """A one-clip t2v with no model: its key is the account's selection, read after launch."""
    return request.mode is Mode.T2V and request.model is None and _single_plain_clip(request)


def _int_field(selection: Mapping[str, object], name: str) -> int | None:
    value = selection.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def selected_video_request(
    request: GenerateVideoRequest, selection: Mapping[str, object]
) -> GenerateVideoRequest | None:
    """``request`` with the model, duration and resolution the account's selection names.

    ``selection`` is the composer's saved picker state (``flow-prompt-box-settings``). A
    duration or resolution the request sets explicitly wins, as it does in Flow's UI. An
    unmapped family or resolution enum, or a combination the request rejects (a duration the
    model does not allow), resolves to ``None``; whether the result is measured is
    :func:`video_target`'s decision, the single lookup.
    """
    family = selection.get("videoModelFamilyId")
    model = VIDEO_FAMILY_MODELS.get(family) if isinstance(family, str) else None
    resolution_enum = _int_field(selection, "videoResolution")
    resolution = request.resolution or (
        None if resolution_enum is None else VIDEO_RESOLUTION_ENUMS.get(resolution_enum)
    )
    if model is None or resolution is None:
        return None
    duration = request.duration
    if duration is None:
        duration = _int_field(selection, "videoDuration")
    try:
        return replace(request, model=model, duration=duration, resolution=resolution)
    except ValueError:
        return None


def choose_video_route(
    request: GenerateVideoRequest | ExtendVideoRequest | EditVideoRequest,
    *,
    flow_host: str,
    proxy_configured: bool,
    effective_max_credits: int | None,
) -> Route:
    """Decide whether a video generation request can be served directly over HTTP.

    The caller must resolve any environment or profile credit caps before calling.
    An unresolvable credit cap must be passed as an explicit cap (route UI), never as "no cap".
    """
    if not _is_environment_supported(flow_host=flow_host, proxy_configured=proxy_configured):
        return Route.UI
    if isinstance(request, (ExtendVideoRequest, EditVideoRequest)):
        return Route.UI
    if _has_credit_cap(effective_max_credits, request.max_credits):
        return Route.UI
    # A mention can resolve to a reference after launch, which the direct frame cannot carry.
    if "@" in request.prompt:
        return Route.UI
    # A bare t2v plans direct (headless): the transport reads the selection on the armed page
    # and ends NotSent when it is unread or unmeasured, so an unmeasured selection costs at
    # most one extra (headed) launch for the UI path.
    if is_bare_t2v(request):
        return Route.DIRECT
    return Route.DIRECT if video_target(request) is not None else Route.UI


def _catalog_value(row: object, name: str) -> object:
    if isinstance(row, dict):
        return cast("dict[str, object]", row).get(name)
    return getattr(row, name, None)


def _read_catalog_field(row: object, *names: str) -> str | None:
    for name in names:
        val = _catalog_value(row, name)
        if isinstance(val, str) and val:
            return val
    return None


def choose_upscale_route(
    scale: str,
    catalog_row: object | None,
    *,
    flow_host: str,
    proxy_configured: bool,
) -> Route:
    """Decide whether a video upscale request can be served directly over HTTP.

    An unresolvable credit cap must be passed as an explicit cap (route UI), never as "no cap".
    """
    if flow_host == "labs.google":
        return Route.UI
    if proxy_configured:
        return Route.UI
    if scale != "1080p":
        return Route.UI
    if catalog_row is None:
        return Route.UI

    workflow_id = _read_catalog_field(catalog_row, "workflow_id", "flow_workflow_id")
    if not workflow_id:
        return Route.UI

    aspect = _read_catalog_field(catalog_row, "aspect", "aspect_ratio")
    model = _read_catalog_field(catalog_row, "model")
    if not aspect or not model:
        return Route.UI
    if (model, _ASPECT_ALIASES.get(aspect, aspect)) not in MEASURED_UPSCALE_SOURCES:
        return Route.UI
    # Resolution is not catalogued: a source Flow will not upscale still surfaces as exit 22.
    if _catalog_value(catalog_row, "duration_seconds") != MEASURED_UPSCALE_DURATION_S:
        return Route.UI
    return Route.DIRECT


def plan_visibility(
    setting: VisibilitySetting | str,
    routes: Iterable[Route],
) -> BrowserVisibility:
    """Plan concrete browser visibility based on configuration and planned routes.

    An unresolvable credit cap must be passed as an explicit cap (route UI), never as "no cap".
    """
    try:
        resolved_setting = VisibilitySetting(setting)
    except ValueError:
        resolved_setting = VisibilitySetting.AUTO

    if resolved_setting == VisibilitySetting.HEADLESS:
        return BrowserVisibility.HEADLESS
    if resolved_setting == VisibilitySetting.HEADED:
        return BrowserVisibility.HEADED

    if any(r == Route.UI for r in routes):
        return BrowserVisibility.HEADED
    return BrowserVisibility.HEADLESS
