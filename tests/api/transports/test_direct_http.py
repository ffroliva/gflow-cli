from __future__ import annotations

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


def test_direct_can_serve_two_images() -> None:
    assert direct_can_serve(GenerateImageRequest(prompt="x", count=2)) is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"refs": (ImageRef(name="00000000-0000-0000-0000-000000000001"),)},
        {"ref_paths": (Path("image.png"),)},
        {"reference_entities": ("entity-1",)},
        {"count": 3},  # only count <= 2 is measured on the direct path
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


def _valid_ogiz0b_text() -> str:
    payload = image_payload()
    frame_json = json.dumps(
        [["wrb.fr", "ogiZ0b", json.dumps(payload), None, None, None, "generic"]]
    )
    return f")]}}'\n{frame_json}"


def _make_fake_page(url: str = "https://flow.google.com/project/proj-123") -> MagicMock:
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.context = MagicMock()
    page.context.cookies = AsyncMock(return_value=[{"name": "SID", "value": "test_sid"}])
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
        "gflow_cli.api.transports.direct_http.build_ogiz0b_body",
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

    req = GenerateImageRequest(prompt="a blue cup", count=2)
    await transport.generate_images(project_id="proj-123", request=req)

    assert ui_called
    assert post_count == 0


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
        "gflow_cli.api.transports.direct_http.build_ogiz0b_body",
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
    monkeypatch.setattr(get_settings(), "flow_host", "labs")
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
    transport._page.goto.assert_awaited_once_with("https://flow.google.com/project/proj-123")


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
async def test_direct_http_sends_the_migrated_wire_model(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NARWHAL is sent as BELUGA on flow.google.com; Flow no longer lists NARWHAL (#958)."""
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=mock_body&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http.build_ogiz0b_body", builder)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    await transport.generate_images(project_id="proj-123", request=GenerateImageRequest(prompt="x"))
    assert builder.call_args.kwargs["model"] == "BELUGA"


@pytest.mark.parametrize("model", [m for m in Model if m is not Model.NARWHAL])
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
    monkeypatch.setattr("gflow_cli.api.transports.direct_http.build_ogiz0b_body", builder)

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


def test_direct_can_serve_one_in_project_reference() -> None:
    assert direct_can_serve(GenerateImageRequest(prompt="x", refs=(_IN_PROJECT,))) is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"refs": (_IN_PROJECT, replace(_IN_PROJECT, name="00000000-0000-0000-0000-000000000003"))},
        {"refs": (replace(_IN_PROJECT, display_name=""),)},
        {"refs": (replace(_IN_PROJECT, in_project=False),)},
        {"refs": (_IN_PROJECT,), "count": 2},
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

    monkeypatch.setattr("gflow_cli.api.transports.direct_http._MINT_RETRY_S", 0.0)
    mint = AsyncMock(side_effect=[RecaptchaError("grecaptcha.enterprise not loaded"), "tok"])
    monkeypatch.setattr("gflow_cli.api.recaptcha.TokenMinter.mint", mint)
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http.build_ogiz0b_body",
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


def test_direct_can_serve_one_named_character() -> None:
    assert direct_can_serve(GenerateImageRequest(prompt="x", **_SKIPPER)) is True  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reference_entities": ("entity-1",)},  # a bare @Name mention: no name pair here
        {"reference_entities": ("entity-1",), "reference_entity_names": ("",)},
        {
            "reference_entities": ("entity-1", "entity-2"),
            "reference_entity_names": ("Skipper", "Mate"),
        },
        {**_SKIPPER, "refs": (_IN_PROJECT,)},
        {**_SKIPPER, "count": 2},
    ],
)
def test_direct_can_serve_refuses_unmeasured_character_shapes(kwargs: dict[str, object]) -> None:
    assert direct_can_serve(GenerateImageRequest(prompt="x", **kwargs)) is False  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_direct_http_sends_the_character_pair(
    transport: DirectHttpTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gflow_cli.api.recaptcha.TokenMinter.mint", AsyncMock(return_value="mock_token")
    )
    builder = MagicMock(return_value="f.req=mock_body&at=at-x&")
    monkeypatch.setattr("gflow_cli.api.transports.direct_http.build_ogiz0b_body", builder)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, text='<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
            )
        return httpx.Response(200, text=_valid_ogiz0b_text())

    transport._http_transport = httpx.MockTransport(handler)
    req = GenerateImageRequest(prompt="x", **_SKIPPER)  # type: ignore[arg-type]
    await transport.generate_images(project_id="proj-123", request=req)
    assert builder.call_args.kwargs["characters"] == (("entity-1", "Skipper"),)
