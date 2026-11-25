# SPDX-License-Identifier: MIT
"""``POST /v1/images/generations`` on ``gflow serve`` (#1365, images half).

The route is an adapter over ``tools.gflow_generate_image``, so the tool is faked here and
the tests pin the adapter: request validation, the size→aspect mapping, the translation of
gflow's problem details into OpenAI's error object, and the guardrails the route must not
bypass (token, rebinding, cross-site requests, no-spend).
Scenario numbers refer to ``docs/superpowers/plans/2026-11-24-openai-images-api/SCENARIO.md``.

Each test builds its own app: ``streamable_http_app()`` mints a session manager whose
``.run()`` may be entered once (see ``test_serve_auth.py``).
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from gflow_cli.mcp import openai_api, openai_errors
from gflow_cli.mcp import tools as mcp_tools
from gflow_cli.mcp.server import build_app

BASE_URL = "http://127.0.0.1:8000"
PATH = "/v1/images/generations"
#: Not a credential — a fixed literal for the auth tests.
TOKEN = "test-daemon-token"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
ERRORS = "https://gflow-cli.dev/errors/"


class FakeTool:
    """Records each call and answers with ``result`` (files written under ``tmp``)."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.result


@pytest.fixture(autouse=True)
def _spend_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    # An operator's GFLOW_MCP_NO_SPEND must not turn every test into a 403.
    monkeypatch.delenv("GFLOW_MCP_NO_SPEND", raising=False)


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
    return fake


def _client(token: str | None = None) -> Iterator[TestClient]:
    app = build_app(transport="http", host="127.0.0.1", token=token)
    with TestClient(app, base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield from _client()


def _openai_error(resp: httpx.Response, status: int) -> dict[str, Any]:
    """Assert ``resp`` is OpenAI's error object at ``status``; return its ``error``."""
    assert resp.status_code == status, resp.text
    error = resp.json()["error"]
    assert {"message", "type", "param", "code"} <= set(error)
    assert isinstance(error["message"], str)
    assert error["message"]
    assert error["type"] in openai_errors.ERROR_TYPES
    return error


def _set_result(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> FakeTool:
    fake = FakeTool(result)
    monkeypatch.setattr(mcp_tools, "gflow_generate_image", fake)
    return fake


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


def test_explicit_nulls_mean_absent(client: TestClient, fake_tool: FakeTool) -> None:
    body = {"prompt": "x", "n": None, "response_format": None, "size": None, "model": None}

    resp = client.post(PATH, json=body)

    assert resp.status_code == 200, resp.text
    assert fake_tool.calls == [
        {"prompt": "x", "model": "nano2", "aspect": "1:1", "count": 1, "wait": True}
    ]


@pytest.mark.parametrize("stream", [False, None])
def test_stream_false_is_accepted(client: TestClient, fake_tool: FakeTool, stream: object) -> None:
    assert client.post(PATH, json={"prompt": "x", "stream": stream}).status_code == 200


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
        # A ratio too large for a float division still has a nearest aspect.
        ("1" + "0" * 400 + "x1", "16:9"),
    ],
)
def test_size_maps_to_nearest_flow_aspect(size: str | None, aspect: str) -> None:
    assert openai_api.aspect_for_size(size) == aspect


@pytest.mark.parametrize(
    "size", ["big", "0x1024", "1024x", "2:1", "1024*1024", 1024, "9" * 5000 + "x1"]
)
def test_bad_size_is_400(client: TestClient, fake_tool: FakeTool, size: object) -> None:
    error = _openai_error(client.post(PATH, json={"prompt": "x", "size": size}), 400)

    assert error["param"] == "size"
    assert error["code"] == "invalid_value"
    assert fake_tool.calls == []


# --- scenarios 6-9: validation -----------------------------------------------------


def test_response_format_url_is_400(client: TestClient, fake_tool: FakeTool) -> None:
    resp = client.post(PATH, json={"prompt": "x", "response_format": "url"})

    assert _openai_error(resp, 400)["param"] == "response_format"
    assert fake_tool.calls == []


def test_response_format_b64_json_is_accepted(client: TestClient, fake_tool: FakeTool) -> None:
    resp = client.post(PATH, json={"prompt": "x", "response_format": "b64_json"})

    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("n", [0, 5, "2", 1.5, True, 10**30])
def test_bad_n_is_400(client: TestClient, fake_tool: FakeTool, n: object) -> None:
    assert _openai_error(client.post(PATH, json={"prompt": "x", "n": n}), 400)["param"] == "n"
    assert fake_tool.calls == []


@pytest.mark.parametrize("model", ["gpt-image-1", "dall-e-3", "dall-e-2", "", 3, ["nano2"]])
def test_non_flow_model_is_400_listing_flow_models(
    client: TestClient, fake_tool: FakeTool, model: object
) -> None:
    error = _openai_error(client.post(PATH, json={"prompt": "x", "model": model}), 400)

    assert error["param"] == "model"
    for alias in ("nano2", "nano-pro", "image4"):
        assert alias in error["message"]
    assert fake_tool.calls == []


def test_stream_true_is_400(client: TestClient, fake_tool: FakeTool) -> None:
    error = _openai_error(client.post(PATH, json={"prompt": "x", "stream": True}), 400)

    assert error["param"] == "stream"
    assert fake_tool.calls == []


@pytest.mark.parametrize("body", [{}, {"prompt": ""}, {"prompt": "   "}, {"prompt": 3}])
def test_missing_prompt_is_400(
    client: TestClient, fake_tool: FakeTool, body: dict[str, Any]
) -> None:
    assert _openai_error(client.post(PATH, json=body), 400)["param"] == "prompt"
    assert fake_tool.calls == []


@pytest.mark.parametrize(
    "raw",
    [b"not json", b"[1, 2]", b'{"prompt": "x", "deep": ' + b"[" * 200_000 + b"]" * 200_000 + b"}"],
)
def test_body_that_is_not_a_usable_json_object_is_400(
    client: TestClient, fake_tool: FakeTool, raw: bytes
) -> None:
    resp = client.post(PATH, content=raw, headers={"Content-Type": "application/json"})

    assert _openai_error(resp, 400)["type"] == "invalid_request_error"
    assert fake_tool.calls == []


def test_body_over_the_cap_is_413(client: TestClient, fake_tool: FakeTool) -> None:
    raw = b'{"prompt": "' + b"x" * (openai_api.MAX_BODY_BYTES + 1) + b'"}'

    resp = client.post(PATH, content=raw, headers={"Content-Type": "application/json"})

    assert _openai_error(resp, 413)["code"] == "request_too_large"
    assert fake_tool.calls == []


def test_chunked_body_over_the_cap_is_413(client: TestClient, fake_tool: FakeTool) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"prompt": "'
        for _ in range(openai_api.MAX_BODY_BYTES // 65536 + 1):
            yield b"x" * 65536
        yield b'"}'

    resp = client.post(PATH, content=chunks(), headers={"Content-Type": "application/json"})

    assert _openai_error(resp, 413)["code"] == "request_too_large"
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


# --- scenarios 10-12: translating gflow problems into OpenAI errors ----------------

#: One row per entry of ``openai_errors`` — (problem, status, OpenAI type, OpenAI code).
MAPPING_ROWS = [
    (
        {"type": ERRORS + "content-policy", "detail": "refused"},
        400,
        "invalid_request_error",
        "content_policy_violation",
    ),
    (
        {"type": ERRORS + "bad-parameter", "status": 400, "detail": "bad"},
        400,
        "invalid_request_error",
        "invalid_value",
    ),
    (
        {"type": ERRORS + "reference-not-found", "detail": "gone"},
        400,
        "invalid_request_error",
        "invalid_value",
    ),
    (
        {"type": ERRORS + "rate-limit", "detail": "slow"},
        429,
        "rate_limit_error",
        "rate_limit_exceeded",
    ),
    (
        {"type": ERRORS + "insufficient-credits", "detail": "empty"},
        429,
        "rate_limit_error",
        "insufficient_quota",
    ),
    (
        {"type": ERRORS + "auth-expired", "status": 401, "detail": "expired"},
        502,
        "server_error",
        "upstream_auth_failed",
    ),
    (
        {"type": ERRORS + "auth-missing", "detail": "no"},
        502,
        "server_error",
        "upstream_auth_failed",
    ),
    (
        {"type": ERRORS + "aisandbox-auth", "detail": "no"},
        502,
        "server_error",
        "upstream_auth_failed",
    ),
    (
        {"type": ERRORS + "auth-browser-rejected", "detail": "no"},
        502,
        "server_error",
        "upstream_auth_failed",
    ),
    (
        {"type": ERRORS + "transport-timeout", "detail": "slow"},
        504,
        "server_error",
        "generation_timeout",
    ),
    # Fallbacks on the problem's own HTTP status, for types the table does not name.
    (
        {"type": ERRORS + "api-error", "status": 403, "detail": "forbidden"},
        502,
        "server_error",
        "upstream_auth_failed",
    ),
    (
        {"type": ERRORS + "api-error", "status": 429, "detail": "busy"},
        429,
        "rate_limit_error",
        "rate_limit_exceeded",
    ),
    (
        {"type": ERRORS + "api-error", "status": 404, "detail": "missing"},
        404,
        "invalid_request_error",
        "invalid_value",
    ),
    (
        {"type": ERRORS + "api-error", "status": 503, "detail": "down"},
        503,
        "server_error",
        "generation_failed",
    ),
    (
        {"type": ERRORS + "unknown", "status": None, "detail": "d"},
        500,
        "server_error",
        "generation_failed",
    ),
    (
        {"type": ERRORS + "unknown", "status": "teapot", "detail": "d"},
        500,
        "server_error",
        "generation_failed",
    ),
    (
        {"type": ERRORS + "unknown", "status": 200, "detail": "d"},
        500,
        "server_error",
        "generation_failed",
    ),
]


@pytest.mark.parametrize(("problem", "status", "type_", "code"), MAPPING_ROWS)
def test_each_gflow_problem_maps_to_its_openai_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    problem: dict[str, Any],
    status: int,
    type_: str,
    code: str,
) -> None:
    _set_result(monkeypatch, {"status": "failed", "task_id": "t-1", "error": problem})

    error = _openai_error(client.post(PATH, json={"prompt": "x"}), status)

    assert (error["type"], error["code"], error["param"]) == (type_, code, None)
    assert error["gflow_type"] == problem["type"]
    assert error["task_id"] == "t-1"


def test_mapping_table_rows_are_all_tested() -> None:
    tested = {row[0]["type"] for row in MAPPING_ROWS}
    assert set(openai_errors.BY_GFLOW_TYPE) <= tested


def test_gflow_facts_ride_along_as_extra_keys(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = {
        "type": ERRORS + "network",
        "status": 502,
        "detail": "reset",
        "message": "[NetworkError] reset",
        "charged": "maybe",
        "retryable": False,
        "next_action": "reconcile",
        "operation_id": "op-9",
        "exit_code": 6,
    }
    _set_result(monkeypatch, {"status": "error", "error": problem})

    resp = client.post(PATH, json={"prompt": "x"})
    error = _openai_error(resp, 502)

    assert error["message"] == "[NetworkError] reset"
    assert error["charged"] == "maybe"
    assert error["retryable"] is False
    assert error["next_action"] == "reconcile"
    assert error["operation_id"] == "op-9"
    assert error["exit_code"] == 6
    assert resp.headers["x-should-retry"] == "false"


def test_upstream_auth_failure_says_to_renew(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = {"type": ERRORS + "auth-expired", "status": 401, "detail": "Session expired."}
    _set_result(monkeypatch, {"status": "error", "error": problem})

    error = _openai_error(client.post(PATH, json={"prompt": "x"}), 502)

    assert "gflow auth renew" in error["message"]


def test_tool_error_given_as_string_is_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_result(monkeypatch, {"status": "error", "error": "boom"})

    error = _openai_error(client.post(PATH, json={"prompt": "x"}), 500)

    assert error["message"] == "boom"
    assert error["type"] == "server_error"


def test_rate_limited_is_429_with_retry_after(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = {
        "type": ERRORS + "rate-limit",
        "detail": "slow down",
        "charged": "no",
        "retryable": True,
    }
    _set_result(monkeypatch, {"status": "rate_limited", "error": problem})

    resp = client.post(PATH, json={"prompt": "x"})

    assert _openai_error(resp, 429)["code"] == "rate_limit_exceeded"
    assert int(resp.headers["retry-after"]) > 0
    # Nothing was spent and the bucket refills: the SDK may retry.
    assert "x-should-retry" not in resp.headers


@pytest.mark.parametrize(
    ("charged", "retryable"), [("maybe", True), ("yes", True), ("no", False), (None, None)]
)
def test_failures_that_could_spend_again_forbid_sdk_retries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, charged: object, retryable: object
) -> None:
    problem = {"type": ERRORS + "network", "status": 500, "detail": "d"}
    problem |= {k: v for k, v in (("charged", charged), ("retryable", retryable)) if v is not None}
    _set_result(monkeypatch, {"status": "failed", "error": problem})

    resp = client.post(PATH, json={"prompt": "x"})

    assert resp.headers["x-should-retry"] == "false"


def test_retryable_uncharged_500_leaves_retry_to_the_sdk(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem = {"type": ERRORS + "network", "status": 500, "charged": "no", "retryable": True}
    _set_result(monkeypatch, {"status": "failed", "error": problem})

    resp = client.post(PATH, json={"prompt": "x"})

    assert resp.status_code == 500
    assert "x-should-retry" not in resp.headers


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_unfinished_task_is_504_naming_the_task(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    _set_result(monkeypatch, {"status": status, "task_id": "task-42"})

    resp = client.post(PATH, json={"prompt": "x"})
    error = _openai_error(resp, 504)

    assert (error["type"], error["code"]) == ("server_error", "generation_timeout")
    assert error["task_id"] == "task-42"
    assert "task-42" in error["message"]
    assert resp.headers["x-should-retry"] == "false"


def test_completed_without_files_is_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_result(monkeypatch, {"status": "completed", "files": []})

    resp = client.post(PATH, json={"prompt": "x"})

    assert _openai_error(resp, 502)["type"] == "server_error"
    assert resp.headers["x-should-retry"] == "false"


def test_unreadable_output_file_is_500_without_retry(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "gone.png"
    _set_result(monkeypatch, {"status": "completed", "task_id": "t-7", "files": [str(missing)]})

    resp = client.post(PATH, json={"prompt": "x"})
    error = _openai_error(resp, 500)

    assert error["code"] == "output_unreadable"
    assert error["charged"] == "yes"
    assert resp.headers["x-should-retry"] == "false"


# --- scenario 3: no-spend ----------------------------------------------------------


def test_no_spend_refuses_without_calling_the_tool(
    client: TestClient, fake_tool: FakeTool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_MCP_NO_SPEND", "1")

    error = _openai_error(client.post(PATH, json={"prompt": "x"}), 403)

    assert (error["type"], error["code"]) == ("permission_error", "no_spend")
    assert fake_tool.calls == []


# --- scenarios 1-2: auth, rebinding and cross-site requests -------------------------


def test_token_required_when_configured(fake_tool: FakeTool) -> None:
    for client in _client(token=TOKEN):
        resp = client.post(PATH, json={"prompt": "x"})
        error = _openai_error(resp, 401)
        assert (error["type"], error["code"]) == ("authentication_error", "invalid_api_key")
        assert resp.headers["www-authenticate"].startswith("Bearer")
        ok = client.post(PATH, json={"prompt": "x"}, headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200, ok.text
    assert len(fake_tool.calls) == 1


def test_mcp_path_keeps_its_plain_401(fake_tool: FakeTool) -> None:
    for client in _client(token=TOKEN):
        resp = client.post("/mcp", json={})
        assert resp.status_code == 401
        assert resp.text == "Unauthorized"


def test_non_loopback_host_is_421_without_token(client: TestClient, fake_tool: FakeTool) -> None:
    resp = client.post(PATH, json={"prompt": "x"}, headers={"Host": "evil.example:8000"})

    assert _openai_error(resp, 421)["type"] == "invalid_request_error"
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


@pytest.mark.parametrize(
    "content_type", [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"]
)
def test_non_json_content_type_is_415(
    client: TestClient, fake_tool: FakeTool, content_type: str | None
) -> None:
    headers = {} if content_type is None else {"Content-Type": content_type}

    resp = client.post(PATH, content=b'{"prompt": "x"}', headers=headers)

    assert _openai_error(resp, 415)["code"] == "unsupported_media_type"
    assert fake_tool.calls == []


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "null", "http://127.0.0.1.evil.example"]
)
def test_cross_site_origin_is_403_without_token(
    client: TestClient, fake_tool: FakeTool, origin: str
) -> None:
    resp = client.post(PATH, json={"prompt": "x"}, headers={"Origin": origin})

    assert _openai_error(resp, 403)["type"] == "permission_error"
    assert fake_tool.calls == []


def test_cross_site_text_plain_post_is_refused(client: TestClient, fake_tool: FakeTool) -> None:
    # What a hostile web page can send without a CORS preflight.
    resp = client.post(
        PATH,
        content=b'{"prompt": "x"}',
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )

    assert resp.status_code in (403, 415)
    assert fake_tool.calls == []


@pytest.mark.parametrize("origin", ["http://127.0.0.1:3000", "http://localhost:8000"])
def test_loopback_origin_is_served(client: TestClient, fake_tool: FakeTool, origin: str) -> None:
    resp = client.post(PATH, json={"prompt": "x"}, headers={"Origin": origin})

    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_other_methods_are_405_in_openai_shape(
    client: TestClient, fake_tool: FakeTool, method: str
) -> None:
    resp = client.request(method, PATH)

    assert _openai_error(resp, 405)["code"] == "method_not_allowed"
    assert resp.headers["allow"] == "POST"
    assert fake_tool.calls == []


def test_route_is_served_on_the_sse_transport_too(fake_tool: FakeTool) -> None:
    app = build_app(transport="sse", host="127.0.0.1", token=None)
    with TestClient(app, base_url=BASE_URL) as client:
        assert client.post(PATH, json={"prompt": "x"}).status_code == 200


# --- the error vocabulary ----------------------------------------------------------


def test_error_response_carries_all_four_keys_and_no_retry_by_default() -> None:
    resp = openai_errors.error_response(400, "bad", type_="invalid_request_error", code="x")

    assert resp.status_code == 400
    assert resp.headers["x-should-retry"] == "false"
    assert resp.body == (
        b'{"error":{"message":"bad","type":"invalid_request_error","param":null,"code":"x"}}'
    )
