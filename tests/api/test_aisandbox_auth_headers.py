import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import AuthMissingError


def _make_client() -> FlowApiClient:
    # Construct without entering the async context (no browser launched).
    return FlowApiClient.__new__(FlowApiClient)


@pytest.mark.unit
def test_is_aisandbox_url_discriminates_host():
    c = _make_client()
    assert c._is_aisandbox_url("https://aisandbox-pa.googleapis.com/v1/flow/x")
    assert not c._is_aisandbox_url(
        "https://labs.google/fx/api/trpc/project.createProject"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_aisandbox_auth_headers_builds_authorization(monkeypatch):
    c = _make_client()
    c._sapisid = None

    async def fake_read():
        return "FAKE_SAPISID"

    monkeypatch.setattr(c, "_read_sapisid_from_context", fake_read)
    monkeypatch.setattr("gflow_cli.api.client.time.time", lambda: 1700000000.0)

    headers = await c._aisandbox_auth_headers()
    assert headers["authorization"].startswith("SAPISIDHASH 1700000000_")
    assert headers["origin"] == "https://labs.google"
    # SAPISID was cached
    assert c._sapisid == "FAKE_SAPISID"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_aisandbox_auth_headers_raises_when_sapisid_absent(monkeypatch):
    c = _make_client()
    c._sapisid = None

    async def fake_read():
        raise AuthMissingError("no SAPISID")

    monkeypatch.setattr(c, "_read_sapisid_from_context", fake_read)
    with pytest.raises(AuthMissingError):
        await c._aisandbox_auth_headers()
