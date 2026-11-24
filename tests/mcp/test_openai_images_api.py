# SPDX-License-Identifier: MIT
"""``POST /v1/images/generations`` on ``gflow serve`` (#1365, images half).

The route is an adapter over ``tools.gflow_generate_image``, so the tool is faked here and
the tests pin the adapter: request validation, the size→aspect mapping, the result→OpenAI
mapping, and the guardrails the route must not bypass (token, rebinding, no-spend).
Scenario numbers refer to ``docs/superpowers/plans/2026-11-24-openai-images-api/SCENARIO.md``.

Each test builds its own app: ``streamable_http_app()`` mints a session manager whose
``.run()`` may be entered once (see ``test_serve_auth.py``).
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from gflow_cli.mcp import openai_api
from gflow_cli.mcp import tools as mcp_tools
from gflow_cli.mcp.server import build_app

BASE_URL = "http://127.0.0.1:8000"
PATH = "/v1/images/generations"
#: Not a credential — a fixed literal for the auth tests.
TOKEN = "test-daemon-token"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeTool:
    """Records each call and answers with ``result`` (files written under ``tmp``)."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.result


@pytest.fixture
def png_files(tmp_path: Path) -> list[str]:
    paths = []
    for i in range(2):
        p = tmp_path / f"img{i}.png"
        p.write_bytes(PNG + bytes([i]))
        paths.append(str(p))
    return paths


@pytest.fixture
def fake_tool(monkeypatch: pytest.MonkeyPatch, png_files: list[str]) -> FakeTool:
    fake = FakeTool({"status": "completed", "files": png_files})
    monkeypatch.setattr(mcp_tools, "gflow_generate_image", fake)
    monkeypatch.delenv("GFLOW_MCP_NO_SPEND", raising=False)
    return fake


def _client(token: str | None = None) -> Iterator[TestClient]:
    app = build_app(transport="http", host="127.0.0.1", token=token)
    with TestClient(app, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield from _client()


# --- scenario 4: golden path -------------------------------------------------------


def test_golden_path_returns_b64_of_each_file(
    client: TestClient, fake_tool: FakeTool, png_files: list[str]
) -> None:
    resp = client.post(PATH, json={"prompt": "a red fox", "n": 2, "size": "1792x1024"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["created"], int)
    decoded = [base64.b64decode(d["b64_json"]) for d in body["data"]]
    assert decoded == [Path(p).read_bytes() for p in png_files]
    assert fake_tool.calls == [
        {"prompt": "a red fox", "model": "nano2", "aspect": "16:9", "count": 2, "wait": True}
    ]


def test_gflow_model_alias_is_passed_through(client: TestClient, fake_tool: FakeTool) -> None:
    resp = client.post(PATH, json={"prompt": "x", "model": "nano-pro"})

    assert resp.status_code == 200, resp.text
    assert fake_tool.calls[0]["model"] == "nano-pro"


# --- scenario 5: size → aspect -----------------------------------------------------


@pytest.mark.parametrize(
    ("size", "aspect"),
    [
        (None, "1:1"),
        ("auto", "1:1"),
        ("1024x1024", "1:1"),
        ("1024x1792", "9:16"),
        ("1792x1024", "16:9"),
        ("1536x1024", "4:3"),
        ("1024x1536", "3:4"),
        ("768x1024", "3:4"),
        ("1920x1080", "16:9"),
        ("16:9", "16:9"),
        ("3:4", "3:4"),
    ],
)
def test_size_maps_to_nearest_flow_aspect(size: str | None, aspect: str) -> None:
    assert openai_api.aspect_for_size(size) == aspect


@pytest.mark.parametrize("size", ["big", "0x1024", "1024x", "2:1", "1024*1024", 1024])
def test_bad_size_is_400(client: TestClient, fake_tool: FakeTool, size: object) -> None:
    resp = client.post(PATH, json={"prompt": "x", "size": size})

    assert resp.status_code == 400
    assert resp.json()["error"]["param"] == "size"
    assert fake_tool.calls == []


# --- scenarios 6-9: validation -----------------------------------------------------


def test_response_format_url_is_400(client: TestClient, fake_tool: FakeTool) -> None:
    resp = client.post(PATH, json={"prompt": "x", "response_format": "url"})

    assert resp.status_code == 400
    assert resp.json()["error"]["param"] == "response_format"
    assert fake_tool.calls == []


def test_response_format_b64_json_is_accepted(client: TestClient, fake_tool: FakeTool) -> None:
    resp = client.post(PATH, json={"prompt": "x", "response_format": "b64_json"})

    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("n", [0, 5, "2", 1.5, True])
def test_bad_n_is_400(client: TestClient, fake_tool: FakeTool, n: object) -> None:
    resp = client.post(PATH, json={"prompt": "x", "n": n})

    assert resp.status_code == 400
    assert resp.json()["error"]["param"] == "n"
    assert fake_tool.calls == []


def test_unknown_model_is_400_naming_aliases(client: TestClient, fake_tool: FakeTool) -> None:
    resp = client.post(PATH, json={"prompt": "x", "model": "gpt-image-1"})

    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["param"] == "model"
    assert "nano2" in error["message"]
    assert fake_tool.calls == []


@pytest.mark.parametrize("body", [{}, {"prompt": ""}, {"prompt": "   "}, {"prompt": 3}])
def test_missing_prompt_is_400(
    client: TestClient, fake_tool: FakeTool, body: dict[str, Any]
) -> None:
    resp = client.post(PATH, json=body)

    assert resp.status_code == 400
    assert resp.json()["error"]["param"] == "prompt"
    assert fake_tool.calls == []


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]"])
def test_body_that_is_not_a_json_object_is_400(
    client: TestClient, fake_tool: FakeTool, raw: bytes
) -> None:
    resp = client.post(PATH, content=raw, headers={"Content-Type": "application/json"})

    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request_error"
    assert fake_tool.calls == []


# --- scenario 13: OpenAI-only knobs are ignored ------------------------------------


def test_openai_only_fields_are_ignored(client: TestClient, fake_tool: FakeTool) -> None:
    resp = client.post(
        PATH,
        json={
            "prompt": "x",
            "quality": "high",
            "style": "vivid",
            "user": "u1",
            "background": "auto",
            "output_format": "png",
        },
    )

    assert resp.status_code == 200, resp.text
    assert set(fake_tool.calls[0]) == {"prompt", "model", "aspect", "count", "wait"}


# --- scenarios 10-12: result mapping -----------------------------------------------


def test_tool_error_keeps_problem_status_and_type(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = {
        "type": "https://gflow-cli.dev/errors/auth-expired",
        "title": "Auth Expired",
        "status": 401,
        "detail": "Session expired.",
        "message": "[AuthExpiredError] Session expired.",
    }
    monkeypatch.setattr(
        mcp_tools, "gflow_generate_image", FakeTool({"status": "error", "error": problem})
    )

    resp = client.post(PATH, json={"prompt": "x"})

    assert resp.status_code == 401
    assert resp.json() == {
        "error": {
            "message": "[AuthExpiredError] Session expired.",
            "type": "gflow_error",
            "code": "https://gflow-cli.dev/errors/auth-expired",
            "param": None,
        }
    }


@pytest.mark.parametrize("status", [None, "teapot", 200, 99])
def test_tool_error_without_usable_status_is_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, status: object
) -> None:
    problem = {"type": "https://gflow-cli.dev/errors/unknown", "status": status, "detail": "d"}
    monkeypatch.setattr(
        mcp_tools, "gflow_generate_image", FakeTool({"status": "failed", "error": problem})
    )

    resp = client.post(PATH, json={"prompt": "x"})

    assert resp.status_code == 500
    assert resp.json()["error"]["message"] == "d"


def test_tool_error_given_as_string_is_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mcp_tools, "gflow_generate_image", FakeTool({"status": "error", "error": "boom"})
    )

    resp = client.post(PATH, json={"prompt": "x"})

    assert resp.status_code == 500
    assert resp.json()["error"]["message"] == "boom"


def test_rate_limited_is_429(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    problem = {"type": "https://gflow-cli.dev/errors/rate-limited", "detail": "slow down"}
    monkeypatch.setattr(
        mcp_tools, "gflow_generate_image", FakeTool({"status": "rate_limited", "error": problem})
    )

    resp = client.post(PATH, json={"prompt": "x"})

    assert resp.status_code == 429


def test_completed_without_files_is_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mcp_tools, "gflow_generate_image", FakeTool({"status": "completed", "files": []})
    )

    resp = client.post(PATH, json={"prompt": "x"})

    assert resp.status_code == 502


# --- scenario 3: no-spend ----------------------------------------------------------


def test_no_spend_refuses_without_calling_the_tool(
    client: TestClient, fake_tool: FakeTool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_MCP_NO_SPEND", "1")

    resp = client.post(PATH, json={"prompt": "x"})

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "no_spend"
    assert fake_tool.calls == []


# --- scenarios 1-2: auth and rebinding ---------------------------------------------


def test_token_required_when_configured(fake_tool: FakeTool) -> None:
    for client in _client(token=TOKEN):
        assert client.post(PATH, json={"prompt": "x"}).status_code == 401
        ok = client.post(PATH, json={"prompt": "x"}, headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200, ok.text
    assert len(fake_tool.calls) == 1


def test_non_loopback_host_is_421_without_token(client: TestClient, fake_tool: FakeTool) -> None:
    resp = client.post(PATH, json={"prompt": "x"}, headers={"Host": "evil.example:8000"})

    assert resp.status_code == 421
    assert fake_tool.calls == []


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1", "[::1]:8000"])
def test_loopback_hosts_are_served_without_token(
    client: TestClient, fake_tool: FakeTool, host: str
) -> None:
    resp = client.post(PATH, json={"prompt": "x"}, headers={"Host": host})

    assert resp.status_code == 200, resp.text


def test_any_host_is_served_with_a_valid_token(fake_tool: FakeTool) -> None:
    for client in _client(token=TOKEN):
        resp = client.post(
            PATH,
            json={"prompt": "x"},
            headers={"Host": "flow-img:8000", "Authorization": f"Bearer {TOKEN}"},
        )
        assert resp.status_code == 200, resp.text


def test_route_is_served_on_the_sse_transport_too(fake_tool: FakeTool) -> None:
    app = build_app(transport="sse", host="127.0.0.1", token=None)
    with TestClient(app, base_url=BASE_URL) as client:
        assert client.post(PATH, json={"prompt": "x"}).status_code == 200
