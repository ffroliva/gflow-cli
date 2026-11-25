# SPDX-License-Identifier: MIT
"""The official ``openai`` SDK against ``POST /v1/images/generations`` (#1365).

``test_openai_images_api.py`` pins the wire shape by hand; these tests pin that the shape is
what the SDK actually parses and how its retry logic reacts to it. The SDK talks to the
ASGI app in process (Starlette's ``TestClient`` is an ``httpx.Client``), and the generate
tool is faked, so nothing reaches Flow.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import openai
import pytest
from starlette.testclient import TestClient

from gflow_cli.mcp.server import build_app
from tests.mcp.openai_fakes import FakeTool

BASE_URL = "http://127.0.0.1:8000/v1"
#: Not a credential — a fixed literal for the auth tests.
TOKEN = "test-daemon-token"
SetTool = Callable[[dict[str, Any]], FakeTool]


def _sdk(
    *, token: str | None = None, api_key: str = "any", **kwargs: Any
) -> Iterator[openai.OpenAI]:
    app = build_app(transport="http", host="127.0.0.1", token=token)
    with TestClient(app, base_url="http://127.0.0.1:8000") as http_client:
        yield openai.OpenAI(base_url=BASE_URL, api_key=api_key, http_client=http_client, **kwargs)


@pytest.fixture
def sdk() -> Iterator[openai.OpenAI]:
    yield from _sdk()


def test_sdk_parses_images_response_and_b64_json(
    sdk: openai.OpenAI, set_image_tool: SetTool, png_files: list[str]
) -> None:
    set_image_tool({"status": "completed", "files": png_files[:1]})

    image = sdk.images.generate(prompt="a paper boat", size="1792x1024")

    assert isinstance(image, openai.types.ImagesResponse)
    assert image.created > 0
    assert image.data is not None
    assert base64.b64decode(image.data[0].b64_json or "") == Path(png_files[0]).read_bytes()


def test_n2_returns_two_images(
    sdk: openai.OpenAI, set_image_tool: SetTool, png_files: list[str]
) -> None:
    fake = set_image_tool({"status": "completed", "files": png_files})

    image = sdk.images.generate(prompt="x", n=2)

    assert image.data is not None
    assert len(image.data) == 2
    assert fake.calls[0]["count"] == 2


def test_bad_request_raises_bad_request_error_with_param(
    sdk: openai.OpenAI, set_image_tool: SetTool
) -> None:
    fake = set_image_tool({"status": "completed", "files": []})

    with pytest.raises(openai.BadRequestError) as excinfo:
        sdk.images.generate(prompt="x", model="gpt-image-1")

    assert excinfo.value.param == "model"
    assert excinfo.value.type == "invalid_request_error"
    assert fake.calls == []


def test_wrong_token_raises_authentication_error(set_image_tool: SetTool) -> None:
    fake = set_image_tool({"status": "completed", "files": []})

    for sdk in _sdk(token=TOKEN, api_key="wrong"):
        with pytest.raises(openai.AuthenticationError) as excinfo:
            sdk.images.generate(prompt="x")
        assert excinfo.value.code == "invalid_api_key"
    assert fake.calls == []


def test_429_raises_rate_limit_error(set_image_tool: SetTool) -> None:
    problem = {"type": "https://gflow-cli.dev/errors/rate-limit", "detail": "slow down"}
    set_image_tool({"status": "rate_limited", "error": problem})

    # max_retries=0: the 429 carries retry-after, which the SDK would otherwise honour.
    for sdk in _sdk(max_retries=0):
        with pytest.raises(openai.RateLimitError) as excinfo:
            sdk.images.generate(prompt="x")
        assert excinfo.value.code == "rate_limit_exceeded"


def test_5xx_is_not_retried_by_the_sdk(sdk: openai.OpenAI, set_image_tool: SetTool) -> None:
    problem = {
        "type": "https://gflow-cli.dev/errors/network",
        "status": 502,
        "detail": "reset after submit",
        "charged": "maybe",
        "retryable": True,
    }
    fake = set_image_tool({"status": "error", "error": problem})

    # The SDK's default is two retries on 5xx; each would be another billed generation.
    with pytest.raises(openai.InternalServerError):
        sdk.images.generate(prompt="x")

    assert len(fake.calls) == 1
