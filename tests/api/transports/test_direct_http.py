from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.image import AgentInstruction, Aspect, GenerateImageRequest, ImageRef
from gflow_cli.api.transports.direct_http import direct_can_serve


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
