"""Every `AisandboxAuthError` says what was actually rejected (#803).

The class default asserted a SAPISID failure, and three of the five raise sites
took it unchanged — on routes where SAPISID is never read. #795 fixed the two
sites in `api/credits.py`; these are the three that were left, reachable from
`createScene`, `commitWorkflow`, `createEntity`, `projectInitialData`,
`upsampleImage` and every other aisandbox route through
`_run_with_aisandbox_retry`.

The bug is not cosmetic: the remediation tells the user to re-run `gflow auth
login`, which on an account Google serves from flow.google.com can roll the
profile's Chrome-strategy marker back and start the #791 re-login spiral. So a
wrong hint here costs the user their working session.
"""

from __future__ import annotations

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import AisandboxAuthError


class _FakeResp:
    def __init__(self, status: int, body: str = "{}") -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body


class _FakeSessionRequest:
    """Answers the `/fx/api/auth/session` GET the token fetch makes."""

    def __init__(self, status: int, body: str) -> None:
        self._resp = _FakeResp(status, body)

    async def get(self, url: str) -> _FakeResp:
        return self._resp


class _FakeContext:
    def __init__(self, status: int, body: str) -> None:
        self.request = _FakeSessionRequest(status, body)


def _client_with_session(status: int, body: str) -> FlowApiClient:
    c = FlowApiClient.__new__(FlowApiClient)
    c._context = _FakeContext(status, body)
    return c


def _no_sapisid_claim(hint: str) -> bool:
    """The hint must not blame a credential this route never reads."""
    return "SAPISID" not in hint


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_non_json_session_reply_is_not_reported_as_a_stale_cookie() -> None:
    """An interstitial or a redirect body is not an expired cookie, and
    re-authenticating cannot change a non-JSON reply."""
    client = _client_with_session(200, "<!doctype html><html>sign in</html>")

    with pytest.raises(AisandboxAuthError) as excinfo:
        await client._fetch_access_token()

    hint = excinfo.value.remediation_hint
    assert _no_sapisid_claim(hint), hint
    assert "non-JSON" in hint
    assert "re-authenticating will not" in hint.lower() or "will not change" in hint.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_session_without_a_token_names_the_host_not_the_cookie() -> None:
    """labs answering 200 with no `access_token` is the migrated-account shape
    (#795). aisandbox-pa has not been contacted, so nothing has rejected
    anything — and SAPISID is what let the session probe answer at all."""
    client = _client_with_session(200, '{"user": {"email": "x@example.com"}}')

    with pytest.raises(AisandboxAuthError) as excinfo:
        await client._fetch_access_token()

    hint = excinfo.value.remediation_hint
    assert _no_sapisid_claim(hint), hint
    assert "flow.google.com" in hint
    assert "#803" in hint or "#795" in hint


@pytest.mark.unit
def test_the_class_default_no_longer_asserts_a_sapisid_failure() -> None:
    """Any site that does not pass a hint gets the default, so the default has
    to be true on every route that can reach it — and none of them reads
    SAPISID."""
    err = AisandboxAuthError("aisandbox-pa returned 401")

    assert _no_sapisid_claim(err.remediation_hint), err.remediation_hint
    assert "token" in err.remediation_hint.lower()


@pytest.mark.unit
def test_a_401_after_refresh_names_the_route_that_was_refused() -> None:
    """`_run_with_aisandbox_retry` already knows the route; naming it is what
    tells the user which capability is refused rather than implying their whole
    sign-in is broken."""
    err = AisandboxAuthError(
        detail="aisandbox-pa returned 401 after token refresh",
        status=401,
        route="createScene",
        remediation_hint=(
            "Flow's labs.google session issued an API token and aisandbox-pa rejected "
            "it on route createScene. Your Google sign-in is not the problem — minting "
            "that token is what proves it works. See issue #803."
        ),
    )

    assert "createScene" in err.remediation_hint
    assert _no_sapisid_claim(err.remediation_hint)
