"""Domain generation route predicates and measured allowlists.

This pure domain module determines whether an image, video, or upscale request can
safely be submitted directly over HTTP without driving the Flow user interface, and
plans the required browser visibility before any browser context is launched.
"""

from __future__ import annotations

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
    from collections.abc import Iterable

__all__ = [
    "IMAGE_WIRE_MODELS",
    "MEASURED_UPSCALE_SOURCES",
    "MEASURED_VIDEO_KEYS",
    "Route",
    "VideoWireTarget",
    "choose_image_route",
    "choose_upscale_route",
    "choose_video_route",
    "direct_can_serve",
    "plan_visibility",
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
    (VideoModel.OMNI_1_2_FLASH, Mode.I2V, 4, "360p", VideoAspect.LANDSCAPE): VideoWireTarget(
        "macaron_cookie_i2v_4s_360p", 2
    ),
    (VideoModel.VEO_3_1_LITE, Mode.T2V, None, None, VideoAspect.LANDSCAPE): VideoWireTarget(
        "veo_3_1_t2v_lite", 2
    ),
    (VideoModel.VEO_3_1_LITE, Mode.I2V, None, None, VideoAspect.PORTRAIT): VideoWireTarget(
        "veo_3_1_i2v_lite", 1
    ),
}

#: Source clip model keys measured for direct 1080p upscale on flow.google.com.
MEASURED_UPSCALE_SOURCES: frozenset[str] = frozenset(
    {"macaron_cookie_t2v_4s_360p", "macaron_cookie_i2v_4s_360p"}
)


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


def _is_video_request_supported(
    request: GenerateVideoRequest,
    effective_max_credits: int | None,
) -> bool:
    if _has_credit_cap(effective_max_credits, request.max_credits):
        return False
    if request.count != 1 or _has_video_references(request):
        return False
    return True


def _has_start_frame(request: GenerateVideoRequest) -> bool:
    return bool(
        request.start_image
        or request.start_image_ref_name
        or request.start_image_ref_id
        or request.start_image_ref_display_name
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
        if not _has_start_frame(request) or _has_end_frame(request):
            return None
        return request.model if request.model is not None else I2V_DEFAULT_MODEL
    if request.mode == Mode.T2V:
        return request.model
    return None


def _is_video_tuple_measured(
    model: VideoModel,
    request: GenerateVideoRequest,
) -> bool:
    key_tuple = (model, request.mode, request.duration, request.resolution, request.aspect)
    return key_tuple in MEASURED_VIDEO_KEYS


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
    if not _is_video_request_supported(request, effective_max_credits):
        return Route.UI

    model = _resolve_video_model(request)
    if model is None:
        return Route.UI

    if _is_video_tuple_measured(model, request):
        return Route.DIRECT

    return Route.UI


def _read_catalog_field(row: object, *names: str) -> str | None:
    if isinstance(row, dict):
        row_dict = cast("dict[str, object]", row)
        for name in names:
            val = row_dict.get(name)
            if isinstance(val, str) and val:
                return val
        return None
    for name in names:
        val = getattr(row, name, None)
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
    if not aspect:
        return Route.UI

    model = _read_catalog_field(catalog_row, "model")
    if not model or model not in MEASURED_UPSCALE_SOURCES:
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
