from pathlib import Path

import pytest

from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, VideoModel


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
