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
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE
from starlette.testclient import TestClient

from gflow_cli.data.store import DataStore
from gflow_cli.mcp import openai_api, openai_errors
from gflow_cli.mcp import tools as mcp_tools
from gflow_cli.mcp.server import build_app
from gflow_cli.worker.queue import QueueRepository
from tests.mcp.openai_fakes import FakeTool

BASE_URL = "http://127.0.0.1:8000"
PATH = "/v1/images/generations"
#: Not a credential — a fixed literal for the auth tests.
TOKEN = "test-daemon-token"
ERRORS = "https://gflow-cli.dev/errors/"
#: OpenAI's error ``type`` vocabulary; every response must use one of these.
ERROR_TYPES = frozenset(
    {
        "invalid_request_error",
        "authentication_error",
        "permission_error",
        "rate_limit_error",
        "server_error",
    }
)

SetTool = Callable[[dict[str, Any]], FakeTool]


@pytest.fixture
def fake_tool(set_image_tool: SetTool, png_files: list[str]) -> FakeTool:
    return set_image_tool({"status": "completed", "files": png_files})


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
    assert error["type"] in ERROR_TYPES
    return error


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


@pytest.mark.parametrize("stream", [False, None, 0])
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
    assert "accepts Google Flow image models only" in error["message"]
    if isinstance(model, str):  # Model.from_cli's own text lists the accepted names
        for alias in ("nano2", "nano-pro", "image4"):
            assert alias in error["message"]
    assert fake_tool.calls == []


def test_stream_true_is_400(client: TestClient, fake_tool: FakeTool) -> None:
    error = _openai_error(client.post(PATH, json={"prompt": "x", "stream": True}), 400)

    assert error["param"] == "stream"
    assert "not supported" in error["message"]
    assert fake_tool.calls == []


@pytest.mark.parametrize("stream", ["true", "yes", 1, [True]])
def test_non_boolean_stream_is_400_naming_the_type(
    client: TestClient, fake_tool: FakeTool, stream: object
) -> None:
    error = _openai_error(client.post(PATH, json={"prompt": "x", "stream": stream}), 400)

    assert error["param"] == "stream"
    assert "boolean" in error["message"]
    assert fake_tool.calls == []


def test_validation_refusals_say_nothing_was_charged(
    client: TestClient, fake_tool: FakeTool
) -> None:
    error = _openai_error(client.post(PATH, json={"prompt": ""}), 400)

    assert error["charged"] == "no"
    assert error["gflow_type"] == ERRORS + "bad-parameter"


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
    raw = b'{"prompt": "' + b"x" * (DEFAULT_MAX_REQUEST_BODY_SIZE + 1) + b'"}'

    resp = client.post(PATH, content=raw, headers={"Content-Type": "application/json"})

    error = _openai_error(resp, 413)
    assert (error["code"], error["charged"]) == ("request_too_large", "no")
    assert fake_tool.calls == []


def test_chunked_body_over_the_cap_is_413(client: TestClient, fake_tool: FakeTool) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"prompt": "'
        for _ in range(DEFAULT_MAX_REQUEST_BODY_SIZE // 65536 + 1):
            yield b"x" * 65536
        yield b'"}'

    resp = client.post(PATH, content=chunks(), headers={"Content-Type": "application/json"})

    error = _openai_error(resp, 413)
    assert (error["code"], error["charged"]) == ("request_too_large", "no")
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
    # Flow refused the daemon's request: nothing the caller changes would help.
    (
        {"type": ERRORS + "waf-rejection", "status": 403, "detail": "blocked"},
        502,
        "server_error",
        "upstream_rejected",
    ),
    (
        {"type": ERRORS + "recaptcha-mint", "status": 403, "detail": "no token"},
        502,
        "server_error",
        "upstream_rejected",
    ),
    # The daemon's own configuration is wrong: a server fault, not the caller's.
    (
        {"type": ERRORS + "no-profile", "status": 400, "detail": "none"},
        500,
        "server_error",
        "server_misconfigured",
    ),
    (
        {"type": ERRORS + "no-default-profile", "status": 400, "detail": "several"},
        500,
        "server_error",
        "server_misconfigured",
    ),
    (
        {"type": ERRORS + "configuration", "status": 400, "detail": "bad env"},
        500,
        "server_error",
        "server_misconfigured",
    ),
    (
        {"type": ERRORS + "profile-access", "status": 400, "detail": "denied"},
        500,
        "server_error",
        "server_misconfigured",
    ),
    (
        {"type": ERRORS + "profile-engine-downgrade", "status": 400, "detail": "old"},
        500,
        "server_error",
        "server_misconfigured",
    ),
    (
        {"type": ERRORS + "browser-engine-unavailable", "status": 400, "detail": "none"},
        500,
        "server_error",
        "server_misconfigured",
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
    # Flow's 4xx is about the daemon's request to Flow, never the caller's to gflow.
    (
        {"type": ERRORS + "api-error", "status": 404, "detail": "missing"},
        502,
        "server_error",
        "upstream_error",
    ),
    (
        {"type": ERRORS + "api-error", "status": 400, "detail": "bad wire"},
        502,
        "server_error",
        "upstream_error",
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
    set_image_tool: SetTool,
    problem: dict[str, Any],
    status: int,
    type_: str,
    code: str,
) -> None:
    set_image_tool({"status": "failed", "task_id": "t-1", "error": problem})

    error = _openai_error(client.post(PATH, json={"prompt": "x"}), status)

    assert (error["type"], error["code"], error["param"]) == (type_, code, None)
    assert error["gflow_type"] == problem["type"]
    assert error["task_id"] == "t-1"


def test_mapping_table_rows_are_all_tested() -> None:
    tested = {row[0]["type"] for row in MAPPING_ROWS}
    assert set(openai_errors.BY_GFLOW_TYPE) <= tested


def test_gflow_facts_ride_along_as_extra_keys(client: TestClient, set_image_tool: SetTool) -> None:
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
    set_image_tool({"status": "error", "error": problem})

    resp = client.post(PATH, json={"prompt": "x"})
    error = _openai_error(resp, 502)

    assert error["message"] == "reset"  # detail first: it is the human sentence
    assert error["charged"] == "maybe"
    assert error["retryable"] is False
    assert error["next_action"] == "reconcile"
    assert error["operation_id"] == "op-9"
    assert error["exit_code"] == 6
    assert resp.headers["x-should-retry"] == "false"


def test_upstream_auth_failure_says_to_renew(client: TestClient, set_image_tool: SetTool) -> None:
    problem = {"type": ERRORS + "auth-expired", "status": 401, "detail": "Session expired."}
    set_image_tool({"status": "error", "error": problem})

    error = _openai_error(client.post(PATH, json={"prompt": "x"}), 502)

    assert "gflow auth renew" in error["message"]


@pytest.mark.parametrize("kind", ["waf-rejection", "recaptcha-mint"])
def test_upstream_rejection_does_not_advise_renewing_the_session(
    client: TestClient, set_image_tool: SetTool, kind: str
) -> None:
    problem = {"type": ERRORS + kind, "status": 403, "detail": "Flow refused the request."}
    set_image_tool({"status": "error", "error": problem})

    error = _openai_error(client.post(PATH, json={"prompt": "x"}), 502)

    assert "renew" not in error["message"]


@pytest.mark.parametrize(
    "problem",
    [
        {"type": ERRORS + "no-profile", "status": 400, "detail": "No gflow profiles exist."},
        # The route never sends a profile or project, so these come from the daemon env.
        {"type": ERRORS + "bad-parameter", "title": "Invalid Profile", "detail": "x/y"},
        {
            "type": ERRORS + "bad-parameter",
            "title": "Default Project Belongs To Another Profile",
            "detail": "GFLOW_CLI_PROJECT names a project of another profile.",
        },
    ],
)
def test_daemon_misconfiguration_is_500_naming_the_daemon(
    client: TestClient, set_image_tool: SetTool, problem: dict[str, Any]
) -> None:
    set_image_tool({"status": "error", "error": problem})

    error = _openai_error(client.post(PATH, json={"prompt": "x"}), 500)

    assert (error["type"], error["code"]) == ("server_error", "server_misconfigured")
    assert "gflow serve" in error["message"]
    assert problem["detail"] in error["message"]


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        ({"detail": "d", "message": "m", "title": "t"}, "d"),
        ({"message": "m", "title": "t"}, "m"),
        ({"title": "t", "type": ERRORS + "network"}, "t"),
        ({"status": 500, "charged": "no"}, "Generation failed."),
    ],
)
def test_message_falls_back_detail_message_title_then_generic(
    client: TestClient, set_image_tool: SetTool, problem: dict[str, Any], message: str
) -> None:
    set_image_tool({"status": "failed", "error": problem})

    assert _openai_error(client.post(PATH, json={"prompt": "x"}), 500)["message"] == message


def test_tool_error_given_as_string_is_500(client: TestClient, set_image_tool: SetTool) -> None:
    set_image_tool({"status": "error", "error": "boom"})

    error = _openai_error(client.post(PATH, json={"prompt": "x"}), 500)

    assert error["message"] == "boom"
    assert error["type"] == "server_error"


def test_local_token_bucket_429_lets_the_sdk_retry_after_one_refill(
    client: TestClient, set_image_tool: SetTool
) -> None:
    set_image_tool(mcp_tools._rate_limited_envelope())  # pyright: ignore[reportPrivateUsage]

    resp = client.post(PATH, json={"prompt": "x"})

    assert _openai_error(resp, 429)["code"] == "rate_limit_exceeded"
    assert resp.headers["retry-after"] == str(openai_errors.RETRY_AFTER_SECONDS)
    # Refused before anything ran and the bucket refills: the SDK may retry.
    assert "x-should-retry" not in resp.headers


def test_flow_quota_429_uses_its_own_retry_after_and_forbids_sdk_retries(
    client: TestClient, set_image_tool: SetTool
) -> None:
    problem = {
        "type": ERRORS + "rate-limit",
        "status": 429,
        "detail": "Daily image quota reached.",
        "retry_after": 3600,
        "charged": "no",
        "retryable": True,
    }
    set_image_tool({"status": "error", "task_id": "t-3", "error": problem})

    resp = client.post(PATH, json={"prompt": "x"})
    error = _openai_error(resp, 429)

    assert resp.headers["retry-after"] == "3600"
    assert error["retry_after"] == 3600
    assert resp.headers["x-should-retry"] == "false"


def test_insufficient_credits_forbids_sdk_retries(
    client: TestClient, set_image_tool: SetTool
) -> None:
    problem = {"type": ERRORS + "insufficient-credits", "detail": "empty", "charged": "no"}
    set_image_tool({"status": "error", "error": problem})

    resp = client.post(PATH, json={"prompt": "x"})

    assert _openai_error(resp, 429)["code"] == "insufficient_quota"
    assert resp.headers["x-should-retry"] == "false"


@pytest.mark.parametrize(
    ("charged", "retryable"),
    [("maybe", True), ("yes", True), ("no", False), ("no", True), (None, None)],
)
def test_tool_failures_forbid_sdk_retries(
    client: TestClient, set_image_tool: SetTool, charged: object, retryable: object
) -> None:
    problem = {"type": ERRORS + "network", "status": 500, "detail": "d"}
    problem |= {k: v for k, v in (("charged", charged), ("retryable", retryable)) if v is not None}
    set_image_tool({"status": "failed", "error": problem})

    resp = client.post(PATH, json={"prompt": "x"})

    assert resp.headers["x-should-retry"] == "false"


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_unfinished_task_is_504_naming_the_task(
    client: TestClient, set_image_tool: SetTool, status: str
) -> None:
    set_image_tool({"status": status, "task_id": "task-42"})

    resp = client.post(PATH, json={"prompt": "x"})
    error = _openai_error(resp, 504)

    assert (error["type"], error["code"]) == ("server_error", "generation_timeout")
    assert error["task_id"] == "task-42"
    assert "task-42" in error["message"]
    assert resp.headers["x-should-retry"] == "false"


@pytest.mark.parametrize("row_status", ["pending", "processing"])
def test_still_unfinished_row_from_wait_true_is_504(
    client: TestClient, set_image_tool: SetTool, tmp_path: Path, row_status: str
) -> None:
    # The envelope wait=True really returns when the row is left unfinished (for example
    # claimed by another worker): built by task_payload, not written by hand.
    with DataStore.open(tmp_path / "q.db") as store:
        store.conn.execute(
            "INSERT OR IGNORE INTO profiles (name, first_seen_at) VALUES ('p', CURRENT_TIMESTAMP)"
        )
        repo = QueueRepository(store)
        repo.enqueue_task(task_id="t-9", profile_name="p", task_type="t2i", payload={"prompt": "x"})
        if row_status != "pending":
            repo.update_task_status("t-9", row_status)
        db_path = store.path
    set_image_tool(mcp_tools.task_payload("p", "t-9", db_path))

    resp = client.post(PATH, json={"prompt": "x"})
    error = _openai_error(resp, 504)

    assert error["code"] == "generation_timeout"
    assert error["task_id"] == "t-9"
    assert error["charged"] == "maybe"


def test_completed_without_files_is_502(client: TestClient, set_image_tool: SetTool) -> None:
    set_image_tool({"status": "completed", "files": []})

    resp = client.post(PATH, json={"prompt": "x"})

    assert _openai_error(resp, 502)["type"] == "server_error"
    assert resp.headers["x-should-retry"] == "false"


def test_unreadable_output_file_is_500_without_retry_or_server_path(
    client: TestClient, set_image_tool: SetTool, tmp_path: Path
) -> None:
    missing = tmp_path / "gone.png"
    set_image_tool({"status": "completed", "task_id": "t-7", "files": [str(missing)]})

    resp = client.post(PATH, json={"prompt": "x"})
    error = _openai_error(resp, 500)

    assert error["code"] == "output_unreadable"
    assert error["charged"] == "yes"
    assert str(tmp_path) not in error["message"]
    assert "gone.png" not in error["message"]
    assert resp.headers["x-should-retry"] == "false"


def test_requests_are_counted_under_their_own_telemetry_client(
    client: TestClient,
    set_image_tool: SetTool,
    png_files: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []
    fake = set_image_tool({"status": "completed", "files": png_files})

    async def spy(**kwargs: Any) -> dict[str, Any]:
        seen.append(mcp_tools.telemetry_client.get())
        return await fake(**kwargs)

    monkeypatch.setattr(mcp_tools, "gflow_generate_image", spy)
    assert client.post(PATH, json={"prompt": "x"}).status_code == 200

    assert seen == ["openai-http"]
    assert mcp_tools.telemetry_client.get() is None


# --- scenario 3: no-spend ----------------------------------------------------------


def test_no_spend_refuses_without_calling_the_tool(
    client: TestClient, fake_tool: FakeTool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_MCP_NO_SPEND", "1")

    error = _openai_error(client.post(PATH, json={"prompt": "x"}), 403)

    assert (error["type"], error["code"]) == ("permission_error", "no_spend")
    assert error["charged"] == "no"
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

    error = _openai_error(resp, 421)
    assert (error["type"], error["charged"]) == ("invalid_request_error", "no")
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

    error = _openai_error(resp, 415)
    assert (error["code"], error["charged"]) == ("unsupported_media_type", "no")
    assert fake_tool.calls == []


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "null", "http://127.0.0.1.evil.example"]
)
def test_cross_site_origin_is_403_without_token(
    client: TestClient, fake_tool: FakeTool, origin: str
) -> None:
    resp = client.post(PATH, json={"prompt": "x"}, headers={"Origin": origin})

    error = _openai_error(resp, 403)
    assert (error["type"], error["charged"]) == ("permission_error", "no")
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

    error = _openai_error(resp, 405)
    assert (error["code"], error["charged"]) == ("method_not_allowed", "no")
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
