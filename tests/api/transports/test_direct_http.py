from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from gflow_cli.api import flow_wire
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import AgentInstruction, Aspect, GenerateImageRequest, ImageRef, Model
from gflow_cli.api.transports._mc_common import UNUSUAL_ACTIVITY_REASON
from gflow_cli.api.transports._mc_submit_observe import ogiz0b_reply_images
from gflow_cli.api.transports.direct_http import DirectHttpTransport, direct_can_serve
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.config import get_settings
from gflow_cli.errors import NetworkError, WafRejectionError, WireFormatError
from tests.api.transports.test_migrated_images import MEDIA, URL, WORKFLOW, image_payload


@pytest.fixture(autouse=True)
def _clean_flow_wire(monkeypatch: pytest.MonkeyPatch) -> Any:
    flow_wire.reset_state()
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    yield
    flow_wire.reset_state()


def test_direct_can_serve_plain_t2i() -> None:
    req = GenerateImageRequest(prompt="A cinematic view of mountains")
    assert direct_can_serve(req) is True


@pytest.mark.parametrize("aspect", list(Aspect))
def test_direct_can_serve_every_measured_aspect(aspect: Aspect) -> None:
    assert direct_can_serve(GenerateImageRequest(prompt="x", aspect=aspect)) is True


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_direct_can_serve_every_count_flow_offers(count: int) -> None:
    """One entry per image in one POST: 2, 3 and 4 entries returned 2, 3 and 4 images live."""
    assert direct_can_serve(GenerateImageRequest(prompt="x", count=count)) is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"refs": (ImageRef(name="00000000-0000-0000-0000-000000000001"),)},
        {"ref_paths": (Path("image.png"),)},
        {"reference_entities": ("entity-1",)},
        {"instructions": (AgentInstruction(text="keep lighting dark"),)},
        {"prompt": "A photo of @Alice in wonderland"},
    ],
)
def test_direct_can_serve_excluded_shapes(kwargs: dict[str, object]) -> None:
    base = {"prompt": "A cinematic view of mountains"}
    req = GenerateImageRequest(**{**base, **kwargs})
    assert direct_can_serve(req) is False


@pytest.mark.parametrize("model", [Model.HARBOR_SEAL, Model.GEM_PIX_2])
@pytest.mark.parametrize(
    "kwargs",
    [
        {"count": 2},
        {"aspect": Aspect.LANDSCAPE},
        {"refs": (ImageRef(name="r", in_project=True, display_name="r"),)},
        {"reference_entities": ("entity-1",), "reference_entity_names": ("Alice",)},
        {"reference_entity_names": ("Alice",)},
    ],
)
def test_new_models_with_an_unmeasured_slot_route_to_the_ui(
    model: Model, kwargs: dict[str, object]
) -> None:
    assert direct_can_serve(GenerateImageRequest(prompt="x", model=model, **kwargs)) is False


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


def _valid_ogiz0b_text() -> str:
    payload = image_payload()
    frame_json = json.dumps(
        [["wrb.fr", "ogiZ0b", json.dumps(payload), None, None, None, "generic"]]
    )
    return f")]}}'\n{frame_json}"


def _make_fake_page(url: str = "https://flow.google.com/project/proj-123") -> MagicMock:
    page = MagicMock()
    page.url = url
    # Like Playwright: a goto moves page.url (the served host is read after navigating).
    page.goto = AsyncMock(side_effect=lambda target, **_: setattr(page, "url", target))
    page.context = MagicMock()
    page.context.cookies = AsyncMock(
        return_value=[{"name": "SID", "value": "test_sid", "domain": ".google.com", "path": "/"}]
    )
    return page


@pytest.fixture
def mock_mint_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint",
        AsyncMock(return_value="mock_token"),
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.frame_builder.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )


@pytest.fixture
def transport(tmp_path: Path) -> DirectHttpTransport:
    t = DirectHttpTransport()
    t._setup_done = True
    t._profile_dir = tmp_path
    t._page = _make_fake_page()
    t.park_deferred_page = AsyncMock()  # type: ignore[method-assign]
    return t


@pytest.mark.asyncio
async def test_direct_http_happy_path(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
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

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    req = GenerateImageRequest(prompt="a blue cup")
    images = await transport.generate_images(project_id="proj-123", request=req)

    assert len(images) == 1
    assert images[0].transport == "direct_http"
    assert transport.last_image_route == "migrated"
    assert not ui_called
    assert post_count == 1


@pytest.mark.asyncio
async def test_direct_http_unsupported_shape_falls_back_to_ui(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)  # any request at all means selection came too late
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    # A model with no measured wire name: refused by the selector, before any HTTP work.
    req = GenerateImageRequest(prompt="a blue cup", model=Model.IMAGEN_3_5)
    await transport.generate_images(project_id="proj-123", request=req)

    assert ui_called
    assert calls == []


def test_direct_can_serve_is_the_generation_route_predicate() -> None:
    """One predicate: the transport and the router cannot disagree about a shape."""
    from gflow_cli.api import generation_route

    assert direct_can_serve is generation_route.direct_can_serve


@pytest.mark.asyncio
async def test_direct_http_agentic_ui_mode_setting_falls_back_to_ui(
    transport: DirectHttpTransport, mock_mint_and_body: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GFLOW_CLI_UI_MODE=agentic`` with no ``--ui-mode`` flag would send a classic frame."""
    from gflow_cli.config import UiMode

    monkeypatch.setattr(get_settings(), "ui_mode", UiMode.AGENTIC)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    await transport.generate_images(
        project_id="proj-123", request=GenerateImageRequest(prompt="a blue cup")
    )

    assert ui_called
    assert calls == []


@pytest.mark.asyncio
async def test_direct_http_pre_send_cookie_failure_falls_back(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    transport._page.context.cookies = AsyncMock(side_effect=RuntimeError("cookie unreadable"))
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "POST":
            post_count += 1
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)

    assert ui_called
    assert post_count == 0


@pytest.mark.asyncio
async def test_direct_http_pre_send_shell_tokens_failure_falls_back(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(401, text="Unauthorized")
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)

    assert ui_called


@pytest.mark.asyncio
async def test_direct_http_pre_send_mint_failure_falls_back(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint",
        AsyncMock(side_effect=RuntimeError("minting failed")),
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.frame_builder.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
        )

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)

    assert ui_called


@pytest.mark.asyncio
async def test_direct_http_pre_send_connect_error_falls_back(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        raise httpx.ConnectError("Connection refused")

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)

    assert ui_called


@pytest.mark.asyncio
async def test_direct_http_post_403_falls_back_to_ui(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        if request.method == "POST":
            post_count += 1
            return httpx.Response(403, text="Forbidden")
        return httpx.Response(404)

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)

    assert ui_called
    assert post_count == 1


@pytest.mark.asyncio
async def test_direct_http_post_500_raises_network_error(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        if request.method == "POST":
            post_count += 1
            return httpx.Response(500, text="Internal Server Error")
        return httpx.Response(404)

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    req = GenerateImageRequest(prompt="a blue cup")
    with pytest.raises(NetworkError) as exc_info:
        await transport.generate_images(project_id="proj-123", request=req)

    assert exc_info.value.next_action == "reconcile"
    assert "not resent" in exc_info.value.detail
    assert not ui_called
    assert post_count == 1


@pytest.mark.asyncio
async def test_direct_http_post_timeout_raises_network_error(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        if request.method == "POST":
            post_count += 1
            raise httpx.ReadTimeout("Request timed out")
        return httpx.Response(404)

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    req = GenerateImageRequest(prompt="a blue cup")
    with pytest.raises(NetworkError) as exc_info:
        await transport.generate_images(project_id="proj-123", request=req)

    assert exc_info.value.next_action == "reconcile"
    assert "not resent" in exc_info.value.detail
    assert not ui_called
    assert post_count == 1


@pytest.mark.asyncio
async def test_direct_http_setup_not_done_raises(transport: DirectHttpTransport) -> None:
    transport._setup_done = False
    req = GenerateImageRequest(prompt="a blue cup")
    with pytest.raises(RuntimeError, match="setup.*must be called"):
        await transport.generate_images(project_id="proj-123", request=req)


@pytest.mark.asyncio
async def test_direct_http_no_project_id_falls_back(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]
    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id=None, request=req)
    assert ui_called


@pytest.mark.asyncio
async def test_direct_http_no_profile_dir_falls_back(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    transport._profile_dir = None
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]
    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)
    assert ui_called


@pytest.mark.asyncio
async def test_direct_http_route_not_migrated_falls_back(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, mock_mint_and_body: None
) -> None:
    monkeypatch.setattr(get_settings(), "flow_host", "labs.google")
    transport._page.url = "https://labs.google/fx/tools/flow"
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]
    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)
    assert ui_called


@pytest.mark.asyncio
async def test_direct_http_proxy_configured_falls_back(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, mock_mint_and_body: None
) -> None:
    monkeypatch.setattr(flow_wire, "getproxies", lambda: {"https": "http://127.0.0.1:8080"})
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]
    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)
    assert ui_called


@pytest.mark.asyncio
async def test_direct_http_page_navigates_to_project_when_needed(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    transport._page.url = "https://flow.google.com/other"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)
    # The first navigation is the mint's; the park after the run is the second.
    assert (
        transport._page.goto.await_args_list[0].args[0]
        == "https://flow.google.com/project/proj-123"
    )


@pytest.mark.asyncio
async def test_direct_http_navigation_error_falls_back(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    transport._page.url = "https://flow.google.com/other"
    transport._page.goto = AsyncMock(side_effect=RuntimeError("nav failed"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]
    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)
    assert ui_called


@pytest.mark.asyncio
async def test_direct_http_post_transport_error_raises_network_error(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        if request.method == "POST":
            raise httpx.ProtocolError("Protocol violation")
        return httpx.Response(404)

    transport._http_transport = httpx.MockTransport(handler)
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]

    req = GenerateImageRequest(prompt="a blue cup")
    with pytest.raises(NetworkError) as exc_info:
        await transport.generate_images(project_id="proj-123", request=req)

    assert exc_info.value.next_action == "reconcile"
    assert "not resent" in exc_info.value.detail
    assert not ui_called


@pytest.mark.asyncio
async def test_direct_http_empty_cookies_falls_back(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    transport._page.context.cookies = AsyncMock(return_value=[])
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]
    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)
    assert ui_called


@pytest.mark.asyncio
async def test_direct_http_user_agent_included(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, mock_mint_and_body: None
) -> None:
    monkeypatch.setattr(flow_wire, "user_agent", lambda _p: "CustomUA/1.0")
    recorded_ua = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal recorded_ua
        if request.method == "GET":
            recorded_ua = request.headers.get("user-agent")
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)
    assert recorded_ua == "CustomUA/1.0"


@pytest.mark.asyncio
async def test_direct_http_shell_tokens_unexpected_error_falls_back(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, mock_mint_and_body: None
) -> None:
    monkeypatch.setattr(
        flow_wire,
        "shell_tokens",
        AsyncMock(side_effect=RuntimeError("unexpected shell error")),
    )
    ui_called = False

    async def mock_ui_locked(*args: Any, **kwargs: Any) -> list[GeneratedImage]:
        nonlocal ui_called
        ui_called = True
        return []

    transport._generate_images_locked = mock_ui_locked  # type: ignore[method-assign]
    req = GenerateImageRequest(prompt="a blue cup")
    await transport.generate_images(project_id="proj-123", request=req)
    assert ui_called


@pytest.mark.asyncio
async def test_direct_http_setup_stores_profile_dir(tmp_path: Path) -> None:
    t = DirectHttpTransport()
    fake_page = _make_fake_page()
    await t.setup(tmp_path, page=fake_page)
    assert t._profile_dir == tmp_path
    assert t._page is fake_page


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "wire"),
    [
        # NARWHAL is sent as BELUGA on flow.google.com; Flow no longer lists NARWHAL (#958).
        (Model.NARWHAL, "BELUGA"),
        # Measured live on direct ogiZ0b, 2026-11-27 (#1549).
        (Model.HARBOR_SEAL, "HARBOR_SEAL"),
        (Model.GEM_PIX_2, "GEM_PIX_2"),
    ],
)
async def test_direct_http_sends_the_migrated_wire_model(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, model: Model, wire: str
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=mock_body&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.build_ogiz0b_body", builder)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    await transport.generate_images(
        project_id="proj-123", request=GenerateImageRequest(prompt="x", model=model)
    )
    assert builder.call_args.kwargs["model"] == wire


_MEASURED_MODELS = (Model.NARWHAL, Model.HARBOR_SEAL, Model.GEM_PIX_2)


@pytest.mark.parametrize("model", _MEASURED_MODELS)
def test_a_model_with_a_measured_wire_name_is_served(model: Model) -> None:
    assert direct_can_serve(GenerateImageRequest(prompt="x", model=model))


@pytest.mark.parametrize("model", [m for m in Model if m not in _MEASURED_MODELS])
def test_a_model_with_no_measured_migrated_wire_name_is_not_served(model: Model) -> None:
    assert not direct_can_serve(GenerateImageRequest(prompt="x", model=model))


@pytest.mark.asyncio
async def test_direct_http_sends_one_seed_per_image_and_the_aspect(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=mock_body&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.build_ogiz0b_body", builder)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    req = GenerateImageRequest(prompt="x", aspect=Aspect.LANDSCAPE, count=2)
    await transport.generate_images(project_id="proj-123", request=req)
    kwargs = builder.call_args.kwargs
    assert len(kwargs["entry_seeds"]) == 2
    assert len(set(kwargs["entry_seeds"])) == 2
    assert kwargs["aspect"] is Aspect.LANDSCAPE


_IN_PROJECT = ImageRef(
    name="00000000-0000-0000-0000-000000000002", display_name="Lighthouse", in_project=True
)


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_direct_can_serve_one_in_project_reference(count: int) -> None:
    req = GenerateImageRequest(prompt="x", refs=(_IN_PROJECT,), count=count)
    assert direct_can_serve(req) is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"refs": (_IN_PROJECT, replace(_IN_PROJECT, name="00000000-0000-0000-0000-000000000003"))},
        {"refs": (replace(_IN_PROJECT, display_name=""),)},
        {"refs": (replace(_IN_PROJECT, in_project=False),)},
    ],
)
def test_direct_can_serve_refuses_unmeasured_reference_shapes(kwargs: dict[str, object]) -> None:
    assert direct_can_serve(GenerateImageRequest(prompt="x", **kwargs)) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_mint_on_a_page_still_loading_recaptcha_is_retried_not_dropped(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live 2026-11-23: an i2i with --project navigates to the project and mints at once;
    grecaptcha.enterprise is not loaded yet, and the run fell back to the UI."""
    from gflow_cli.errors import RecaptchaError

    monkeypatch.setattr("gflow_cli.api.transports.direct_http._mint._MINT_RETRY_S", 0.0)
    mint = AsyncMock(side_effect=[RecaptchaError("grecaptcha.enterprise not loaded"), "tok"])
    monkeypatch.setattr("gflow_cli.api.recaptcha.TokenMinter.mint", mint)
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    transport._page.url = "about:blank"  # type: ignore[union-attr]
    images = await transport.generate_images(
        project_id="proj-123", request=GenerateImageRequest(prompt="x")
    )
    assert images[0].transport == "direct_http"
    assert mint.await_count == 2


_SKIPPER = {"reference_entities": ("entity-1",), "reference_entity_names": ("Skipper",)}


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_direct_can_serve_one_named_character(count: int) -> None:
    assert (
        direct_can_serve(GenerateImageRequest(prompt="x", count=count, **_SKIPPER))  # type: ignore[arg-type]
        is True
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reference_entities": ("entity-1",)},  # an id with no name: the chip needs both
        {"reference_entities": ("entity-1",), "reference_entity_names": ("",)},
        {
            "reference_entities": ("entity-1", "entity-2"),
            "reference_entity_names": ("Skipper", "Mate"),
        },
        {**_SKIPPER, "refs": (_IN_PROJECT,)},
    ],
)
def test_direct_can_serve_refuses_unmeasured_character_shapes(kwargs: dict[str, object]) -> None:
    assert direct_can_serve(GenerateImageRequest(prompt="x", **kwargs)) is False  # type: ignore[arg-type]


def _valid_ogiz0b_two_images_text() -> str:
    payload = image_payload()
    second_media = json.loads(
        json.dumps(payload[0][0][0]).replace(MEDIA, "55555555-5555-4555-8555-555555555555")
    )
    payload[0][0].append(second_media)
    frame_json = json.dumps(
        [["wrb.fr", "ogiZ0b", json.dumps(payload), None, None, None, "generic"]]
    )
    return f")]}}'\n{frame_json}"


@pytest.mark.asyncio
async def test_direct_http_sends_reference_with_multiple_seeds(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=mock_body&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.build_ogiz0b_body", builder)
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        post_count += 1
        return httpx.Response(200, text=_valid_ogiz0b_two_images_text())

    transport._http_transport = httpx.MockTransport(handler)
    req = GenerateImageRequest(prompt="a sailboat", refs=(_IN_PROJECT,), count=2)
    images = await transport.generate_images(project_id="proj-123", request=req)
    assert post_count == 1
    assert len(images) == 2
    assert all(img.transport == "direct_http" for img in images)
    kwargs = builder.call_args.kwargs
    assert len(kwargs["entry_seeds"]) == 2
    assert kwargs["references"] == ((_IN_PROJECT.name, _IN_PROJECT.display_name),)
    assert kwargs["characters"] == ()


@pytest.mark.asyncio
async def test_direct_http_sends_character_with_multiple_seeds(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=mock_body&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.build_ogiz0b_body", builder)
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.read_project",
        AsyncMock(return_value=_project_with("Mate", "Skipper")),
    )
    post_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        post_count += 1
        return httpx.Response(200, text=_valid_ogiz0b_two_images_text())

    transport._http_transport = httpx.MockTransport(handler)
    req = GenerateImageRequest(prompt="standing on pier", count=2, **_SKIPPER)  # type: ignore[arg-type]
    images = await transport.generate_images(project_id="proj-123", request=req)
    assert post_count == 1
    assert len(images) == 2
    assert all(img.transport == "direct_http" for img in images)
    kwargs = builder.call_args.kwargs
    assert len(kwargs["entry_seeds"]) == 2
    assert kwargs["characters"] == (("entity-1", "Skipper"),)
    assert kwargs["references"] == ()


@pytest.mark.asyncio
async def test_direct_http_sends_the_character_pair(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=mock_body&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.build_ogiz0b_body", builder)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.read_project",
        AsyncMock(return_value=_project_with("Mate", "Skipper")),
    )
    req = GenerateImageRequest(prompt="x", **_SKIPPER)  # type: ignore[arg-type]
    await transport.generate_images(project_id="proj-123", request=req)
    assert builder.call_args.kwargs["characters"] == (("entity-1", "Skipper"),)


# --- Review fix round (code-review xhigh + ponytail-review xhigh, 2026-11-23) ----------


def _shell_and(post: httpx.Response | Exception) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        if isinstance(post, Exception):
            raise post
        return post

    return httpx.MockTransport(handler)


def _watch_ui(transport: DirectHttpTransport) -> list[bool]:
    calls: list[bool] = []

    async def ui(*_: Any, **__: Any) -> list[GeneratedImage]:
        calls.append(True)
        return []

    transport._generate_images_locked = ui  # type: ignore[method-assign]
    return calls


def test_agentic_ui_mode_is_not_served() -> None:
    """PR-4: --ui-mode agentic would have been sent as a classic frame."""
    from gflow_cli.config import UiMode

    assert direct_can_serve(GenerateImageRequest(prompt="x", ui_mode=UiMode.AGENTIC)) is False


def test_a_catalogued_reference_keeps_the_ui_path() -> None:
    """A catalogued ref (it carries local_path) may be stale: the UI proves it still exists
    before submit and refuses at $0; a direct POST cannot (fresh review, 2026-11-23)."""
    ref = replace(_IN_PROJECT, local_path="face.png", local_sha256="0" * 64)
    assert direct_can_serve(GenerateImageRequest(prompt="x", refs=(ref,))) is False


@pytest.mark.asyncio
async def test_a_rejected_post_drops_the_cached_shell_tokens(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    """CR-B: a stale at stuck in a long-lived process failed every later direct job."""
    transport._http_transport = _shell_and(httpx.Response(500))
    _watch_ui(transport)
    with pytest.raises(NetworkError):
        await transport.generate_images(
            project_id="proj-123", request=GenerateImageRequest(prompt="x")
        )
    assert transport._profile_dir not in flow_wire._tokens


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ConnectTimeout("t"), httpx.PoolTimeout("t")])
async def test_a_connection_that_never_opened_falls_back(
    transport: DirectHttpTransport, mock_mint_and_body: None, exc: Exception
) -> None:
    """CR-D: ConnectTimeout is not a ConnectError; nothing was sent, so no reconcile."""
    transport._http_transport = _shell_and(exc)
    ui = _watch_ui(transport)
    await transport.generate_images(project_id="proj-123", request=GenerateImageRequest(prompt="x"))
    assert ui == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401])
async def test_a_redirect_or_401_falls_back_instead_of_reconcile(
    transport: DirectHttpTransport, mock_mint_and_body: None, status: int
) -> None:
    """CR-J: Flow accepted nothing; telling the user to check for an image was wrong."""
    transport._http_transport = _shell_and(httpx.Response(status))
    ui = _watch_ui(transport)
    await transport.generate_images(project_id="proj-123", request=GenerateImageRequest(prompt="x"))
    assert ui == [True]


@pytest.mark.asyncio
async def test_the_page_is_parked_after_a_direct_run(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    """CR-E / PR-3: a mounted composer must not leak into the next request."""
    transport._http_transport = _shell_and(httpx.Response(200, text=_valid_ogiz0b_text()))
    park = AsyncMock()
    transport._park_composer_page = park  # type: ignore[method-assign]
    await transport.generate_images(project_id="proj-123", request=GenerateImageRequest(prompt="x"))
    park.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_proxy_alone_does_not_disable_the_direct_path(
    transport: DirectHttpTransport, mock_mint_and_body: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CR-F: getproxies() returns a 'no' key when only NO_PROXY is set."""
    monkeypatch.setattr(flow_wire, "getproxies", lambda: {"no": "localhost"})
    transport._http_transport = _shell_and(httpx.Response(200, text=_valid_ogiz0b_text()))
    ui = _watch_ui(transport)
    images = await transport.generate_images(
        project_id="proj-123", request=GenerateImageRequest(prompt="x")
    )
    assert ui == []
    assert images[0].transport == "direct_http"


@pytest.mark.asyncio
async def test_an_account_served_labs_falls_back_before_any_post(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    """CR-G: under flow_host=auto the old route check passed every URL."""
    page = transport._page
    assert page is not None
    page.url = "about:blank"
    page.goto = AsyncMock(
        side_effect=lambda *_a, **_k: setattr(page, "url", "https://labs.google/fx/tools/flow")
    )
    posts: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(True)
        return httpx.Response(200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid"</html>')

    transport._http_transport = httpx.MockTransport(handler)
    ui = _watch_ui(transport)
    await transport.generate_images(project_id="proj-123", request=GenerateImageRequest(prompt="x"))
    assert ui == [True]
    assert posts == []


@pytest.mark.asyncio
async def test_a_mint_that_never_settles_is_bounded(
    transport: DirectHttpTransport, mock_mint_and_body: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CR-K: one evaluate whose promise never settles hung while holding the lock."""
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._mint._MINT_BUDGET_S", 0.2)

    async def never(*_: Any, **__: Any) -> str:
        await asyncio.sleep(3600)
        return ""

    monkeypatch.setattr("gflow_cli.api.recaptcha.TokenMinter.mint", never)
    transport._http_transport = _shell_and(httpx.Response(200, text=_valid_ogiz0b_text()))
    ui = _watch_ui(transport)
    await asyncio.wait_for(
        transport.generate_images(project_id="proj-123", request=GenerateImageRequest(prompt="x")),
        timeout=5,
    )
    assert ui == [True]


@pytest.mark.asyncio
async def test_cookies_are_matched_by_domain_not_by_a_url_filter(
    transport: DirectHttpTransport, mock_mint_and_body: None
) -> None:
    """CR-L: a URL filter also drops path-scoped cookies (#222)."""
    page = transport._page
    assert page is not None
    page.context.cookies = AsyncMock(
        return_value=[
            {"name": "SID", "value": "a", "domain": ".google.com", "path": "/"},
            {"name": "XS", "value": "b", "domain": "flow.google.com", "path": "/_/"},
            {"name": "OTHER", "value": "c", "domain": "example.com", "path": "/"},
            # Suffix collisions a plain endswith() would accept (RFC 6265 5.1.3 refuses them).
            {"name": "SUFFIX", "value": "d", "domain": ".le.com", "path": "/"},
            {"name": "HOSTONLY", "value": "e", "domain": "le.com", "path": "/"},
            {"name": "EMPTY", "value": "f", "domain": "", "path": "/"},
        ]
    )
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("cookie", ""))
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    await transport.generate_images(project_id="proj-123", request=GenerateImageRequest(prompt="x"))
    assert sent[-1] == "SID=a; XS=b"
    page.context.cookies.assert_awaited_once_with()


# --- A bare @Name mention (T2.2): the name is resolved to the project's character id -----


def _project_with(*names: str) -> Any:
    from gflow_cli.api.character import Character
    from gflow_cli.api.transports.migrated_character import ProjectRead

    chars = tuple(
        Character(
            entity_id=f"entity-{i}",
            display_name=name,
            project_id="proj-123",
            workflow_ids=(),
            voice=None,
            personality=None,
            thumbnail_media_id=None,
        )
        for i, name in enumerate(names)
    )
    return ProjectRead(characters=chars, voices=())


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_a_bare_mention_name_is_served(count: int) -> None:
    req = GenerateImageRequest(
        prompt="Skipper on a pier", reference_entity_names=("Skipper",), count=count
    )
    assert direct_can_serve(req) is True


@pytest.mark.asyncio
async def test_a_bare_mention_is_resolved_to_the_project_character_id(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=mock_body&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.build_ogiz0b_body", builder)
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.read_project",
        AsyncMock(return_value=_project_with("Mate", "Skipper")),
    )
    transport._http_transport = _shell_and(httpx.Response(200, text=_valid_ogiz0b_text()))
    req = GenerateImageRequest(prompt="Skipper on a pier", reference_entity_names=("Skipper",))
    images = await transport.generate_images(project_id="proj-123", request=req)
    assert builder.call_args.kwargs["characters"] == (("entity-1", "Skipper"),)
    assert images[0].transport == "direct_http"


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [(), ("Skipper", "Skipper")])
async def test_a_mention_with_no_or_several_matches_falls_back_before_any_post(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.read_project",
        AsyncMock(return_value=_project_with(*names)),
    )
    posts: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(True)
        return httpx.Response(
            200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
        )

    transport._http_transport = httpx.MockTransport(handler)
    ui = _watch_ui(transport)
    req = GenerateImageRequest(prompt="Skipper on a pier", reference_entity_names=("Skipper",))
    await transport.generate_images(project_id="proj-123", request=req)
    assert ui == [True]
    assert posts == []


@pytest.mark.asyncio
async def test_a_mention_matches_the_picker_rule_trimmed_and_case_insensitive(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh review: @kael must find "Kael " as the UI picker does (_mc_mention.py)."""
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=mock_body&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.build_ogiz0b_body", builder)
    read = AsyncMock(return_value=_project_with("Mate", "Kael "))
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.read_project", read)
    transport._http_transport = _shell_and(httpx.Response(200, text=_valid_ogiz0b_text()))
    req = GenerateImageRequest(prompt="kael on a pier", reference_entity_names=("kael",))
    await transport.generate_images(project_id="proj-123", request=req)
    assert builder.call_args.kwargs["characters"] == (("entity-1", "Kael "),)
    assert read.await_args.kwargs["timeout_s"] == 15.0


@pytest.mark.asyncio
async def test_names_differing_only_in_case_are_ambiguous(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh review: "Kael" and "KAEL" are one name to the picker, which refuses; so do we."""
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.read_project",
        AsyncMock(return_value=_project_with("Kael", "KAEL")),
    )
    transport._http_transport = _shell_and(httpx.Response(200, text=_valid_ogiz0b_text()))
    ui = _watch_ui(transport)
    req = GenerateImageRequest(prompt="Kael on a pier", reference_entity_names=("Kael",))
    await transport.generate_images(project_id="proj-123", request=req)
    assert ui == [True]


@pytest.mark.asyncio
async def test_an_entity_id_the_project_does_not_hold_falls_back_before_any_post(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--reference-entity is verified against the project, as the UI attach does: a stale
    or foreign id must not reach Flow on the direct path."""
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.read_project",
        AsyncMock(return_value=_project_with("Mate")),  # entity-0 only; entity-1 is gone
    )
    posts: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(True)
        return httpx.Response(
            200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
        )

    transport._http_transport = httpx.MockTransport(handler)
    ui = _watch_ui(transport)
    req = GenerateImageRequest(prompt="x", **_SKIPPER)  # type: ignore[arg-type]
    await transport.generate_images(project_id="proj-123", request=req)
    assert ui == [True]
    assert posts == []


@pytest.mark.asyncio
async def test_an_entity_id_uses_the_name_flow_holds_for_it(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chip carries the character's name as Flow knows it, not a caller's spelling."""
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=mock_body&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.build_ogiz0b_body", builder)
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.read_project",
        AsyncMock(return_value=_project_with("Mate", "Skipper")),
    )
    transport._http_transport = _shell_and(httpx.Response(200, text=_valid_ogiz0b_text()))
    req = GenerateImageRequest(
        prompt="x", reference_entities=("entity-1",), reference_entity_names=("skipper",)
    )
    await transport.generate_images(project_id="proj-123", request=req)
    assert builder.call_args.kwargs["characters"] == (("entity-1", "Skipper"),)


# --- Phase 4: direct maseQ upload ----------------------------------------------------------

_UPLOADED = "f9efcc22-495d-4c89-9b9b-f9dce015742b"


def _maseq_reply(media_id: str = _UPLOADED) -> str:
    frame = json.dumps(
        [["wrb.fr", "maseQ", json.dumps([[media_id, "proj-123"]]), None, None, None, "generic"]]
    )
    return f")]}}'\n{frame}"


def _png(tmp_path: Path) -> Path:
    f = tmp_path / "face.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return f


@pytest.mark.asyncio
async def test_a_local_file_is_uploaded_with_one_direct_maseq_post(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mint = AsyncMock(return_value="mock_token")
    monkeypatch.setattr("gflow_cli.api.recaptcha.TokenMinter.mint", mint)
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        posts.append(request.url.params["rpcids"])
        return httpx.Response(200, text=_maseq_reply())

    transport._http_transport = httpx.MockTransport(handler)
    ui = AsyncMock()
    monkeypatch.setattr(UiAutomationTransport, "upload_reference", ui)
    ref = await transport.upload_reference(project_id="proj-123", path=_png(tmp_path))
    assert ref is not None
    assert ref.name == _UPLOADED
    assert ref.in_project is True
    assert ref.display_name.startswith("face-") and ref.display_name.endswith(".png")
    assert posts == ["maseQ"]
    assert mint.await_args.args[0] == "UPLOAD_IMAGE"
    ui.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_upload_that_cannot_start_uses_the_ui_upload(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = transport._page
    assert page is not None
    page.context.cookies = AsyncMock(return_value=[])  # no session: nothing can be sent
    ui = AsyncMock(return_value=None)
    monkeypatch.setattr(UiAutomationTransport, "upload_reference", ui)
    await transport.upload_reference(project_id="proj-123", path=_png(tmp_path))
    ui.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_upload_failing_after_the_send_is_not_resent(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    transport._http_transport = _shell_and(httpx.Response(500))
    ui = AsyncMock()
    monkeypatch.setattr(UiAutomationTransport, "upload_reference", ui)
    with pytest.raises(NetworkError) as caught:
        await transport.upload_reference(project_id="proj-123", path=_png(tmp_path))
    assert caught.value.next_action == "reconcile"
    assert "upload" in (caught.value.remediation_hint or "")
    ui.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_upload_reply_without_a_media_id_is_a_wire_format_error(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    transport._http_transport = _shell_and(httpx.Response(200, text=")]}'\n[]"))
    with pytest.raises(WireFormatError):
        await transport.upload_reference(project_id="proj-123", path=_png(tmp_path))


@pytest.mark.asyncio
async def test_the_character_read_has_one_outer_deadline(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CodeRabbit: read_project's per-attempt budget plus its retry could stack past 15 s."""
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images._CHARACTER_READ_S", 0.2)

    async def slow(*_: Any, **__: Any) -> Any:
        await asyncio.sleep(3600)

    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.read_project", slow)
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    transport._http_transport = _shell_and(httpx.Response(200, text=_valid_ogiz0b_text()))
    ui = _watch_ui(transport)
    req = GenerateImageRequest(prompt="x", reference_entity_names=("Skipper",))
    await asyncio.wait_for(transport.generate_images(project_id="proj-123", request=req), timeout=5)
    assert ui == [True]


@pytest.mark.asyncio
async def test_the_upload_mime_type_comes_from_the_bytes_not_the_name(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CodeRabbit: an extensionless JPEG was sent as image/png (name-based guess)."""
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=x&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.build_maseq_body", builder)
    transport._http_transport = _shell_and(httpx.Response(200, text=_maseq_reply()))
    portrait = tmp_path / "portrait"  # no extension
    portrait.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    await transport.upload_reference(project_id="proj-123", path=portrait)
    assert builder.call_args.kwargs["mime"] == "image/jpeg"
