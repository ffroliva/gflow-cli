"""The direct transport's routes, read from the settings.

One copy for both readers: the pre-launch plan (``_cli_helpers``) and the transport's run-time
checks call these, so the planned visibility always matches what the transport does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gflow_cli.api import flow_wire
from gflow_cli.api.generation_route import (
    Route,
    choose_image_route,
    choose_upscale_route,
    choose_video_route,
    direct_can_serve,
)
from gflow_cli.config import UiMode, get_settings, resolve_ui_mode

if TYPE_CHECKING:
    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest


def environment_reason() -> str | None:
    """Why no request may go direct in this environment, or ``None`` when one may."""
    if get_settings().flow_host == "labs.google":
        return "flow_host_labs"
    if flow_wire.proxy_configured():
        return "proxy_configured"
    return None


def image_shape_direct(request: GenerateImageRequest) -> bool:
    """Whether ``request``'s shape may go direct, whatever the environment; the agentic UI
    mode (request or setting) is the UI."""
    return direct_can_serve(request) and resolve_ui_mode(request.ui_mode) is not UiMode.AGENTIC


def image_route(request: GenerateImageRequest) -> Route:
    """The route for ``request``: its shape, then the environment."""
    if not image_shape_direct(request):
        return Route.UI
    return choose_image_route(
        request, flow_host=get_settings().flow_host, proxy_configured=flow_wire.proxy_configured()
    )


def video_route(request: GenerateVideoRequest) -> Route:
    """The route for ``request``. The request carries its effective credit cap (callers
    resolve ``GFLOW_CLI_MAX_CREDITS`` into it), so a capped video routes to the UI, where
    Flow's quote is read."""
    if resolve_ui_mode(request.ui_mode) is UiMode.AGENTIC:
        return Route.UI
    return choose_video_route(
        request,
        flow_host=get_settings().flow_host,
        proxy_configured=flow_wire.proxy_configured(),
        effective_max_credits=request.max_credits,
    )


def upscale_route(scale: str, catalog_row: object | None) -> Route:
    """The route a video upscale takes, from the local catalog row alone."""
    return choose_upscale_route(
        scale,
        catalog_row,
        flow_host=get_settings().flow_host,
        proxy_configured=flow_wire.proxy_configured(),
    )
