from pathlib import Path

import pytest

from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    max_duration_for,
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

    def test_duration_allowed_on_veo_models_within_cap(self) -> None:
        for model in (
            VideoModel.VEO_3_1_LITE,
            VideoModel.VEO_3_1_FAST,
            VideoModel.VEO_3_1_QUALITY,
            VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
        ):
            for dur in (4, 6, 8):
                req = GenerateVideoRequest(prompt="x", mode=Mode.T2V, model=model, duration=dur)
                assert req.duration == dur

    def test_duration_10_rejected_on_veo_models(self) -> None:
        for model in (
            VideoModel.VEO_3_1_LITE,
            VideoModel.VEO_3_1_FAST,
            VideoModel.VEO_3_1_QUALITY,
            VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
        ):
            with pytest.raises(ValueError, match="caps at 8s"):
                GenerateVideoRequest(prompt="x", mode=Mode.T2V, model=model, duration=10)

    def test_duration_allowed_on_omni_flash(self) -> None:
        req = GenerateVideoRequest(
            prompt="x", mode=Mode.T2V, model=VideoModel.OMNI_FLASH, duration=10
        )
        assert req.duration == 10

    def test_duration_allowed_when_model_is_unset(self) -> None:
        """T2V leaves Flow's sticky picker default untouched."""
        assert GenerateVideoRequest(prompt="x", mode=Mode.T2V, duration=8).duration == 8

    def test_i2v_default_model_rejects_duration_10(self) -> None:
        with pytest.raises(ValueError, match="caps at 8s"):
            GenerateVideoRequest(
                prompt="x", mode=Mode.I2V, start_image=Path("start.png"), duration=10
            )

    def test_max_duration_matches_the_verified_matrix(self) -> None:
        assert max_duration_for(VideoModel.OMNI_FLASH) == 10
        for model in (
            VideoModel.VEO_3_1_LITE,
            VideoModel.VEO_3_1_FAST,
            VideoModel.VEO_3_1_QUALITY,
            VideoModel.VEO_3_1_LITE_LOWER_PRIORITY,
        ):
            assert max_duration_for(model) == 8

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
