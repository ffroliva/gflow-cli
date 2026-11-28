"""Offline BDD step definitions for direct_http_transport.feature."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api import flow_wire
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.transports import make_transport
from gflow_cli.api.transports.direct_http import DirectHttpTransport
from tests.api.transports.test_migrated_images import image_payload

if TYPE_CHECKING:
    from pathlib import Path

scenarios("direct_http_transport.feature")


def _valid_ogiz0b_text() -> str:
    payload = image_payload()
    frame_json = json.dumps(
        [["wrb.fr", "ogiZ0b", json.dumps(payload), None, None, None, "generic"]]
    )
    return f")]}}'\n{frame_json}"


@pytest.fixture(autouse=True)
def _clean_flow_wire(monkeypatch: pytest.MonkeyPatch) -> Any:
    flow_wire.reset_state()
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    yield
    flow_wire.reset_state()


@pytest.fixture
def bdd_ctx() -> dict[str, Any]:
    return {}


@given("a profile served flow.google.com")
def step_profile_served_flow(
    bdd_ctx: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint",
        AsyncMock(return_value="mock_recaptcha_token"),
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.frame_builder.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )

    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        if request.method == "POST":
            post_count += 1
            return httpx.Response(200, text=_valid_ogiz0b_text())
        return httpx.Response(404)

    mock_transport = httpx.MockTransport(handler)

    page = MagicMock()
    page.url = "https://flow.google.com/project/proj-bdd-1"
    page.goto = AsyncMock()
    page.context = MagicMock()
    page.context.cookies = AsyncMock(
        return_value=[{"name": "SID", "value": "test_sid", "domain": ".google.com", "path": "/"}]
    )

    t = make_transport("direct_http")
    assert isinstance(t, DirectHttpTransport)
    t._http_transport = mock_transport
    t.park_deferred_page = AsyncMock()  # type: ignore[method-assign]

    async def _init_transport() -> None:
        await t.setup(tmp_path, page=page)

    asyncio.run(_init_transport())

    bdd_ctx["transport"] = t
    bdd_ctx["get_post_count"] = lambda: post_count


@when("a plain text-to-image request runs on the direct_http transport")
def step_run_t2i(bdd_ctx: dict[str, Any]) -> None:
    t: DirectHttpTransport = bdd_ctx["transport"]
    req = GenerateImageRequest(prompt="a cinematic sunset")

    async def _run() -> None:
        bdd_ctx["images"] = await t.generate_images(project_id="proj-bdd-1", request=req)

    asyncio.run(_run())


@then("one direct POST is sent and the image is returned with transport direct_http")
def step_verify_result(bdd_ctx: dict[str, Any]) -> None:
    t: DirectHttpTransport = bdd_ctx["transport"]
    images = bdd_ctx["images"]
    get_post_count = bdd_ctx["get_post_count"]

    assert get_post_count() == 1
    assert len(images) == 1
    assert images[0].transport == "direct_http"
    assert t.last_image_route == "migrated"
