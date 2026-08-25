import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import AisandboxAuthError


class _FakeResp:
    def __init__(self, status, body="{}"):
        self.status = status
        self._body = body

    async def text(self):
        return self._body


class _FakeRequest:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    async def post(self, url, *, data, headers):
        self.calls.append({"url": url, "headers": dict(headers)})
        return _FakeResp(self.statuses.pop(0))


class _FakeRequestPatch(_FakeRequest):
    async def patch(self, url, *, data, headers):
        self.calls.append({"url": url, "headers": dict(headers)})
        return _FakeResp(self.statuses.pop(0))


class _FakePage:
    def __init__(self, statuses):
        self.request = _FakeRequest(statuses)
        self.context = None


def _client_with_page(page, token="ya29.TOK"):
    c = FlowApiClient.__new__(FlowApiClient)
    c._access_token = token
    c._access_token_exp = 9_999_999_999.0  # far future → no re-fetch

    async def checkout():
        return page

    c._checkout_page = checkout  # type: ignore[method-assign]
    c._checkin_page = lambda p: None  # type: ignore[method-assign]
    return c


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_json_attaches_bearer_for_aisandbox():
    page = _FakePage([200])
    c = _client_with_page(page)
    await c._post_json(
        "https://aisandbox-pa.googleapis.com/v1/flow/projects/p/scenes",
        {"workflowIds": []},
    )
    assert page.request.calls[0]["headers"]["authorization"] == "Bearer ya29.TOK"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_json_does_not_attach_auth_for_bff():
    page = _FakePage([200])
    c = _client_with_page(page)
    await c._post_json(
        "https://labs.google/fx/api/trpc/project.createProject",
        {"json": {}},
        content_type="application/json",
    )
    assert "authorization" not in page.request.calls[0]["headers"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_json_attaches_origin_and_referer_for_bff():
    """A labs.google tRPC mutation must look like it came from the Flow SPA.

    Regression: `project.createProject` went out carrying only `content-type`,
    so Google answered 401 on a demonstrably live session while the
    UI-automation lane kept generating on the same cookie jar.
    """
    page = _FakePage([200])
    c = _client_with_page(page)
    await c._post_json(
        "https://labs.google/fx/api/trpc/project.createProject",
        {"json": {}},
        content_type="application/json",
    )
    sent = page.request.calls[0]["headers"]
    assert sent["origin"] == "https://labs.google"
    assert sent["referer"] == "https://labs.google/fx/tools/flow"
    assert sent["content-type"] == "application/json"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_patch_json_attaches_origin_and_referer_for_bff():
    page = _FakePage([200])
    page.request = _FakeRequestPatch([200])
    c = _client_with_page(page)
    await c._patch_json("https://labs.google/fx/api/trpc/project.renameProject", {"json": {}})
    sent = page.request.calls[0]["headers"]
    assert sent["origin"] == "https://labs.google"
    assert sent["referer"] == "https://labs.google/fx/tools/flow"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_json_still_logs_headers_for_bff_when_env_set(monkeypatch, install_log_capture):
    """The `GFLOW_CLI_LOG_REQUEST_HEADERS=1` diagnostic still works on this lane."""
    monkeypatch.setenv("GFLOW_CLI_LOG_REQUEST_HEADERS", "1")
    page = _FakePage([200])
    c = _client_with_page(page)
    await c._post_json(
        "https://labs.google/fx/api/trpc/project.createProject",
        {"json": {}},
        content_type="application/json",
    )
    logged = [e for e in install_log_capture.entries if e.get("event") == "request_headers"]
    assert logged, "request_headers event missing on the labs.google lane"
    assert logged[0]["headers"]["referer"] == "https://labs.google/fx/tools/flow"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_json_refetches_token_on_401_then_raises():
    page = _FakePage([401, 401])  # 401, re-fetch token, still 401
    c = _client_with_page(page)
    refetched = {"n": 0}

    async def fake_fetch():
        refetched["n"] += 1
        return ("ya29.NEW", 9_999_999_999.0)

    c._fetch_access_token = fake_fetch  # type: ignore[method-assign]
    with pytest.raises(AisandboxAuthError):
        await c._post_json(
            "https://aisandbox-pa.googleapis.com/v1/flow/projects/p/scenes",
            {"workflowIds": []},
        )
    assert refetched["n"] == 1  # re-fetched exactly once
    assert len(page.request.calls) == 2  # original + one retry
    # second attempt used the refreshed token
    assert page.request.calls[1]["headers"]["authorization"] == "Bearer ya29.NEW"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_patch_json_attaches_bearer_for_aisandbox():
    page = _FakePage([200])
    page.request = _FakeRequestPatch([200])
    c = _client_with_page(page)
    await c._patch_json(
        "https://aisandbox-pa.googleapis.com/v1/flowWorkflows/wf-1",
        {"workflow": {"name": "wf-1"}, "updateMask": "metadata.primaryMediaId"},
    )
    assert page.request.calls[0]["headers"]["authorization"] == "Bearer ya29.TOK"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_json_refetches_token_on_401_then_succeeds():
    page = _FakePage([401, 200])  # 401, re-fetch token, retry → 200
    c = _client_with_page(page)
    refetched = {"n": 0}

    async def fake_fetch():
        refetched["n"] += 1
        return ("ya29.NEW", 9_999_999_999.0)

    c._fetch_access_token = fake_fetch  # type: ignore[method-assign]
    result = await c._post_json(
        "https://aisandbox-pa.googleapis.com/v1/flow/projects/p/scenes",
        {"workflowIds": []},
    )
    assert result == {}  # 200 body parsed, no AisandboxAuthError
    assert refetched["n"] == 1  # re-fetched exactly once
    assert len(page.request.calls) == 2  # original 401 + successful retry
    assert page.request.calls[1]["headers"]["authorization"] == "Bearer ya29.NEW"


@pytest.mark.asyncio
async def test_request_headers_withholds_labs_origin_from_a_third_host():
    """An Origin must name the host actually being called.

    Cross-checked against notebooklm-py, which hit this on Google's upload
    lanes and documents it: "an Origin naming the other host fails Google's
    origin-bound auth checks." Keying the BFF headers on `not is_aisandbox`
    would be correct only while routes.py defines exactly two hosts — a third
    would silently inherit a labs.google Origin. Keyed on the URL instead.
    """
    client = _client_with_page(_FakePage([200]))

    headers = await client._request_headers(
        url="https://example.invalid/v1/thing",
        content_type="application/json",
        is_aisandbox=False,
    )

    assert "origin" not in headers
    assert "referer" not in headers
    assert headers["content-type"] == "application/json"
