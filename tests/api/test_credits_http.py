from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from gflow_cli.api import credits as credits_api
from gflow_cli.auth.cookies import ChromeCookieSnapshot
from gflow_cli.errors import AisandboxAuthError, WireFormatError


class _FakeClient:
    calls: list[tuple[str, dict[str, str] | None]] = []

    def __init__(self, **kwargs: object) -> None:
        assert kwargs["cookies"] == {"session": "cookie"}

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
        self.calls.append((url, headers))
        request = httpx.Request("GET", url)
        if url == credits_api.SESSION_API_URL:
            return httpx.Response(200, json={"access_token": "ya29.test"}, request=request)
        return httpx.Response(
            200,
            json={"credits": 12, "subscriptionCredits": 20, "sku": "G1_PRO"},
            request=request,
        )


async def test_http_fast_path_uses_cookie_session_then_bearer(monkeypatch) -> None:
    async def cookies(profile_dir: Path) -> ChromeCookieSnapshot:
        assert profile_dir == Path("/profile")
        return ChromeCookieSnapshot(httpx_cookies={"session": "cookie"}, google_session=True)

    _FakeClient.calls = []
    monkeypatch.setattr(credits_api, "get_chrome_cookie_snapshot", cookies)
    monkeypatch.setattr(credits_api.httpx, "AsyncClient", _FakeClient)

    result = await credits_api.fetch_credits_http(Path("/profile"))

    assert result.credits == 12
    assert [call[0] for call in _FakeClient.calls] == [
        credits_api.SESSION_API_URL,
        credits_api.routes.CREDITS,
    ]
    session_headers = _FakeClient.calls[0][1]
    credits_headers = _FakeClient.calls[1][1]
    assert session_headers is not None and "authorization" not in session_headers
    assert credits_headers is not None and credits_headers["authorization"] == "Bearer ya29.test"
    assert "key=" not in _FakeClient.calls[1][0]


async def test_http_fast_path_rejects_session_without_token(monkeypatch) -> None:
    class MissingTokenClient(_FakeClient):
        async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
            request = httpx.Request("GET", url)
            return httpx.Response(200, json={"user": {}}, request=request)

    async def cookies(profile_dir: Path) -> ChromeCookieSnapshot:
        return ChromeCookieSnapshot(httpx_cookies={"session": "cookie"}, google_session=True)

    monkeypatch.setattr(credits_api, "get_chrome_cookie_snapshot", cookies)
    monkeypatch.setattr(credits_api.httpx, "AsyncClient", MissingTokenClient)

    with pytest.raises(AisandboxAuthError, match="no access_token"):
        await credits_api.fetch_credits_http(Path("/profile"))


async def test_http_fast_path_rejects_malformed_credits(monkeypatch) -> None:
    class MalformedCreditsClient(_FakeClient):
        async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
            request = httpx.Request("GET", url)
            payload = {"access_token": "ya29.test"} if url == credits_api.SESSION_API_URL else {}
            return httpx.Response(200, json=payload, request=request)

    async def cookies(profile_dir: Path) -> ChromeCookieSnapshot:
        return ChromeCookieSnapshot(httpx_cookies={"session": "cookie"}, google_session=True)

    monkeypatch.setattr(credits_api, "get_chrome_cookie_snapshot", cookies)
    monkeypatch.setattr(credits_api.httpx, "AsyncClient", MalformedCreditsClient)

    with pytest.raises(WireFormatError, match="credits"):
        await credits_api.fetch_credits_http(Path("/profile"))
