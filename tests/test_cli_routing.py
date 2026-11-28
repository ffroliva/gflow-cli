"""Route-aware client construction: every launch decides its visibility from planned routes.

SCENARIO.md rows 6 (a UI-bound shape launches headed), 11 and 33 (a credit-capped video routes
to the UI) and 34 (a mixed run launches headed; an all-direct run launches headless).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli import config
from gflow_cli._cli_helpers import (
    client_visibility,
    image_route,
    upscale_route,
    video_client_visibility,
    video_route,
)
from gflow_cli.api.generation_route import Route
from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.submit_policy import BrowserVisibility
from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel


@pytest.fixture(autouse=True)
def _auto_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default settings: auto visibility, the default transport, no cap, no proxy."""
    from gflow_cli.api import flow_wire

    for name in (
        "GFLOW_CLI_BROWSER_VISIBILITY",
        "GFLOW_CLI_HEADLESS",
        "GFLOW_CLI_TRANSPORT",
        "GFLOW_CLI_MAX_CREDITS",
        "GFLOW_CLI_FLOW_HOST",
        "GFLOW_CLI_BROWSER_PROXY",
        "GFLOW_CLI_UI_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    config.reset_settings()
    yield
    config.reset_settings()


def measured_t2v(**overrides: object) -> GenerateVideoRequest:
    fields: dict[str, object] = {
        "prompt": "a lighthouse at dusk",
        "mode": Mode.T2V,
        "model": VideoModel.OMNI_1_2_FLASH,
        "duration": 4,
        "resolution": "360p",
        "aspect": Aspect.LANDSCAPE,
    }
    fields.update(overrides)
    return GenerateVideoRequest(**fields)  # type: ignore[arg-type]


def measured_t2i(**overrides: object) -> GenerateImageRequest:
    fields: dict[str, object] = {
        "prompt": "a lighthouse at dusk",
        "model": ImageModel.NARWHAL,
        "aspect": ImageAspect.LANDSCAPE,
    }
    fields.update(overrides)
    return GenerateImageRequest(**fields)  # type: ignore[arg-type]


# --- the helper ---------------------------------------------------------------------------


def test_ui_only_construction_is_headed_under_auto() -> None:
    assert client_visibility(config.get_settings()) is BrowserVisibility.HEADED


def test_measured_shapes_route_direct_and_launch_headless() -> None:
    routes = (video_route(measured_t2v()), image_route(measured_t2i()))
    assert routes == (Route.DIRECT, Route.DIRECT)
    assert client_visibility(config.get_settings(), routes) is BrowserVisibility.HEADLESS


def test_unmeasured_video_shape_routes_ui() -> None:
    assert video_route(measured_t2v(model=VideoModel.VEO_3_1_FAST, duration=8)) is Route.UI


def test_capped_video_routes_ui_from_the_request_or_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert video_route(measured_t2v(max_credits=50)) is Route.UI
    monkeypatch.setenv("GFLOW_CLI_MAX_CREDITS", "50")
    config.reset_settings()
    assert video_route(measured_t2v()) is Route.UI


def test_mention_in_a_video_prompt_routes_ui() -> None:
    """A mention resolves to a reference after launch, which the direct path cannot send."""
    assert video_route(measured_t2v(prompt="@Ada walks on the beach")) is Route.UI


def test_kill_switch_routes_every_shape_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    assert video_route(measured_t2v(), transport="ui_automation") is Route.UI
    assert image_route(measured_t2i(), transport="ui_automation") is Route.UI
    monkeypatch.setenv("GFLOW_CLI_TRANSPORT", "ui_automation")
    assert video_route(measured_t2v()) is Route.UI
    row = {
        "flow_workflow_id": "wf",
        "model": "omni_1_2_flash",
        "aspect": "landscape",
        "duration_seconds": 4,
    }
    assert upscale_route("1080p", row) is Route.UI


def test_agentic_ui_mode_routes_images_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_UI_MODE", "agentic")
    config.reset_settings()
    assert image_route(measured_t2i()) is Route.UI


def test_proxy_routes_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    from gflow_cli.api import flow_wire

    monkeypatch.setattr(flow_wire, "getproxies", lambda: {"https": "http://proxy:8080"})
    assert video_route(measured_t2v()) is Route.UI
    assert image_route(measured_t2i()) is Route.UI


def test_explicit_headless_setting_keeps_its_meaning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GFLOW_CLI_BROWSER_VISIBILITY", "headless")
    config.reset_settings()
    assert client_visibility(config.get_settings()) is BrowserVisibility.HEADLESS


async def test_empty_balance_refused_before_launch_for_a_direct_video(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli.api import flow_wire
    from gflow_cli.errors import InsufficientCreditsError, exit_code_for

    async def zero(_profile_dir: Path) -> object:
        return type("Info", (), {"credits": 0})()

    monkeypatch.setattr(flow_wire, "read_credits", zero)
    with pytest.raises(InsufficientCreditsError) as exc:
        await video_client_visibility(config.get_settings(), tmp_path, (Route.DIRECT,))
    assert exit_code_for(exc.value) == 37


async def test_balance_not_read_when_every_clip_routes_ui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gflow_cli.api import flow_wire

    async def boom(_profile_dir: Path) -> object:
        raise AssertionError("the balance is read only for a planned direct clip")

    monkeypatch.setattr(flow_wire, "read_credits", boom)
    visibility = await video_client_visibility(config.get_settings(), tmp_path, (Route.UI,))
    assert visibility is BrowserVisibility.HEADED
