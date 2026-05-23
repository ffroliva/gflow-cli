"""Pure tests for video value objects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gflow_cli.api.video import (
    MAX_REFERENCE_IMAGES,
    Aspect,
    GenerateVideoRequest,
    Mode,
    Tier,
    VideoModel,
    VideoResult,
    VideoStatus,
    media_name_from_generate_response,
    parse_video_status,
)


class TestVideoModelEnum:
    def test_from_cli_none_returns_none(self) -> None:
        assert VideoModel.from_cli(None) is None

    def test_from_cli_aliases(self) -> None:
        assert VideoModel.from_cli("omni-flash") is VideoModel.OMNI_FLASH
        assert VideoModel.from_cli("veo-fast") is VideoModel.VEO_3_1_FAST
        assert VideoModel.from_cli("veo-quality") is VideoModel.VEO_3_1_QUALITY
        assert VideoModel.from_cli("veo-lite") is VideoModel.VEO_3_1_LITE
        assert VideoModel.from_cli("veo-lite-lp") is VideoModel.VEO_3_1_LITE_LOWER_PRIORITY

    def test_from_cli_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown video model"):
            VideoModel.from_cli("sora")


class TestVideoRequestNewFields:
    def test_defaults(self) -> None:
        req = GenerateVideoRequest(prompt="x")
        assert req.model is None
        assert req.duration is None
        assert req.count == 1

    def test_invalid_duration_rejected(self) -> None:
        with pytest.raises(ValueError, match="duration must be"):
            GenerateVideoRequest(prompt="x", duration=5)

    def test_10s_requires_omni_flash(self) -> None:
        with pytest.raises(ValueError, match="10s duration"):
            GenerateVideoRequest(prompt="x", duration=10, model=VideoModel.VEO_3_1_FAST)
        # omni_flash + 10s, and model-less 10s (default unknown), are both OK
        GenerateVideoRequest(prompt="x", duration=10, model=VideoModel.OMNI_FLASH)
        GenerateVideoRequest(prompt="x", duration=10)

    def test_count_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="count must be"):
            GenerateVideoRequest(prompt="x", count=5)
        with pytest.raises(ValueError, match="count must be"):
            GenerateVideoRequest(prompt="x", count=0)


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

    def test_per_model_reference_cap(self) -> None:
        refs5 = tuple(Path(f"r{i}.png") for i in range(5))
        refs7 = tuple(Path(f"r{i}.png") for i in range(7))
        # veo caps at 3 -> 5 refs rejected
        with pytest.raises(ValueError, match="at most 3 reference"):
            GenerateVideoRequest(
                prompt="x", mode=Mode.R2V, model=VideoModel.VEO_3_1_FAST, reference_images=refs5
            )
        # omni_flash allows 7
        GenerateVideoRequest(
            prompt="x", mode=Mode.R2V, model=VideoModel.OMNI_FLASH, reference_images=refs7
        )
        # model None -> only the absolute ceiling (7) applies; 5 is fine
        GenerateVideoRequest(prompt="x", mode=Mode.R2V, reference_images=refs5)

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


_CAPTURES = Path(__file__).parent.parent.parent / "samples" / "captured"


def _body(filename: str) -> dict:
    """Load the response body the parsers consume from a committed capture.

    NOTE: the capture sanitizer redacts media ids inconsistently — capture 02
    uses `<UUID>`, captures 08/09/10/11 use `<GENERATED_MEDIA_ID>`. The
    assertions below match each file's actual token; a re-sanitization that
    unifies them would require updating these expected values.
    """
    raw = json.loads((_CAPTURES / filename).read_text(encoding="utf-8"))
    return raw["response_body_parsed"]


class TestMediaNameFromGenerateResponse:
    def test_t2v_capture(self) -> None:
        name = media_name_from_generate_response(_body("02_batchAsyncGenerateVideoText.json"))
        assert name == "<UUID>"

    def test_i2v_capture(self) -> None:
        name = media_name_from_generate_response(
            _body("08_batchAsyncGenerateVideoStartAndEndImage.json")
        )
        assert name == "<GENERATED_MEDIA_ID>"

    def test_r2v_capture(self) -> None:
        name = media_name_from_generate_response(
            _body("09_batchAsyncGenerateVideoReferenceImages.json")
        )
        assert name == "<GENERATED_MEDIA_ID>"

    def test_missing_media_raises(self) -> None:
        with pytest.raises(ValueError, match="no media"):
            media_name_from_generate_response({"workflows": []})


class TestParseVideoStatus:
    def test_successful_capture(self) -> None:
        s = parse_video_status(
            _body("10_batchCheckAsyncVideoGenerationStatus_successful.json"),
            media_id="<GENERATED_MEDIA_ID>",
        )
        assert s.status == "MEDIA_GENERATION_STATUS_SUCCESSFUL"
        assert s.is_terminal is True
        assert s.succeeded is True
        assert s.failure_reasons == ()
        assert s.error_message is None

    def test_failed_capture(self) -> None:
        s = parse_video_status(
            _body("11_batchCheckAsyncVideoGenerationStatus_failed.json"),
            media_id="<GENERATED_MEDIA_ID>",
        )
        assert s.status == "MEDIA_GENERATION_STATUS_FAILED"
        assert s.is_terminal is True
        assert s.succeeded is False
        assert s.failure_reasons == ("IP_PROHIBITED",)
        assert s.error_message == "PUBLIC_ERROR_IP_INPUT_IMAGE"

    def test_media_id_not_in_response_raises(self) -> None:
        with pytest.raises(ValueError, match="not found"):
            parse_video_status(
                _body("10_batchCheckAsyncVideoGenerationStatus_successful.json"),
                media_id="no-such-id",
            )

    def test_malformed_status_raises(self) -> None:
        with pytest.raises(ValueError, match="mediaGenerationStatus"):
            parse_video_status({"media": [{"name": "m", "mediaMetadata": {}}]}, media_id="m")


class TestVideoResult:
    def test_video_result_holds_fields(self) -> None:
        status = VideoStatus(media_id="abc-123", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
        result = VideoResult(status=status, local_path=Path("/tmp/abc-123.mp4"))
        assert result.status is status
        assert result.local_path == Path("/tmp/abc-123.mp4")

    def test_video_result_no_path_when_failed(self) -> None:
        status = VideoStatus(media_id="abc-123", status="MEDIA_GENERATION_STATUS_FAILED")
        result = VideoResult(status=status, local_path=None)
        assert result.local_path is None
        assert not result.status.succeeded

    def test_video_result_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        status = VideoStatus(media_id="x", status="MEDIA_GENERATION_STATUS_SUCCESSFUL")
        result = VideoResult(status=status, local_path=None)
        with pytest.raises(FrozenInstanceError):
            result.local_path = Path("/tmp/other.mp4")  # type: ignore[misc]
