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


def _client_with_page(page, sapisid="FAKE_SAPISID"):
    c = FlowApiClient.__new__(FlowApiClient)
    c._sapisid = sapisid

    async def checkout():
        return page

    c._checkout_page = checkout  # type: ignore[method-assign]
    c._checkin_page = lambda p: None  # type: ignore[method-assign]
    return c


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_json_attaches_sapisidhash_for_aisandbox(monkeypatch):
    monkeypatch.setattr("gflow_cli.api.client.time.time", lambda: 1700000000.0)
    page = _FakePage([200])
    c = _client_with_page(page)
    await c._post_json(
        "https://aisandbox-pa.googleapis.com/v1/flow/projects/p/scenes",
        {"workflowIds": []},
    )
    sent = page.request.calls[0]["headers"]
    assert sent["authorization"].startswith("SAPISIDHASH 1700000000_")


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
async def test_post_json_refreshes_sapisid_on_401_then_raises(monkeypatch):
    monkeypatch.setattr("gflow_cli.api.client.time.time", lambda: 1700000000.0)
    page = _FakePage([401, 401])  # 401, refresh, still 401
    c = _client_with_page(page)
    refreshed = {"n": 0}

    async def fake_read():
        refreshed["n"] += 1
        return "ROTATED_SAPISID"

    c._read_sapisid_from_context = fake_read  # type: ignore[method-assign]
    with pytest.raises(AisandboxAuthError):
        await c._post_json(
            "https://aisandbox-pa.googleapis.com/v1/flow/projects/p/scenes",
            {"workflowIds": []},
        )
    assert refreshed["n"] == 1  # re-read exactly once
    assert len(page.request.calls) == 2  # original + one retry


@pytest.mark.integration
@pytest.mark.asyncio
async def test_patch_json_attaches_sapisidhash_for_aisandbox(monkeypatch):
    monkeypatch.setattr("gflow_cli.api.client.time.time", lambda: 1700000000.0)
    page = _FakePage([200])
    page.request = _FakeRequestPatch([200])
    c = _client_with_page(page)
    await c._patch_json(
        "https://aisandbox-pa.googleapis.com/v1/flowWorkflows/wf-1",
        {"workflow": {"name": "wf-1"}, "updateMask": "metadata.primaryMediaId"},
    )
    assert page.request.calls[0]["headers"]["authorization"].startswith("SAPISIDHASH ")
