"""Guard: SAPISID and the computed SAPISIDHASH must never reach logs.

docs/SECURITY.md — "No cookies, no tokens, no API keys" in logs. The auth
header is built per aisandbox request; this asserts the existing header
redaction covers it even with GFLOW_CLI_LOG_REQUEST_HEADERS=1.
"""

from __future__ import annotations

import pytest
import structlog

from gflow_cli.api.client import FlowApiClient


class _FakeResp:
    def __init__(self, status: int, body: str = "{}") -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body


class _FakeRequest:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def post(self, url, *, data, headers):
        self.calls.append({"url": url, "headers": dict(headers)})
        return _FakeResp(200)


class _FakePage:
    def __init__(self) -> None:
        self.request = _FakeRequest()
        self.context = None


def _client_with_page(page, sapisid: str) -> FlowApiClient:
    c = FlowApiClient.__new__(FlowApiClient)
    c._sapisid = sapisid

    async def checkout():
        return page

    c._checkout_page = checkout  # type: ignore[method-assign]
    c._checkin_page = lambda p: None  # type: ignore[method-assign]
    return c


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sapisid_and_sapisidhash_never_logged(
    monkeypatch,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    monkeypatch.setenv("GFLOW_CLI_LOG_REQUEST_HEADERS", "1")
    monkeypatch.setattr("gflow_cli.api.client.time.time", lambda: 1700000000.0)
    secret = "SUPER_SECRET_SAPISID"
    page = _FakePage()
    c = _client_with_page(page, sapisid=secret)

    await c._post_json(
        "https://aisandbox-pa.googleapis.com/v1/flow/projects/p/scenes",
        {"workflowIds": []},
    )

    # The request_headers event WAS emitted (env flag on)...
    assert any(e.get("event") == "request_headers" for e in install_log_capture.entries)
    # ...but neither the raw cookie nor the computed hash may appear anywhere.
    blob = repr(install_log_capture.entries)
    assert secret not in blob
    assert "SAPISIDHASH 1700000000_" not in blob  # unredacted header form
