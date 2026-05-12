"""Tests for `gflow_cli.api.image` — pure value objects + body builder.

Tests load the captured samples in `samples/captured/` and assert structural
equality (modulo the four variable fields: recaptcha token, projectId,
batchId, sessionId).
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.image import (
    Aspect,
    GenerateImageRequest,
    ImageRef,
    Model,
    _build_batch_generate_images_body,
)

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples" / "captured"

# Variable fields that are swapped to sentinels before comparison.
_PROJECT_SENTINEL = "<PROJECT_SENTINEL>"
_BATCH_SENTINEL = "<BATCH_SENTINEL>"
_SESSION_SENTINEL = "<SESSION_SENTINEL>"
_TOKEN_SENTINEL = "<TOKEN_SENTINEL>"


def _normalize(body: dict[str, Any]) -> dict[str, Any]:
    """Replace the four variable fields with sentinels everywhere they occur."""
    out = deepcopy(body)

    def _walk_client_context(cc: dict[str, Any]) -> None:
        if "projectId" in cc:
            cc["projectId"] = _PROJECT_SENTINEL
        if "sessionId" in cc:
            cc["sessionId"] = _SESSION_SENTINEL
        rc = cc.get("recaptchaContext")
        if isinstance(rc, dict) and "token" in rc:
            rc["token"] = _TOKEN_SENTINEL

    cc_root = out.get("clientContext")
    if isinstance(cc_root, dict):
        _walk_client_context(cc_root)
    mgc = out.get("mediaGenerationContext")
    if isinstance(mgc, dict) and "batchId" in mgc:
        mgc["batchId"] = _BATCH_SENTINEL
    for req in out.get("requests", []):
        cc_req = req.get("clientContext")
        if isinstance(cc_req, dict):
            _walk_client_context(cc_req)
    return out


def _load_sample(name: str) -> dict[str, Any]:
    return json.loads((SAMPLES_DIR / name).read_text(encoding="utf-8"))


class TestImageAspect:
    def test_portrait(self) -> None:
        assert Aspect.from_cli("9:16") is Aspect.PORTRAIT
        assert Aspect.PORTRAIT.value == "IMAGE_ASPECT_RATIO_PORTRAIT"

    def test_landscape(self) -> None:
        assert Aspect.from_cli("16:9") is Aspect.LANDSCAPE
        assert Aspect.LANDSCAPE.value == "IMAGE_ASPECT_RATIO_LANDSCAPE"

    def test_square(self) -> None:
        assert Aspect.from_cli("1:1") is Aspect.SQUARE
        assert Aspect.SQUARE.value == "IMAGE_ASPECT_RATIO_SQUARE"

    def test_landscape_four_three(self) -> None:
        assert Aspect.from_cli("4:3") is Aspect.LANDSCAPE_FOUR_THREE
        assert Aspect.LANDSCAPE_FOUR_THREE.value == "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE"

    def test_portrait_three_four(self) -> None:
        assert Aspect.from_cli("3:4") is Aspect.PORTRAIT_THREE_FOUR
        assert Aspect.PORTRAIT_THREE_FOUR.value == "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR"

    def test_default_when_none(self) -> None:
        assert Aspect.from_cli(None) is Aspect.PORTRAIT

    def test_garbage_raises(self) -> None:
        with pytest.raises(ValueError):
            Aspect.from_cli("garbage")


class TestImageModel:
    def test_nano2_alias(self) -> None:
        assert Model.from_cli("nano2") is Model.NARWHAL

    def test_nano_banana_2_alias(self) -> None:
        assert Model.from_cli("nano-banana-2") is Model.NARWHAL

    def test_nano_pro_alias(self) -> None:
        assert Model.from_cli("nano-pro") is Model.GEM_PIX_2

    def test_image4_alias(self) -> None:
        assert Model.from_cli("image4") is Model.IMAGEN_3_5

    def test_default_when_none(self) -> None:
        assert Model.from_cli(None) is Model.NARWHAL

    def test_wire_value(self) -> None:
        assert Model.NARWHAL.value == "NARWHAL"
        assert Model.GEM_PIX_2.value == "GEM_PIX_2"
        assert Model.IMAGEN_3_5.value == "IMAGEN_3_5"

    def test_unknown_alias_raises(self) -> None:
        with pytest.raises(ValueError):
            Model.from_cli("totally-unknown")


class TestImageRef:
    def test_to_wire(self) -> None:
        assert ImageRef("uuid-here").to_wire() == {
            "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE",
            "name": "uuid-here",
        }

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            ImageRef("")

    def test_whitespace_raises(self) -> None:
        with pytest.raises(ValueError):
            ImageRef("   ")

    def test_whitespace_padded_raises(self) -> None:
        # Padded values would emit garbage on the wire — reject explicitly.
        with pytest.raises(ValueError):
            ImageRef("  real-uuid  ")
        with pytest.raises(ValueError):
            ImageRef("real-uuid ")
        with pytest.raises(ValueError):
            ImageRef(" real-uuid")


class TestGenerateImageRequest:
    def test_empty_prompt_raises(self) -> None:
        with pytest.raises(ValueError):
            GenerateImageRequest(prompt="", aspect=Aspect.PORTRAIT, model=Model.NARWHAL)

    def test_whitespace_only_prompt_raises(self) -> None:
        with pytest.raises(ValueError):
            GenerateImageRequest(prompt="   ", aspect=Aspect.PORTRAIT, model=Model.NARWHAL)

    def test_carries_recaptcha_token(self) -> None:
        req = GenerateImageRequest(
            prompt="test",
            model=Model.NARWHAL,
            aspect=Aspect.PORTRAIT,
            recaptcha_token="abc123_recaptcha",
        )
        assert req.recaptcha_token == "abc123_recaptcha"

    def test_recaptcha_token_defaults_to_empty(self) -> None:
        req = GenerateImageRequest(prompt="test", model=Model.NARWHAL, aspect=Aspect.PORTRAIT)
        assert req.recaptcha_token == ""


class TestBuildBatchGenerateImagesBody:
    def test_matches_sample_06_t2i(self) -> None:
        sample = _load_sample("06_batchGenerateImages.json")
        sample_body = sample["request_body_parsed"]
        prompt = sample_body["requests"][0]["structuredPrompt"]["parts"][0]["text"]
        seed = sample_body["requests"][0]["seed"]

        req = GenerateImageRequest(
            prompt=prompt,
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
            refs=(),
        )
        built = _build_batch_generate_images_body(
            dc_replace(req, recaptcha_token="any-token"),
            project_id="any-project",
            batch_id="any-batch",
            seed=seed,
            session_id="any-session",
        )
        assert _normalize(built) == _normalize(sample_body)

    def test_matches_sample_07_i2i_4_3(self) -> None:
        sample = _load_sample("07_batchGenerateImages_seeded.json")
        sample_body = sample["request_body_parsed"]
        prompt = sample_body["requests"][0]["structuredPrompt"]["parts"][0]["text"]
        seed = sample_body["requests"][0]["seed"]
        ref_uuid = sample_body["requests"][0]["imageInputs"][0]["name"]

        req = GenerateImageRequest(
            prompt=prompt,
            aspect=Aspect.LANDSCAPE_FOUR_THREE,
            model=Model.NARWHAL,
            refs=(ImageRef(ref_uuid),),
        )
        built = _build_batch_generate_images_body(
            dc_replace(req, recaptcha_token="any-token"),
            project_id="any-project",
            batch_id="any-batch",
            seed=seed,
            session_id="any-session",
        )
        assert _normalize(built) == _normalize(sample_body)

    def test_clientcontext_duplicated_at_root_and_per_request(self) -> None:
        req = GenerateImageRequest(
            prompt="hello",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
        )
        body = _build_batch_generate_images_body(
            dc_replace(req, recaptcha_token="tok-1"),
            project_id="proj-1",
            batch_id="batch-1",
            seed=42,
            session_id=";1234567890",
        )
        root_cc = body["clientContext"]
        req_cc = body["requests"][0]["clientContext"]
        assert root_cc["projectId"] == "proj-1" == req_cc["projectId"]
        assert root_cc["tool"] == "PINHOLE" == req_cc["tool"]
        assert root_cc["sessionId"] == ";1234567890" == req_cc["sessionId"]
        # Same recaptcha token in both places — confirmed by samples 06/07.
        assert root_cc["recaptchaContext"]["token"] == "tok-1"
        assert req_cc["recaptchaContext"]["token"] == "tok-1"
        assert root_cc["recaptchaContext"]["token"] == req_cc["recaptchaContext"]["token"]

    def test_use_new_media_flag_set_true(self) -> None:
        req = GenerateImageRequest(prompt="hello", aspect=Aspect.PORTRAIT, model=Model.NARWHAL)
        body = _build_batch_generate_images_body(
            dc_replace(req, recaptcha_token="t"),
            project_id="p",
            batch_id="b",
            seed=1,
            session_id="s",
        )
        assert body["useNewMedia"] is True

    def test_image_inputs_empty_for_t2i(self) -> None:
        req = GenerateImageRequest(
            prompt="hello",
            aspect=Aspect.PORTRAIT,
            model=Model.NARWHAL,
            refs=(),
        )
        body = _build_batch_generate_images_body(
            dc_replace(req, recaptcha_token="t"),
            project_id="p",
            batch_id="b",
            seed=1,
            session_id="s",
        )
        # Must be the empty list — NOT missing, NOT null.
        assert "imageInputs" in body["requests"][0]
        assert body["requests"][0]["imageInputs"] == []
