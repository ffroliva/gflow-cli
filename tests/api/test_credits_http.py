from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from gflow_cli.api import credits as credits_api
from gflow_cli.auth import verification
from gflow_cli.auth.cookies import ChromeCookieSnapshot
from gflow_cli.errors import (
    AisandboxAuthError,
    AuthExpiredError,
    FlowApiError,
    SecurityError,
    WireFormatError,
)


@pytest.fixture
def profile_dir(tmp_path: Path) -> Path:
    profile = tmp_path / "gflow_home" / "profile_demo"
    profile.mkdir(parents=True)
    return profile


def _install_http(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_responses: list[tuple[int, object]],
    credits_response: tuple[int, object] = (
        200,
        {"credits": 12, "subscriptionCredits": 20, "sku": "G1_PRO"},
    ),
) -> tuple[list[httpx.Request], list[dict[str, object]]]:
    requests: list[httpx.Request] = []
    client_kwargs: list[dict[str, object]] = []
    real_async_client = httpx.AsyncClient

    def response(request: httpx.Request, status: int, payload: object) -> httpx.Response:
        if isinstance(payload, str):
            return httpx.Response(status, text=payload, request=request)
        return httpx.Response(status, json=payload, request=request)

    remaining_sessions = list(session_responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url == httpx.URL(verification.SESSION_API_URL):
            status, payload = remaining_sessions.pop(0)
        else:
            status, payload = credits_response
        return response(request, status, payload)

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        client_kwargs.append(dict(kwargs))
        return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

    snapshot = ChromeCookieSnapshot(
        httpx_cookies={"SIDCC": "session-secret"},
        google_session=True,
    )
    monkeypatch.setattr(
        verification,
        "get_chrome_cookie_snapshot",
        AsyncMock(return_value=snapshot),
    )
    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    return requests, client_kwargs


async def test_http_fast_path_scopes_cookies_to_session_host(
    monkeypatch: pytest.MonkeyPatch,
    profile_dir: Path,
    install_log_capture,
) -> None:
    requests, client_kwargs = _install_http(
        monkeypatch,
        session_responses=[(200, {"access_token": "ya29.test"})],
    )

    result = await credits_api.fetch_credits_http(profile_dir)

    assert result.credits == 12
    assert len(client_kwargs) == 2
    assert client_kwargs[0]["cookies"] == {"SIDCC": "session-secret"}
    assert "cookies" not in client_kwargs[1]
    assert client_kwargs[0]["follow_redirects"] is False
    assert client_kwargs[1]["follow_redirects"] is False
    assert requests[0].headers["cookie"] == "SIDCC=session-secret"
    assert "authorization" not in requests[0].headers
    assert "cookie" not in requests[1].headers
    assert requests[1].headers["authorization"] == "Bearer ya29.test"
    assert "key=" not in str(requests[1].url)
    event = install_log_capture.entries[0]
    assert event["event"] == "credits.http_fast_path_succeeded"
    assert event["status_code"] == 200
    assert event["response_keys"] == ["credits", "sku", "subscriptionCredits"]
    assert event["unknown_key_count"] == 0
    assert "ya29.test" not in str(event)
    assert "session-secret" not in str(event)


async def test_http_fast_path_reuses_session_retry_policy(
    monkeypatch: pytest.MonkeyPatch,
    profile_dir: Path,
) -> None:
    requests, _ = _install_http(
        monkeypatch,
        session_responses=[
            (503, {}),
            (200, {"access_token": "ya29.test"}),
        ],
    )
    sleep = AsyncMock()
    monkeypatch.setattr(verification.asyncio, "sleep", sleep)

    result = await credits_api.fetch_credits_http(profile_dir)

    assert result.credits == 12
    assert [request.url.host for request in requests].count("labs.google") == 2
    sleep.assert_awaited_once_with(1.0)


@pytest.mark.parametrize(
    ("session_status", "session_payload", "expected_type"),
    [
        (401, {}, AuthExpiredError),
        (403, {}, AuthExpiredError),
        (500, {}, FlowApiError),
        (200, "not-json", WireFormatError),
        (200, [], WireFormatError),
        (200, {"user": {}}, AisandboxAuthError),
    ],
)
async def test_http_fast_path_rejects_bad_session_responses(
    monkeypatch: pytest.MonkeyPatch,
    profile_dir: Path,
    session_status: int,
    session_payload: object,
    expected_type: type[Exception],
) -> None:
    _install_http(
        monkeypatch,
        session_responses=[(session_status, session_payload)],
    )

    with pytest.raises(expected_type) as caught:
        await credits_api.fetch_credits_http(profile_dir)

    assert type(caught.value) is expected_type


@pytest.mark.parametrize(
    ("credits_status", "credits_payload", "expected_type"),
    [
        (401, {}, AisandboxAuthError),
        (403, {}, AisandboxAuthError),
        (500, {}, FlowApiError),
        (200, "not-json", WireFormatError),
        (200, [], WireFormatError),
        (200, {}, WireFormatError),
    ],
)
async def test_http_fast_path_rejects_bad_credits_responses(
    monkeypatch: pytest.MonkeyPatch,
    profile_dir: Path,
    credits_status: int,
    credits_payload: object,
    expected_type: type[Exception],
) -> None:
    _install_http(
        monkeypatch,
        session_responses=[(200, {"access_token": "ya29.test"})],
        credits_response=(credits_status, credits_payload),
    )

    with pytest.raises(expected_type) as caught:
        await credits_api.fetch_credits_http(profile_dir)

    assert type(caught.value) is expected_type


async def test_http_fast_path_rejects_profile_outside_home_before_cookie_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookie_snapshot = AsyncMock()
    monkeypatch.setattr(verification, "get_chrome_cookie_snapshot", cookie_snapshot)

    with pytest.raises(SecurityError):
        await credits_api.fetch_credits_http(Path("/outside-gflow-home"))

    cookie_snapshot.assert_not_awaited()
