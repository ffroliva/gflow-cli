"""Tests for generation route predicate and measured allowlists."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from gflow_cli.api.generation_route import (
    IMAGE_WIRE_MODELS,
    MEASURED_VIDEO_KEYS,
    Route,
    VideoWireTarget,
    choose_image_route,
    choose_upscale_route,
    choose_video_route,
    direct_can_serve,
    plan_visibility,
)
from gflow_cli.api.image import (
    Aspect as ImageAspect,
)
from gflow_cli.api.image import (
    GenerateImageRequest,
    ImageRef,
)
from gflow_cli.api.image import (
    Model as ImageModel,
)
from gflow_cli.api.submit_policy import BrowserVisibility, VisibilitySetting
from gflow_cli.api.video import (
    Aspect as VideoAspect,
)
from gflow_cli.api.video import (
    GenerateVideoRequest,
    Mode,
    VideoModel,
)
from gflow_cli.config import UiMode


@dataclass
class FakeCatalogRow:
    flow_workflow_id: str | None = "wf-123"
    aspect_ratio: str | None = "16:9"
    model: str | None = "macaron_cookie_t2v_4s_360p"
    workflow_id: str | None = None
    aspect: str | None = None

    def __post_init__(self) -> None:
        if self.workflow_id is None:
            self.workflow_id = self.flow_workflow_id
        if self.aspect is None:
            self.aspect = self.aspect_ratio


def test_route_never_reads_settings_or_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route functions take environmental facts explicitly and never touch settings/proxies."""

    def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Forbidden settings/proxy read in pure route domain module")

    _explode.cache_clear = lambda: None  # type: ignore[attr-defined]

    import urllib.request

    import gflow_cli.config

    monkeypatch.setattr(gflow_cli.config, "get_settings", _explode)
    monkeypatch.setattr(urllib.request, "getproxies", _explode)

    img_req = GenerateImageRequest(prompt="test", model=ImageModel.NARWHAL)
    vid_req = GenerateVideoRequest(
        prompt="test",
        mode=Mode.T2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=4,
        resolution="360p",
        aspect=VideoAspect.LANDSCAPE,
    )
    row = FakeCatalogRow()

    assert choose_image_route(img_req, flow_host="", proxy_configured=False) == Route.DIRECT
    assert (
        choose_video_route(
            vid_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.DIRECT
    )
    assert choose_upscale_route("1080p", row, flow_host="", proxy_configured=False) == Route.DIRECT


def test_route_functions_have_no_environment_defaults() -> None:
    """Environmental parameters must be required keyword-only with no default."""
    import inspect

    expected_params: dict[object, set[str]] = {
        choose_image_route: {"flow_host", "proxy_configured"},
        choose_video_route: {"flow_host", "proxy_configured", "effective_max_credits"},
        choose_upscale_route: {"flow_host", "proxy_configured"},
    }

    for fn, params in expected_params.items():
        assert callable(fn)
        sig = inspect.signature(fn)
        for param_name in params:
            assert param_name in sig.parameters, f"{fn.__name__} missing parameter {param_name}"
            param = sig.parameters[param_name]
            assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"{fn.__name__}.{param_name} must be keyword-only"
            )
            assert param.default is inspect.Parameter.empty, (
                f"{fn.__name__}.{param_name} must not have a default (was {param.default!r})"
            )


def test_extend_and_edit_video_route_ui() -> None:
    """Extend and edit video requests route UI."""
    from gflow_cli.api.video import EditVideoRequest
    from gflow_cli.api.video_extend import ExtendVideoRequest

    extend_req = ExtendVideoRequest(
        media_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        scene_id="00000000-0000-0000-0000-000000000003",
        position=0,
        prompt="extend clip",
        model_key="veo_3_1_extension_lite",
        aspect="16:9",
    )
    assert (
        choose_video_route(
            extend_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )

    edit_req = EditVideoRequest(
        prompt="edit clip",
        source_media_id="00000000-0000-0000-0000-000000000001",
    )
    assert (
        choose_video_route(
            edit_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )


def test_unmeasured_shapes_route_ui() -> None:
    """r2v, extend, edit, end frame, ingredients, 4K upscale, Omni i2v at 9:16 route UI."""
    from gflow_cli.api.video import EditVideoRequest
    from gflow_cli.api.video_extend import ExtendVideoRequest

    # R2V
    r2v_req = GenerateVideoRequest(
        prompt="test r2v",
        mode=Mode.R2V,
        reference_images=(Path("ref.png"),),
    )
    assert (
        choose_video_route(
            r2v_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )

    # Extend
    extend_req = ExtendVideoRequest(
        media_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        scene_id="00000000-0000-0000-0000-000000000003",
        position=0,
        prompt="extend clip",
        model_key="veo_3_1_extension_lite",
        aspect="16:9",
    )
    assert (
        choose_video_route(
            extend_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )

    # Edit
    edit_req = EditVideoRequest(
        prompt="edit clip",
        source_media_id="00000000-0000-0000-0000-000000000001",
    )
    assert (
        choose_video_route(
            edit_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )

    # End frame on I2V
    end_frame_req = GenerateVideoRequest(
        prompt="test end frame",
        mode=Mode.I2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=4,
        resolution="360p",
        aspect=VideoAspect.LANDSCAPE,
        start_image=Path("start.png"),
        end_image=Path("end.png"),
    )
    assert (
        choose_video_route(
            end_frame_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )

    # Ingredients (reference images on R2V)
    ingredients_req = GenerateVideoRequest(
        prompt="test ingredients",
        mode=Mode.R2V,
        reference_images=(Path("ingredient.png"),),
    )
    assert (
        choose_video_route(
            ingredients_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )

    # Character reference entities (on R2V)
    char_req = GenerateVideoRequest(
        prompt="test character",
        mode=Mode.R2V,
        reference_entities=("char-uuid",),
        reference_entity_names=("CharName",),
    )
    assert (
        choose_video_route(
            char_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )

    # 4K upscale
    assert (
        choose_upscale_route("4K", FakeCatalogRow(), flow_host="", proxy_configured=False)
        == Route.UI
    )

    # Omni I2V at 9:16 (unmeasured: only 16:9 is measured)
    omni_i2v_portrait = GenerateVideoRequest(
        prompt="test omni i2v portrait",
        mode=Mode.I2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=4,
        resolution="360p",
        aspect=VideoAspect.PORTRAIT,
        start_image=Path("start.png"),
    )
    assert (
        choose_video_route(
            omni_i2v_portrait, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )


def test_credit_capped_video_routes_ui() -> None:
    """A credit cap on video routes UI because direct video has no wire quote."""
    # Cap on request
    req_capped = GenerateVideoRequest(
        prompt="test capped",
        mode=Mode.T2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=4,
        resolution="360p",
        aspect=VideoAspect.LANDSCAPE,
        max_credits=10,
    )
    assert (
        choose_video_route(
            req_capped, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )

    # Cap passed as effective_max_credits
    req_uncapped = GenerateVideoRequest(
        prompt="test uncapped",
        mode=Mode.T2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=4,
        resolution="360p",
        aspect=VideoAspect.LANDSCAPE,
    )
    assert (
        choose_video_route(
            req_uncapped, flow_host="", proxy_configured=False, effective_max_credits=10
        )
        == Route.UI
    )

    # No cap routes direct
    assert (
        choose_video_route(
            req_uncapped, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.DIRECT
    )


test_capped_video_routes_ui = test_credit_capped_video_routes_ui


def test_video_direct_only_for_exact_measured_tuple() -> None:
    """Exact measured tuples route DIRECT; unmeasured combinations route UI."""
    # 1. Omni 1.2 Flash T2V 4s 360p 16:9 -> DIRECT
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.T2V,
                model=VideoModel.OMNI_1_2_FLASH,
                duration=4,
                resolution="360p",
                aspect=VideoAspect.LANDSCAPE,
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.DIRECT
    )

    # 2. Omni 1.2 Flash T2V 4s 360p 9:16 -> DIRECT
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.T2V,
                model=VideoModel.OMNI_1_2_FLASH,
                duration=4,
                resolution="360p",
                aspect=VideoAspect.PORTRAIT,
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.DIRECT
    )

    # 3. Omni 1.2 Flash I2V 4s 360p 16:9 -> DIRECT
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.I2V,
                model=VideoModel.OMNI_1_2_FLASH,
                duration=4,
                resolution="360p",
                aspect=VideoAspect.LANDSCAPE,
                start_image=Path("start.png"),
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.DIRECT
    )

    # 4. Veo 3.1 Lite T2V None None 16:9 -> DIRECT
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.T2V,
                model=VideoModel.VEO_3_1_LITE,
                duration=None,
                resolution=None,
                aspect=VideoAspect.LANDSCAPE,
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.DIRECT
    )

    # 5. Veo 3.1 Lite I2V None None 9:16 -> DIRECT
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.I2V,
                model=VideoModel.VEO_3_1_LITE,
                duration=None,
                resolution=None,
                aspect=VideoAspect.PORTRAIT,
                start_image=Path("start.png"),
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.DIRECT
    )

    # Unmeasured: Veo 3.1 Fast
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.T2V,
                model=VideoModel.VEO_3_1_FAST,
                aspect=VideoAspect.LANDSCAPE,
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.UI
    )

    # Unmeasured: Veo 3.1 Quality
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.T2V,
                model=VideoModel.VEO_3_1_QUALITY,
                aspect=VideoAspect.LANDSCAPE,
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.UI
    )

    # Unmeasured: Omni Flash (legacy)
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.T2V,
                model=VideoModel.OMNI_FLASH,
                duration=4,
                resolution="360p",
                aspect=VideoAspect.LANDSCAPE,
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.UI
    )

    # Unmeasured: duration 6s for Omni 1.2 Flash
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.T2V,
                model=VideoModel.OMNI_1_2_FLASH,
                duration=6,
                resolution="360p",
                aspect=VideoAspect.LANDSCAPE,
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.UI
    )

    # Unmeasured: resolution 720p for Omni 1.2 Flash
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.T2V,
                model=VideoModel.OMNI_1_2_FLASH,
                duration=4,
                resolution="720p",
                aspect=VideoAspect.LANDSCAPE,
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.UI
    )

    # Count > 1 routes UI
    assert (
        choose_video_route(
            GenerateVideoRequest(
                prompt="p",
                mode=Mode.T2V,
                model=VideoModel.OMNI_1_2_FLASH,
                duration=4,
                resolution="360p",
                aspect=VideoAspect.LANDSCAPE,
                count=2,
            ),
            flow_host="",
            proxy_configured=False,
            effective_max_credits=None,
        )
        == Route.UI
    )


def test_i2v_model_none_normalises_to_veo_3_1_i2v_lite_and_routes_direct() -> None:
    """I2V with model=None normalises to VEO_3_1_LITE and routes DIRECT; T2V None routes UI."""
    # I2V with model=None at 9:16
    i2v_req = GenerateVideoRequest(
        prompt="p",
        mode=Mode.I2V,
        model=None,
        aspect=VideoAspect.PORTRAIT,
        start_image=Path("start.png"),
    )
    assert (
        choose_video_route(
            i2v_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.DIRECT
    )

    # T2V with model=None routes UI
    t2v_req = GenerateVideoRequest(
        prompt="p",
        mode=Mode.T2V,
        model=None,
        aspect=VideoAspect.LANDSCAPE,
    )
    assert (
        choose_video_route(
            t2v_req, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )


def test_omni_with_unset_duration_or_resolution_routes_ui() -> None:
    """Omni tuple with duration or resolution left None routes UI (unlisted in table)."""
    # Unset duration
    req_no_dur = GenerateVideoRequest(
        prompt="p",
        mode=Mode.T2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=None,
        resolution="360p",
        aspect=VideoAspect.LANDSCAPE,
    )
    assert (
        choose_video_route(
            req_no_dur, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )

    # Unset resolution
    req_no_res = GenerateVideoRequest(
        prompt="p",
        mode=Mode.T2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=4,
        resolution=None,
        aspect=VideoAspect.LANDSCAPE,
    )
    assert (
        choose_video_route(
            req_no_res, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )

    # Unset both
    req_neither = GenerateVideoRequest(
        prompt="p",
        mode=Mode.T2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=None,
        resolution=None,
        aspect=VideoAspect.LANDSCAPE,
    )
    assert (
        choose_video_route(
            req_neither, flow_host="", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )


def test_proxy_routes_ui() -> None:
    """A configured browser proxy forces image and video routes to UI."""
    img_req = GenerateImageRequest(prompt="test", model=ImageModel.NARWHAL)
    vid_req = GenerateVideoRequest(
        prompt="test",
        mode=Mode.T2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=4,
        resolution="360p",
        aspect=VideoAspect.LANDSCAPE,
    )
    assert choose_image_route(img_req, flow_host="", proxy_configured=True) == Route.UI
    assert (
        choose_video_route(vid_req, flow_host="", proxy_configured=True, effective_max_credits=None)
        == Route.UI
    )


def test_labs_host_routes_ui() -> None:
    """Flow host labs.google forces image and video routes to UI."""
    img_req = GenerateImageRequest(prompt="test", model=ImageModel.NARWHAL)
    vid_req = GenerateVideoRequest(
        prompt="test",
        mode=Mode.T2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=4,
        resolution="360p",
        aspect=VideoAspect.LANDSCAPE,
    )
    assert choose_image_route(img_req, flow_host="labs.google", proxy_configured=False) == Route.UI
    assert (
        choose_video_route(
            vid_req, flow_host="labs.google", proxy_configured=False, effective_max_credits=None
        )
        == Route.UI
    )


def test_upscale_source_not_in_catalog_routes_ui() -> None:
    """choose_upscale_route routes DIRECT only for 1080p and measured catalog source."""
    # Not in catalog
    assert choose_upscale_route("1080p", None, flow_host="", proxy_configured=False) == Route.UI

    # Missing workflow_id
    assert (
        choose_upscale_route(
            "1080p",
            FakeCatalogRow(workflow_id=None, flow_workflow_id=None),
            flow_host="",
            proxy_configured=False,
        )
        == Route.UI
    )

    # Missing aspect
    assert (
        choose_upscale_route(
            "1080p",
            FakeCatalogRow(aspect=None, aspect_ratio=None),
            flow_host="",
            proxy_configured=False,
        )
        == Route.UI
    )

    # Unmeasured model sources
    assert (
        choose_upscale_route(
            "1080p",
            FakeCatalogRow(model="veo_3_1_lite"),
            flow_host="",
            proxy_configured=False,
        )
        == Route.UI
    )
    assert (
        choose_upscale_route(
            "1080p",
            FakeCatalogRow(model="veo_3_1_quality"),
            flow_host="",
            proxy_configured=False,
        )
        == Route.UI
    )

    # Measured source clips: 1080p -> DIRECT
    assert (
        choose_upscale_route(
            "1080p",
            FakeCatalogRow(model="macaron_cookie_t2v_4s_360p"),
            flow_host="",
            proxy_configured=False,
        )
        == Route.DIRECT
    )
    assert (
        choose_upscale_route(
            "1080p",
            FakeCatalogRow(model="macaron_cookie_i2v_4s_360p"),
            flow_host="",
            proxy_configured=False,
        )
        == Route.DIRECT
    )

    # Non-1080p scales -> UI
    assert (
        choose_upscale_route(
            "4K",
            FakeCatalogRow(model="macaron_cookie_t2v_4s_360p"),
            flow_host="",
            proxy_configured=False,
        )
        == Route.UI
    )
    assert (
        choose_upscale_route(
            "720p",
            FakeCatalogRow(model="macaron_cookie_t2v_4s_360p"),
            flow_host="",
            proxy_configured=False,
        )
        == Route.UI
    )
    assert (
        choose_upscale_route(
            "GIF",
            FakeCatalogRow(model="macaron_cookie_t2v_4s_360p"),
            flow_host="",
            proxy_configured=False,
        )
        == Route.UI
    )


def test_plan_visibility_headed_when_any_route_is_ui() -> None:
    """plan_visibility selects HEADED if any route is UI under AUTO; otherwise HEADLESS."""
    # AUTO with mixed or UI routes -> HEADED
    assert (
        plan_visibility(VisibilitySetting.AUTO, [Route.DIRECT, Route.UI])
        == BrowserVisibility.HEADED
    )
    assert (
        plan_visibility(VisibilitySetting.AUTO, [Route.UI, Route.DIRECT])
        == BrowserVisibility.HEADED
    )
    assert plan_visibility(VisibilitySetting.AUTO, [Route.UI]) == BrowserVisibility.HEADED

    # AUTO with all DIRECT routes -> HEADLESS
    assert plan_visibility(VisibilitySetting.AUTO, [Route.DIRECT]) == BrowserVisibility.HEADLESS
    assert (
        plan_visibility(VisibilitySetting.AUTO, [Route.DIRECT, Route.DIRECT])
        == BrowserVisibility.HEADLESS
    )
    assert plan_visibility(VisibilitySetting.AUTO, []) == BrowserVisibility.HEADLESS

    # HEADED pins HEADED regardless of routes
    assert plan_visibility(VisibilitySetting.HEADED, [Route.DIRECT]) == BrowserVisibility.HEADED
    assert plan_visibility(VisibilitySetting.HEADED, [Route.UI]) == BrowserVisibility.HEADED
    assert plan_visibility(VisibilitySetting.HEADED, []) == BrowserVisibility.HEADED


def test_auto_ui_route_launches_headed() -> None:
    """Every UI-bound shape launches HEADED under AUTO."""
    # R2V
    r2v_req = GenerateVideoRequest(prompt="p", mode=Mode.R2V, reference_images=(Path("r.png"),))
    assert (
        plan_visibility(
            VisibilitySetting.AUTO,
            [
                choose_video_route(
                    r2v_req, flow_host="", proxy_configured=False, effective_max_credits=None
                )
            ],
        )
        == BrowserVisibility.HEADED
    )

    # End-frame I2V
    end_frame_req = GenerateVideoRequest(
        prompt="p",
        mode=Mode.I2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=4,
        resolution="360p",
        aspect=VideoAspect.LANDSCAPE,
        start_image=Path("s.png"),
        end_image=Path("end.png"),
    )
    assert (
        plan_visibility(
            VisibilitySetting.AUTO,
            [
                choose_video_route(
                    end_frame_req,
                    flow_host="",
                    proxy_configured=False,
                    effective_max_credits=None,
                )
            ],
        )
        == BrowserVisibility.HEADED
    )

    # 4K / 720p / GIF upscale
    valid_row = FakeCatalogRow(model="macaron_cookie_t2v_4s_360p")
    assert (
        plan_visibility(
            VisibilitySetting.AUTO,
            [choose_upscale_route("4K", valid_row, flow_host="", proxy_configured=False)],
        )
        == BrowserVisibility.HEADED
    )
    assert (
        plan_visibility(
            VisibilitySetting.AUTO,
            [choose_upscale_route("720p", valid_row, flow_host="", proxy_configured=False)],
        )
        == BrowserVisibility.HEADED
    )
    assert (
        plan_visibility(
            VisibilitySetting.AUTO,
            [choose_upscale_route("GIF", valid_row, flow_host="", proxy_configured=False)],
        )
        == BrowserVisibility.HEADED
    )

    # Local --ref I2I (ref_paths or catalogued local path ref)
    local_ref_req = GenerateImageRequest(
        prompt="p",
        model=ImageModel.NARWHAL,
        ref_paths=[Path("local.png")],
    )
    assert (
        plan_visibility(
            VisibilitySetting.AUTO,
            [choose_image_route(local_ref_req, flow_host="", proxy_configured=False)],
        )
        == BrowserVisibility.HEADED
    )
    local_ref_obj_req = GenerateImageRequest(
        prompt="p",
        model=ImageModel.NARWHAL,
        refs=[ImageRef("ref-uuid", local_path="local.png", local_sha256="sha256abc")],
    )
    assert (
        plan_visibility(
            VisibilitySetting.AUTO,
            [choose_image_route(local_ref_obj_req, flow_host="", proxy_configured=False)],
        )
        == BrowserVisibility.HEADED
    )

    # Capped video
    capped_vid = GenerateVideoRequest(
        prompt="p",
        mode=Mode.T2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=4,
        resolution="360p",
        aspect=VideoAspect.LANDSCAPE,
        max_credits=5,
    )
    assert (
        plan_visibility(
            VisibilitySetting.AUTO,
            [
                choose_video_route(
                    capped_vid, flow_host="", proxy_configured=False, effective_max_credits=None
                )
            ],
        )
        == BrowserVisibility.HEADED
    )


def test_explicit_headless_keeps_ui_shapes_headless() -> None:
    """Explicit HEADLESS setting stays HEADLESS even for UI routes."""
    assert plan_visibility(VisibilitySetting.HEADLESS, [Route.UI]) == BrowserVisibility.HEADLESS
    assert plan_visibility(VisibilitySetting.HEADLESS, [Route.DIRECT]) == BrowserVisibility.HEADLESS
    assert plan_visibility("headless", [Route.UI]) == BrowserVisibility.HEADLESS

    # Unmeasured UI shape under explicit headless
    r2v_req = GenerateVideoRequest(prompt="p", mode=Mode.R2V, reference_images=(Path("r.png"),))
    assert (
        plan_visibility(
            VisibilitySetting.HEADLESS,
            [
                choose_video_route(
                    r2v_req, flow_host="", proxy_configured=False, effective_max_credits=None
                )
            ],
        )
        == BrowserVisibility.HEADLESS
    )


def test_measured_video_keys_aspect_is_strictly_video_aspect() -> None:
    """Allowlist keys are keyed on VideoAspect only; string aspect aliases are rejected."""
    for key in MEASURED_VIDEO_KEYS:
        aspect = key[4]
        assert isinstance(aspect, VideoAspect)
        assert type(aspect) is VideoAspect


def test_measured_keys_exist_in_htrjv_fixture() -> None:
    """Every wire key in MEASURED_VIDEO_KEYS and IMAGE_WIRE_MODELS exists in HTrJv fixture."""
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "flow_htrjv_model_families.json"
    )
    assert fixture_path.exists(), f"Fixture missing: {fixture_path}"

    fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture_keys: set[str] = set()
    for family in fixture_data.get("families", []):
        if len(family) > 1 and isinstance(family[1], list):
            for entry in family[1]:
                if entry and isinstance(entry, list) and isinstance(entry[0], str):
                    fixture_keys.add(entry[0])

    # Check video wire keys
    assert MEASURED_VIDEO_KEYS, "MEASURED_VIDEO_KEYS must not be empty"
    for target in MEASURED_VIDEO_KEYS.values():
        wire_key = target.wire_key if isinstance(target, VideoWireTarget) else target[0]
        assert wire_key in fixture_keys, f"Video wire key {wire_key!r} not in HTrJv fixture"

    # Check image wire models
    assert IMAGE_WIRE_MODELS, "IMAGE_WIRE_MODELS must not be empty"
    for img_model_name in IMAGE_WIRE_MODELS.values():
        assert img_model_name in fixture_keys, (
            f"Image wire model {img_model_name!r} not in HTrJv fixture"
        )


def test_gem_pix_2_route_mirrors_harbor_seal() -> None:
    """GEM_PIX_2 routes DIRECT only for 9:16 n1; 16:9, n2, or refs route UI."""
    # 9:16 (portrait) n1 -> direct
    req_direct = GenerateImageRequest(
        prompt="a cat",
        model=ImageModel.GEM_PIX_2,
        aspect=ImageAspect.PORTRAIT,
        count=1,
    )
    assert choose_image_route(req_direct, flow_host="", proxy_configured=False) == Route.DIRECT

    # 16:9 (landscape) -> UI
    req_land = GenerateImageRequest(
        prompt="a cat",
        model=ImageModel.GEM_PIX_2,
        aspect=ImageAspect.LANDSCAPE,
        count=1,
    )
    assert choose_image_route(req_land, flow_host="", proxy_configured=False) == Route.UI

    # n2 -> UI
    req_n2 = GenerateImageRequest(
        prompt="a cat",
        model=ImageModel.GEM_PIX_2,
        aspect=ImageAspect.PORTRAIT,
        count=2,
    )
    assert choose_image_route(req_n2, flow_host="", proxy_configured=False) == Route.UI

    # reference -> UI
    req_ref = GenerateImageRequest(
        prompt="a cat",
        model=ImageModel.GEM_PIX_2,
        aspect=ImageAspect.PORTRAIT,
        count=1,
        refs=[ImageRef("uuid-1", in_project=True, display_name="img1")],
    )
    assert choose_image_route(req_ref, flow_host="", proxy_configured=False) == Route.UI


def test_direct_can_serve_rules() -> None:
    """direct_can_serve preserves #1549 and #1552 allowlist rules."""
    # Beluga (Narwhal)
    req_beluga = GenerateImageRequest(prompt="a cat", model=ImageModel.NARWHAL)
    assert direct_can_serve(req_beluga) is True

    # Beluga with in-project ref (no local_path, display_name present)
    req_beluga_ref = GenerateImageRequest(
        prompt="a cat",
        model=ImageModel.NARWHAL,
        refs=[ImageRef("uuid-1", in_project=True, display_name="img1")],
    )
    assert direct_can_serve(req_beluga_ref) is True

    # Harbor Seal: portrait count 1, no refs -> True
    req_hs = GenerateImageRequest(
        prompt="a cat",
        model=ImageModel.HARBOR_SEAL,
        aspect=ImageAspect.PORTRAIT,
        count=1,
    )
    assert direct_can_serve(req_hs) is True

    # Harbor Seal: landscape -> False
    req_hs_land = GenerateImageRequest(
        prompt="a cat",
        model=ImageModel.HARBOR_SEAL,
        aspect=ImageAspect.LANDSCAPE,
        count=1,
    )
    assert direct_can_serve(req_hs_land) is False

    # Harbor Seal: count 2 -> False
    req_hs_c2 = GenerateImageRequest(
        prompt="a cat",
        model=ImageModel.HARBOR_SEAL,
        aspect=ImageAspect.PORTRAIT,
        count=2,
    )
    assert direct_can_serve(req_hs_c2) is False

    # Harbor Seal with ref -> False
    req_hs_ref = GenerateImageRequest(
        prompt="a cat",
        model=ImageModel.HARBOR_SEAL,
        aspect=ImageAspect.PORTRAIT,
        count=1,
        refs=[ImageRef("uuid-1", in_project=True, display_name="img1")],
    )
    assert direct_can_serve(req_hs_ref) is False

    # Agentic UI mode -> False
    req_agentic = GenerateImageRequest(
        prompt="a cat",
        model=ImageModel.NARWHAL,
        ui_mode=UiMode.AGENTIC,
    )
    assert direct_can_serve(req_agentic) is False

    # Raw @ in prompt -> False
    req_at = GenerateImageRequest(
        prompt="a cat @character",
        model=ImageModel.NARWHAL,
    )
    assert direct_can_serve(req_at) is False
