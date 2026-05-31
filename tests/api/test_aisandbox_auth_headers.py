import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import AisandboxAuthError, AuthMissingError


def _make_client() -> FlowApiClient:
    # Construct without entering the async context (no browser launched).
    return FlowApiClient.__new__(FlowApiClient)


class _FakeApiResp:
    def __init__(self, status: int = 200, body: str = "{}") -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body


class _FakeCtxRequest:
    def __init__(self, resp: _FakeApiResp) -> None:
        self._resp = resp
        self.calls: list[str] = []

    async def get(self, url: str) -> _FakeApiResp:
        self.calls.append(url)
        return self._resp


class _FakeContext:
    def __init__(self, resp: _FakeApiResp) -> None:
        self.request = _FakeCtxRequest(resp)


@pytest.mark.unit
def test_is_aisandbox_url_discriminates_host():
    c = _make_client()
    assert c._is_aisandbox_url("https://aisandbox-pa.googleapis.com/v1/flow/x")
    assert not c._is_aisandbox_url("https://labs.google/fx/api/trpc/project.createProject")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_aisandbox_auth_headers_builds_bearer_from_cached_token():
    c = _make_client()
    c._access_token = "ya29.FAKE"
    c._access_token_exp = 9_999_999_999.0
    headers = await c._aisandbox_auth_headers()
    assert headers["authorization"] == "Bearer ya29.FAKE"
    assert headers["origin"] == "https://labs.google"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_access_token_uses_context_request_not_a_page():
    """Regression: token fetch must use self._context.request, never a Page.

    A nested ``_checkout_page`` inside a ``_post_json`` attempt() deadlocks a
    size-1 pool (live-smoke incident 2026-05-31).
    """
    c = _make_client()
    c._access_token = None
    c._access_token_exp = 0.0
    c._context = _FakeContext(
        _FakeApiResp(200, '{"access_token":"ya29.CTX","expires":"2999-01-01T00:00:00Z"}')
    )

    def _boom():
        raise AssertionError("_checkout_page must NOT be called from token fetch")

    c._checkout_page = _boom  # type: ignore[method-assign]
    assert await c._ensure_access_token() == "ya29.CTX"
    assert c._context.request.calls == ["https://labs.google/fx/api/auth/session"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_access_token_reuses_unexpired_cache(monkeypatch):
    c = _make_client()
    c._access_token = "ya29.CACHED"
    c._access_token_exp = 9_999_999_999.0

    async def _boom():
        raise AssertionError("must not re-fetch a still-valid cached token")

    monkeypatch.setattr(c, "_fetch_access_token", _boom)
    assert await c._ensure_access_token() == "ya29.CACHED"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_access_token_refetches_when_expired(monkeypatch):
    c = _make_client()
    c._access_token = "ya29.OLD"
    c._access_token_exp = 1.0  # long past → expired

    async def fake_fetch():
        return ("ya29.NEW", 9_999_999_999.0)

    monkeypatch.setattr(c, "_fetch_access_token", fake_fetch)
    assert await c._ensure_access_token() == "ya29.NEW"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_access_token_raises_when_no_token_in_session():
    c = _make_client()
    c._context = _FakeContext(_FakeApiResp(200, '{"user":{"email":"x@y.z"}}'))
    with pytest.raises(AisandboxAuthError):
        await c._fetch_access_token()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_access_token_raises_without_context():
    c = _make_client()
    c._context = None
    with pytest.raises(AuthMissingError):
        await c._fetch_access_token()
