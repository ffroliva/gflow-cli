"""Pure tests for video value objects + body builders."""

from __future__ import annotations

import pytest

from flow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    Tier,
    build_generate_body,
    model_key,
)


class TestModelKey:
    def test_t2v_fast_portrait(self) -> None:
        assert model_key(Mode.T2V, Tier.FAST, Aspect.PORTRAIT) == "veo_3_1_t2v_fast_portrait"

    def test_i2v_quality_landscape(self) -> None:
        assert (
            model_key(Mode.I2V, Tier.QUALITY, Aspect.LANDSCAPE) == "veo_3_1_i2v_quality_landscape"
        )

    def test_t2v_fast_square(self) -> None:
        assert model_key(Mode.T2V, Tier.FAST, Aspect.SQUARE) == "veo_3_1_t2v_fast_square"


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


class TestBuildGenerateBody:
    def test_t2v_minimal(self) -> None:
        req = GenerateVideoRequest(prompt="a cat in a hat", aspect=Aspect.PORTRAIT)
        body = build_generate_body(
            req,
            project_id="proj-1",
            recaptcha_token="TOKEN",
            batch_id="batch-1",
            seed=42,
            session_id=";1700000000000",
        )
        assert body["mediaGenerationContext"]["batchId"] == "batch-1"
        assert body["clientContext"]["projectId"] == "proj-1"
        assert body["clientContext"]["recaptchaContext"]["token"] == "TOKEN"
        assert body["requests"][0]["videoModelKey"] == "veo_3_1_t2v_fast_portrait"
        assert body["requests"][0]["aspectRatio"] == "VIDEO_ASPECT_RATIO_PORTRAIT"
        assert body["requests"][0]["seed"] == 42
        assert body["requests"][0]["textInput"]["structuredPrompt"]["parts"][0]["text"] == (
            "a cat in a hat"
        )
        assert "imageInput" not in body["requests"][0]
        assert body["useV2ModelConfig"] is True

    def test_i2v_includes_image_input(self) -> None:
        req = GenerateVideoRequest(
            prompt="push in",
            aspect=Aspect.PORTRAIT,
            start_asset_uuid="asset-uuid-123",
        )
        body = build_generate_body(
            req, project_id="proj-1", recaptcha_token="T", batch_id="b", seed=1, session_id=";1"
        )
        assert body["requests"][0]["videoModelKey"] == "veo_3_1_i2v_fast_portrait"
        assert body["requests"][0]["imageInput"]["mediaId"] == "asset-uuid-123"
