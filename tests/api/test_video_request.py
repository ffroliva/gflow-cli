from pathlib import Path

import pytest

from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    reference_cap_for,
)


def test_r2v_valid_with_entities_only() -> None:
    req = GenerateVideoRequest(
        prompt="x",
        mode=Mode.R2V,
        aspect=Aspect.LANDSCAPE,
        model=VideoModel.VEO_3_1_LITE,
        reference_entities=("ent-1",),
    )
    assert req.reference_entities == ("ent-1",)


def test_r2v_valid_with_audio() -> None:
    req = GenerateVideoRequest(
        prompt="x",
        mode=Mode.R2V,
        aspect=Aspect.LANDSCAPE,
        model=VideoModel.VEO_3_1_LITE,
        reference_entities=("ent-1",),
        reference_audio="alnilam",
    )
    assert req.reference_audio == "alnilam"


def test_r2v_requires_images_or_entities() -> None:
    with pytest.raises(ValueError, match="reference_images, ref_names, or reference_entities"):
        GenerateVideoRequest(
            prompt="x", mode=Mode.R2V, aspect=Aspect.LANDSCAPE, model=VideoModel.VEO_3_1_LITE
        )


def test_r2v_accepts_remote_ref_names_alone() -> None:
    # PR #237: a UUID resolved to a remote display name (ref_names) is a valid
    # R2V reference source on its own — no local reference_images required.
    req = GenerateVideoRequest(
        prompt="x",
        mode=Mode.R2V,
        aspect=Aspect.LANDSCAPE,
        model=VideoModel.VEO_3_1_LITE,
        ref_names=("A cozy cabin",),
    )
    assert req.ref_names == ("A cozy cabin",)


def test_cap_budget_counts_entities_plus_images() -> None:
    # veo_3_1 cap = 3; 2 entities + 2 images = 4 > 3
    with pytest.raises(ValueError, match="reference cap"):
        GenerateVideoRequest(
            prompt="x",
            mode=Mode.R2V,
            aspect=Aspect.LANDSCAPE,
            model=VideoModel.VEO_3_1_LITE,
            reference_entities=("a", "b"),
            reference_images=(Path("x.png"), Path("y.png")),
        )


def test_ui_mode_field_defaults_none_and_accepts_enum() -> None:
    # #299 PR-A: the video DTO carries the requested UI arm like the image DTO
    # (api/image.py ui_mode). None -> resolve from GFLOW_CLI_UI_MODE at the
    # transport; never sent on the wire.
    from gflow_cli.config import UiMode

    assert GenerateVideoRequest(prompt="x").ui_mode is None
    req = GenerateVideoRequest(prompt="x", ui_mode=UiMode.CLASSIC)
    assert req.ui_mode is UiMode.CLASSIC


def test_ui_mode_agentic_rejected_at_dto() -> None:
    # #299 code-review finding: the CLI/MCP edges reject agentic with friendly
    # errors, but queue payloads and programmatic use reach the DTO directly —
    # a silent classic clamp there would spend credits on a render the caller
    # believes is agentic. The DTO is the every-producer backstop.
    from gflow_cli.config import UiMode

    with pytest.raises(ValueError, match="agentic"):
        GenerateVideoRequest(prompt="x", ui_mode=UiMode.AGENTIC)


class TestModelCapabilityGuards:
    """#451/#288: Flow's settings popover is model-conditional, so a duration
    that the selected model cannot render must fail at the DTO — not 30s later
    as a UiSelectorDriftError that blames the UI for a capability mismatch."""

    def test_duration_rejected_on_models_without_a_duration_control(self) -> None:
        for model in (
            VideoModel.VEO_3_1_LITE,
            VideoModel.VEO_3_1_FAST,
            VideoModel.VEO_3_1_QUALITY,
        ):
            with pytest.raises(ValueError, match="no duration control"):
                GenerateVideoRequest(prompt="x", mode=Mode.T2V, model=model, duration=8)

    def test_duration_allowed_on_omni_flash(self) -> None:
        req = GenerateVideoRequest(
            prompt="x", mode=Mode.T2V, model=VideoModel.OMNI_FLASH, duration=10
        )
        assert req.duration == 10

    def test_duration_allowed_when_model_is_unset(self) -> None:
        """model=None leaves Flow's picker untouched, so there is no capability
        to check against — the guard must not fire."""
        assert GenerateVideoRequest(prompt="x", mode=Mode.T2V, duration=8).duration == 8

    def test_supports_duration_matches_the_verified_matrix(self) -> None:
        assert VideoModel.OMNI_FLASH.supports_duration()
        assert not VideoModel.VEO_3_1_LITE.supports_duration()
        assert not VideoModel.VEO_3_1_FAST.supports_duration()
        assert not VideoModel.VEO_3_1_QUALITY.supports_duration()

    def test_ingredient_capability_has_exactly_one_source_of_truth(self) -> None:
        """`reference_cap_for` IS the ingredient-capability answer: a cap of 0
        means the model takes no image ingredients. Verified live 2026-08-14 —
        Veo 3.1 Quality refuses them, the others accept. No second predicate
        encodes this rule (one was written, found to have no production caller,
        and deleted)."""
        assert reference_cap_for(VideoModel.VEO_3_1_QUALITY) == 0
        for model in (
            VideoModel.OMNI_FLASH,
            VideoModel.VEO_3_1_FAST,
            VideoModel.VEO_3_1_LITE,
        ):
            assert reference_cap_for(model) > 0
