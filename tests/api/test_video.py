"""Pure tests for video value objects."""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.video import (
    MAX_REFERENCE_IMAGES,
    Aspect,
    GenerateVideoRequest,
    Mode,
    Tier,
    VideoStatus,
)


class TestAspectEnum:
    def test_portrait_wire_value(self) -> None:
        assert Aspect.PORTRAIT.wire() == "VIDEO_ASPECT_RATIO_PORTRAIT"

    def test_landscape_wire_value(self) -> None:
        assert Aspect.LANDSCAPE.wire() == "VIDEO_ASPECT_RATIO_LANDSCAPE"

    def test_square_wire_value(self) -> None:
        assert Aspect.SQUARE.wire() == "VIDEO_ASPECT_RATIO_SQUARE"

    def test_from_cli_value(self) -> None:
        assert Aspect.from_cli("9:16") == Aspect.PORTRAIT
        assert Aspect.from_cli("16:9") == Aspect.LANDSCAPE
        assert Aspect.from_cli("1:1") == Aspect.SQUARE

    def test_from_cli_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="3:2"):
            Aspect.from_cli("3:2")


class TestMode:
    def test_has_r2v(self) -> None:
        assert Mode.R2V == "r2v"

    def test_three_modes(self) -> None:
        assert {m.value for m in Mode} == {"t2v", "i2v", "r2v"}


class TestGenerateVideoRequest:
    def test_t2v_defaults(self) -> None:
        req = GenerateVideoRequest(prompt="a calm forest at dawn")
        assert req.mode is Mode.T2V
        assert req.aspect is Aspect.PORTRAIT
        assert req.tier is Tier.FAST
        assert req.start_image is None
        assert req.reference_images == ()

    def test_empty_prompt_rejected(self) -> None:
        with pytest.raises(ValueError, match="prompt must not be empty"):
            GenerateVideoRequest(prompt="   ")

    def test_t2v_must_not_carry_image_inputs(self) -> None:
        with pytest.raises(ValueError, match="T2V request must not carry image inputs"):
            GenerateVideoRequest(prompt="x", mode=Mode.T2V, start_image=Path("a.png"))

    def test_i2v_requires_start_image(self) -> None:
        with pytest.raises(ValueError, match="I2V request requires start_image"):
            GenerateVideoRequest(prompt="x", mode=Mode.I2V)

    def test_i2v_accepts_start_and_optional_end(self) -> None:
        req = GenerateVideoRequest(
            prompt="x", mode=Mode.I2V, start_image=Path("a.png"), end_image=Path("b.png")
        )
        assert req.start_image == Path("a.png")
        assert req.end_image == Path("b.png")

    def test_i2v_must_not_carry_reference_images(self) -> None:
        with pytest.raises(ValueError, match="must not carry reference_images"):
            GenerateVideoRequest(
                prompt="x",
                mode=Mode.I2V,
                start_image=Path("a.png"),
                reference_images=(Path("r.png"),),
            )

    def test_r2v_requires_a_reference_image(self) -> None:
        with pytest.raises(ValueError, match="R2V request requires at least one"):
            GenerateVideoRequest(prompt="x", mode=Mode.R2V)

    def test_r2v_must_not_carry_start_end(self) -> None:
        with pytest.raises(ValueError, match="must not carry start/end"):
            GenerateVideoRequest(
                prompt="x",
                mode=Mode.R2V,
                reference_images=(Path("r.png"),),
                start_image=Path("a.png"),
            )

    def test_too_many_reference_images_rejected(self) -> None:
        too_many = tuple(Path(f"r{i}.png") for i in range(MAX_REFERENCE_IMAGES + 1))
        with pytest.raises(ValueError, match="at most"):
            GenerateVideoRequest(prompt="x", mode=Mode.R2V, reference_images=too_many)

    def test_seed_range_enforced(self) -> None:
        with pytest.raises(ValueError, match="seed out of range"):
            GenerateVideoRequest(prompt="x", seed=-1)
        GenerateVideoRequest(prompt="x", seed=0)  # boundary OK
        GenerateVideoRequest(prompt="x", seed=2**31 - 1)  # boundary OK

    def test_post_init_does_not_touch_the_filesystem(self) -> None:
        # Structural validation only — a non-existent path must NOT raise.
        GenerateVideoRequest(prompt="x", mode=Mode.I2V, start_image=Path("does/not/exist.png"))


class TestVideoStatus:
    def test_pending_is_not_terminal(self) -> None:
        s = VideoStatus(media_id="m", status="MEDIA_GENERATION_STATUS_PENDING")
        assert s.is_terminal is False
        assert s.succeeded is False

    def test_active_is_not_terminal(self) -> None:
        s = VideoStatus(media_id="m", status="MEDIA_GENERATION_STATUS_ACTIVE")
        assert s.is_terminal is False

    def test_successful_is_terminal_and_succeeded(self) -> None:
        s = VideoStatus(media_id="m", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
        assert s.is_terminal is True
        assert s.succeeded is True

    def test_failed_is_terminal_not_succeeded(self) -> None:
        s = VideoStatus(
            media_id="m",
            status="MEDIA_GENERATION_STATUS_FAILED",
            failure_reasons=("IP_PROHIBITED",),
            error_message="PUBLIC_ERROR_IP_INPUT_IMAGE",
        )
        assert s.is_terminal is True
        assert s.succeeded is False
        assert s.failure_reasons == ("IP_PROHIBITED",)
        assert s.error_message == "PUBLIC_ERROR_IP_INPUT_IMAGE"
