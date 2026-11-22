from __future__ import annotations

import json
from pathlib import Path

import pytest

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import AgentInstruction, Aspect, GenerateImageRequest, ImageRef
from gflow_cli.api.transports._mc_common import UNUSUAL_ACTIVITY_REASON
from gflow_cli.api.transports._mc_submit_observe import ogiz0b_reply_images
from gflow_cli.api.transports.direct_http import direct_can_serve
from gflow_cli.errors import WafRejectionError, WireFormatError
from tests.api.transports.test_migrated_images import MEDIA, URL, WORKFLOW, image_payload


def test_direct_can_serve_plain_t2i() -> None:
    req = GenerateImageRequest(prompt="A cinematic view of mountains")
    assert direct_can_serve(req) is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"refs": (ImageRef(name="00000000-0000-0000-0000-000000000001"),)},
        {"ref_paths": (Path("image.png"),)},
        {"reference_entities": ("entity-1",)},
        {"aspect": Aspect.LANDSCAPE},
        {"aspect": Aspect.SQUARE},
        {"count": 2},
        {"instructions": (AgentInstruction(text="keep lighting dark"),)},
        {"prompt": "A photo of @Alice in wonderland"},
    ],
)
def test_direct_can_serve_excluded_shapes(kwargs: dict[str, object]) -> None:
    base = {"prompt": "A cinematic view of mountains"}
    req = GenerateImageRequest(**{**base, **kwargs})
    assert direct_can_serve(req) is False


def test_ogiz0b_reply_images_success() -> None:
    payload = image_payload()
    frame_json = json.dumps(
        [["wrb.fr", "ogiZ0b", json.dumps(payload), None, None, None, "generic"]]
    )
    text = f")]}}'\n{frame_json}"
    req = GenerateImageRequest(prompt="a blue cup")
    images = ogiz0b_reply_images(text, req)
    assert len(images) == 1
    img = images[0]
    assert isinstance(img, GeneratedImage)
    assert img.media_name == MEDIA
    assert img.workflow_id == WORKFLOW
    assert img.prompt == "a blue cup"
    assert img.seed == 12345
    assert img.aspect_ratio == Aspect.PORTRAIT.value
    assert img.dimensions == (1376, 768)
    assert img.display_name == "Blue cup"
    assert img.fife_url == URL


def test_ogiz0b_reply_images_no_frame() -> None:
    req = GenerateImageRequest(prompt="a blue cup")
    with pytest.raises(WireFormatError, match="migrated image submit returned no ogiZ0b frame"):
        ogiz0b_reply_images(")]}'\n", req)


def test_ogiz0b_reply_images_refusal() -> None:
    req = GenerateImageRequest(prompt="a blue cup")
    err_body = json.dumps(
        [["wrb.fr", "ogiZ0b", None, None, None, [3, None, [[UNUSUAL_ACTIVITY_REASON]]], "generic"]]
    )
    text = f")]}}'\n{err_body}"
    with pytest.raises(WafRejectionError):
        ogiz0b_reply_images(text, req)
