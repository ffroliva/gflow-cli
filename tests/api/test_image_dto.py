"""DTO parser tests for image-generation responses.

Validates GeneratedImage and UploadedImage against captured wire samples
under samples/captured/.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from gflow_cli.api.dto import GeneratedImage, UploadedImage

CAPTURED = Path(__file__).parent.parent.parent / "samples" / "captured"


def _load(filename: str) -> dict:
    """Load a captured sample. Files use a {request_*, response_body_parsed} envelope."""
    return json.loads((CAPTURED / filename).read_text(encoding="utf-8"))


class TestGeneratedImage:
    def test_parse_real_t2i_response_item(self) -> None:
        """1.1 Parse a single media[] item from the T2I baseline capture."""
        sample = _load("06_batchGenerateImages.json")
        media = sample["response_body_parsed"]["media"]
        gi = GeneratedImage.from_response_item(media[0])

        assert gi.media_name == "<UUID>"
        assert gi.workflow_id == "<UUID>"
        assert gi.seed == 646428
        assert gi.prompt == "a warrior zelda in a dangeon. cinematic. 8k "
        assert gi.model_name_type == "NARWHAL"
        assert gi.aspect_ratio == "IMAGE_ASPECT_RATIO_PORTRAIT"
        assert gi.fife_url.startswith("https://flow-content.google/image/")
        assert gi.dimensions == (768, 1376)

    def test_is_signed_url_true_when_signature_present(self) -> None:
        """1.2 fife URLs containing Signature= are recognised as signed."""
        sample = _load("06_batchGenerateImages.json")
        media = sample["response_body_parsed"]["media"]
        gi = GeneratedImage.from_response_item(media[0])
        assert "Signature=" in gi.fife_url
        assert gi.is_signed_url is True

    def test_is_signed_url_false_when_no_signature(self) -> None:
        gi = GeneratedImage(
            media_name="m",
            workflow_id="w",
            seed=1,
            prompt="p",
            model_name_type="NARWHAL",
            aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
            fife_url="https://example.com/plain",
            dimensions=(1, 1),
        )
        assert gi.is_signed_url is False

    def test_from_response_dict_returns_list(self) -> None:
        """1.3 Top-level parser returns a list even when there's a single entry."""
        sample = _load("06_batchGenerateImages.json")
        body = sample["response_body_parsed"]
        results = GeneratedImage.from_response_dict(body)
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0].seed == 646428

    def test_parse_seeded_landscape_response(self) -> None:
        """Seeded I2I + 4:3 landscape capture parses to expected dimensions/aspect."""
        sample = _load("07_batchGenerateImages_seeded.json")
        body = sample["response_body_parsed"]
        results = GeneratedImage.from_response_dict(body)
        assert len(results) == 1
        gi = results[0]
        assert gi.aspect_ratio == "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE"
        assert gi.dimensions == (1200, 896)
        assert gi.seed == 689072

    def test_missing_keys_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="batchGenerateImages"):
            GeneratedImage.from_response_item({"name": "x"})

    def test_from_response_dict_missing_media_raises(self) -> None:
        with pytest.raises(ValueError, match="batchGenerateImages"):
            GeneratedImage.from_response_dict({"workflows": []})

    def test_frozen(self) -> None:
        gi = GeneratedImage(
            media_name="m",
            workflow_id="w",
            seed=1,
            prompt="p",
            model_name_type="NARWHAL",
            aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
            fife_url="https://example.com/?Signature=abc",
            dimensions=(1, 1),
        )
        with pytest.raises(FrozenInstanceError):
            gi.seed = 2  # type: ignore[misc]


class TestUploadedImage:
    def test_parse_real_upload_response(self) -> None:
        """1.4 UploadedImage matches the captured uploadImage response."""
        sample = _load("01_upload_image.json")
        body = sample["response_body_parsed"]
        ui = UploadedImage.from_upload_response(body)
        assert ui.media_name == "<UUID>"
        assert ui.workflow_id == "<UUID>"
        assert ui.dimensions == (768, 1376)

    def test_missing_media_raises(self) -> None:
        with pytest.raises(ValueError, match="uploadImage"):
            UploadedImage.from_upload_response({"workflow": {}})

    def test_frozen(self) -> None:
        ui = UploadedImage(media_name="m", workflow_id="w", dimensions=(10, 20))
        with pytest.raises(FrozenInstanceError):
            ui.media_name = "other"  # type: ignore[misc]
