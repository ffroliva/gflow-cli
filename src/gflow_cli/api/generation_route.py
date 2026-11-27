"""Domain generation route predicates and measured allowlists.

This pure domain module determines whether an image, video, or upscale request can
safely be submitted directly over HTTP without driving the Flow user interface, and
plans the required browser visibility before any browser context is launched.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple, cast
from urllib.request import getproxies

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
    GenerateVideoRequest,
    Mode,
    VideoModel,
)
from gflow_cli.api.video import (
    Aspect as VideoAspect,
)
from gflow_cli.config import Settings, UiMode, get_settings, resolve_ui_mode

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
    tuple[VideoModel | None, Mode, int | None, str | None, VideoAspect | str],
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
    (VideoModel.OMNI_1_2_FLASH, Mode.T2V, 4, "360p", "16:9"): VideoWireTarget(
        "macaron_cookie_t2v_4s_360p", 2
    ),
    (VideoModel.OMNI_1_2_FLASH, Mode.T2V, 4, "360p", "9:16"): VideoWireTarget(
        "macaron_cookie_t2v_4s_360p", 1
    ),
    (VideoModel.OMNI_1_2_FLASH, Mode.I2V, 4, "360p", "16:9"): VideoWireTarget(
        "macaron_cookie_i2v_4s_360p", 2
    ),
    (VideoModel.VEO_3_1_LITE, Mode.T2V, None, None, "16:9"): VideoWireTarget("veo_3_1_t2v_lite", 2),
    (VideoModel.VEO_3_1_LITE, Mode.I2V, None, None, "9:16"): VideoWireTarget("veo_3_1_i2v_lite", 1),
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
        and resolve_ui_mode(request.ui_mode) is not UiMode.AGENTIC
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


def _proxy_configured(env: Settings | None = None) -> bool:
    settings = env if env is not None else get_settings()
    if settings.browser_proxy:
        return True
    return any(k != "no" for k in getproxies())


def _flow_host(env: Settings | None = None) -> str:
    settings = env if env is not None else get_settings()
    return str(settings.flow_host)


def choose_image_route(
    request: GenerateImageRequest,
    env: Settings | None = None,
) -> Route:
    """Decide whether an image generation request can be served directly over HTTP."""
    if not direct_can_serve(request):
        return Route.UI
    if _flow_host(env) == "labs.google":
        return Route.UI
    if _proxy_configured(env):
        return Route.UI
    return Route.DIRECT


def choose_video_route(
    request: GenerateVideoRequest,
    env: Settings | None = None,
    *,
    effective_max_credits: int | None = None,
) -> Route:
    """Decide whether a video generation request can be served directly over HTTP."""
    if _flow_host(env) == "labs.google":
        return Route.UI
    if _proxy_configured(env):
        return Route.UI

    if effective_max_credits is None:
        if request.max_credits is not None:
            effective_max_credits = request.max_credits
        elif env is not None and env.max_credits is not None:
            effective_max_credits = env.max_credits
        else:
            try:
                effective_max_credits = get_settings().max_credits
            except Exception:
                effective_max_credits = None

    if effective_max_credits is not None:
        return Route.UI

    if request.count != 1:
        return Route.UI

    if request.reference_images or request.ref_names:
        return Route.UI

    if request.reference_entities or request.reference_entity_names:
        return Route.UI

    if request.reference_audio is not None:
        return Route.UI

    model = request.model
    if request.mode == Mode.I2V:
        if model is None:
            model = I2V_DEFAULT_MODEL
        has_start_frame = bool(
            request.start_image
            or request.start_image_ref_name
            or request.start_image_ref_id
            or request.start_image_ref_display_name
        )
        if not has_start_frame:
            return Route.UI
        has_end_frame = bool(
            request.end_image
            or request.end_image_ref_name
            or request.end_image_ref_id
            or request.end_image_ref_display_name
        )
        if has_end_frame:
            return Route.UI
    elif request.mode == Mode.T2V:
        if model is None:
            return Route.UI
    else:
        return Route.UI

    aspect = request.aspect
    key_tuple = (model, request.mode, request.duration, request.resolution, aspect)
    if key_tuple in MEASURED_VIDEO_KEYS:
        return Route.DIRECT

    cli_mapping: dict[VideoAspect, str] = {
        VideoAspect.PORTRAIT: "9:16",
        VideoAspect.LANDSCAPE: "16:9",
        VideoAspect.SQUARE: "1:1",
    }
    if aspect in cli_mapping:
        alias = cli_mapping[aspect]
        if (
            model,
            request.mode,
            request.duration,
            request.resolution,
            alias,
        ) in MEASURED_VIDEO_KEYS:
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


def choose_upscale_route(scale: str, catalog_row: object | None) -> Route:
    """Decide whether a video upscale request can be served directly over HTTP."""
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
    """Plan concrete browser visibility based on configuration and planned routes."""
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
