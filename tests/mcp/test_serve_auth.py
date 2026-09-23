# SPDX-License-Identifier: MIT
"""Request-level auth and DNS-rebinding protection for ``gflow serve``.

``Settings.daemon_token`` has existed since the daemon was sketched, and
``gflow serve`` checks at STARTUP that one is set when ``--host`` is not
loopback (``cli.py``) — but the token was never handed to the transport.
``run_http`` / ``run_sse`` called the SDK's ``run_*_async`` helpers with no
auth at all, so the startup check gated *binding*, never a single request: any
process that could reach the port got the full tool surface, including the
credit-spending generate tools.

``transport_security`` was left ``None`` as well. Measured against this SDK
(``streamable_http_app`` auto-fills it), that is NOT the hole it looks like for
the default bind — a ``None`` on ``127.0.0.1``/``localhost``/``::1`` is filled
in with protection ON (bad Host 421, bad Origin 403), and only a non-loopback
bind (``0.0.0.0``, a LAN address) leaves it OFF, i.e. 400 straight into the
transport. The rebinding tests below therefore CHARACTERISE the loopback
default rather than drive a change; see the note above each one.

These tests pin the contract of the ``build_app`` seam both entry points must
route through, so the assertions cover the real wiring rather than a component
in isolation:

* no token configured   → the transport is open exactly as before (no 401)
* token configured      → every request needs ``Authorization: Bearer <token>``
* both transports       → Streamable HTTP ``/mcp``, SSE ``/sse`` + ``/messages/``
* constant-time compare → the check goes through ``hmac.compare_digest``
* loopback bind         → DNS-rebinding protection ON (421 Host / 403 Origin)
* ordering              → auth is OUTERMOST; an unauthenticated caller must not
  learn anything about transport-security state (401 wins over 421)

Two mechanical notes for whoever edits this file:

1. The SDK builds a fresh ``StreamableHTTPSessionManager`` per
   ``streamable_http_app()`` call and its ``.run()`` may be entered ONCE.
   Every test therefore builds its own app — never share one across tests or
   the second lifespan raises ``RuntimeError``.
2. ``TestClient`` is driven with an explicit loopback ``base_url`` so the
   generated ``Host`` header is ``127.0.0.1:8000``. The default
   (``testserver``) would be rejected by the rebinding guard and mask the
   status code under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, Literal

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.types import Message, Scope

from gflow_cli.mcp.server import HTTP_PATH

HOST = "127.0.0.1"
BASE_URL = "http://127.0.0.1:8000"
SSE_PATH = "/sse"
MESSAGE_PATH = "/messages/"

#: Not a credential — a fixed literal so the constant-time spy can recognise it.
TOKEN = "test-daemon-token"

#: Streamable HTTP demands both media types on Accept; without them the
#: transport answers 406 and the "not 401" assertions would pass for the wrong
#: reason.
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

#: ``ping`` is answered with 400 "Missing session ID" whatever the auth outcome,
#: so it can only ever support a ``!= 401`` claim. ``initialize`` is the one
#: method that needs no prior session: measured against this SDK it returns 200,
#: a real ``result`` body and an ``mcp-session-id`` header — a POSITIVE success,
#: which is what lets the "correct token" tests fail a middleware that answers
#: 500 or 403 instead of passing the request through.
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "gflow-serve-auth-tests", "version": "0"},
    },
}


def build(
    *,
    transport: Literal["http", "sse"],
    host: str = HOST,
    token: str | None = None,
) -> Starlette:
    """Call the ``build_app`` seam under test.

    The import lives inside the call on purpose: while ``build_app`` does not
    exist, every test reds individually with a pointed ``ImportError`` instead
    of collapsing the whole module into one collection error.
    """
    from gflow_cli.mcp.server import build_app

    return build_app(transport=transport, host=host, token=token)


class _HeadResponseSentError(Exception):
    """Sentinel raised out of ``send`` once the response headers are known."""


async def response_head(
    app: Starlette,
    method: str,
    path: str,
    *,
    authorization: str | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, str]]:
    """Drive ``app`` as a raw ASGI callable and return ``(status, headers)``.

    Needed for ``GET /sse`` only. An SSE stream that is NOT rejected never
    ends, and nothing in ``TestClient`` can bound it — the in-process
    transport ignores httpx's ``timeout=``, and ``client.stream()`` blocks on
    the portal just the same (both verified against this SDK). A suite that
    hangs is strictly worse than one that fails.

    The status line is the whole answer here and it arrives before the body, so
    ``send`` records ``http.response.start`` and immediately raises
    :class:`_HeadResponseSentError` — the stream is torn down at the one instant its
    headers are complete and the body is never read. That is what makes the
    ACCEPTED case testable too, not just the rejected one: an endless
    ``text/event-stream`` becomes a plain ``(200, headers)`` return.

    ``except*`` because the SDK's stream response runs inside an anyio task
    group, which re-raises the sentinel wrapped in an ``ExceptionGroup``; a
    401 raises it bare. Both are caught, and a real hang still surfaces as the
    ``asyncio.wait_for`` ``TimeoutError``.
    """
    start: dict[str, Any] = {}
    body_sent = False

    async def receive() -> Message:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        # Block instead of looping: a streaming endpoint polls for
        # http.disconnect, and returning http.request forever would spin.
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            start.update(message)
            raise _HeadResponseSentError

    raw_headers = [(b"host", b"127.0.0.1:8000")]
    if authorization is not None:
        raw_headers.append((b"authorization", authorization.encode()))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=timeout)
    except* _HeadResponseSentError:
        pass
    headers = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
    return int(start["status"]), headers


@pytest.fixture()
def fresh_settings() -> Iterator[None]:
    """Drop the cached ``Settings`` singleton either side of a test.

    The wiring tests set ``GFLOW_CLI_DAEMON_TOKEN``; ``monkeypatch`` unsets the
    env var again but cannot evict the ``lru_cache`` that already read it.
    """
    from gflow_cli.config import reset_settings

    reset_settings()
    yield
    reset_settings()


class _StopBeforeServeError(Exception):
    """Raised by the ``build_app`` spy so no entry point ever binds a socket."""


# ---------------------------------------------------------------------------
# 1. No token configured — the transport stays exactly as open as it was
# ---------------------------------------------------------------------------


def test_without_a_token_no_request_is_challenged() -> None:
    """``token=None`` must not add auth: this is the default local posture and
    a regression here would break every existing stdio/HTTP client."""
    app = build(transport="http")
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post(HTTP_PATH, headers=MCP_HEADERS, json=PING)
    # Observed: 400 (the transport's own "missing session id"), never 401.
    assert resp.status_code != 401


# ---------------------------------------------------------------------------
# 2. Token configured — every request carries Bearer <token>
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "authorization"),
    [
        ("absent", None),
        ("wrong-token", "Bearer not-the-token"),
        ("non-bearer-scheme", f"Basic {TOKEN}"),
        ("bare-token-no-scheme", TOKEN),
    ],
)
def test_a_request_without_the_token_is_rejected(label: str, authorization: str | None) -> None:
    """Missing, wrong, and non-Bearer credentials all get 401 — and the 401
    must name the scheme, so a client knows what to present."""
    app = build(transport="http", token=TOKEN)
    headers = dict(MCP_HEADERS)
    if authorization is not None:
        headers["Authorization"] = authorization
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post(HTTP_PATH, headers=headers, json=PING)
    assert resp.status_code == 401, label
    assert resp.headers.get("www-authenticate", "").startswith("Bearer"), label


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_the_correct_token_reaches_the_transport(scheme: str) -> None:
    """RFC 9110 makes the auth scheme case-insensitive; a client that sends
    ``bearer`` is not an attacker and must not be locked out."""
    app = build(transport="http", token=TOKEN)
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post(
            HTTP_PATH,
            headers={**MCP_HEADERS, "Authorization": f"{scheme} {TOKEN}"},
            json=INITIALIZE,
        )
    # A POSITIVE success, not merely "not 401": the request reached the
    # transport AND the transport answered it. ``!= 401`` on a ``ping`` (which
    # 400s whatever happens) would be satisfied by a middleware that 500s.
    assert resp.status_code == 200, (scheme, resp.status_code, resp.text[:200])
    assert resp.headers.get("mcp-session-id"), scheme
    assert '"result"' in resp.text, scheme


# ---------------------------------------------------------------------------
# 3. Both transports — SSE is not a back door
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_stream_path_requires_the_token() -> None:
    """``--transport sse`` is deprecated but still shipped, so the stream path
    must be covered too — it is the one that hands out server events.

    Driven through :func:`response_head` rather than ``TestClient``: without
    the 401 this request opens an endless stream, and only a cancellation
    boundary turns that into a failure instead of a hung suite.
    """
    app = build(transport="sse", token=TOKEN)
    status, headers = await response_head(app, "GET", SSE_PATH)
    assert status == 401
    assert headers.get("www-authenticate", "").startswith("Bearer")


@pytest.mark.asyncio
async def test_sse_stream_path_admits_the_correct_token() -> None:
    """The correct token is accepted on BOTH transports, not just on ``/mcp``.

    This was previously recorded as untestable — an accepted SSE stream never
    ends. It is testable: :func:`response_head` tears the stream down the
    instant ``http.response.start`` arrives, which is after the status and
    content type are final and before a single byte of body is read.

    Without this, a middleware that 401s every ``GET /sse`` while passing
    ``/messages/`` through would satisfy the whole SSE section — the deprecated
    transport would be dead and nothing red.
    """
    app = build(transport="sse", token=TOKEN)
    status, headers = await response_head(app, "GET", SSE_PATH, authorization=f"Bearer {TOKEN}")
    assert status == 200, (status, headers)
    assert headers.get("content-type", "").startswith("text/event-stream"), headers


def test_sse_message_path_requires_the_token() -> None:
    """The other half of the SSE surface: the client's write channel."""
    app = build(transport="sse", token=TOKEN)
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post(MESSAGE_PATH, json=PING)
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate", "").startswith("Bearer")


def test_sse_message_path_admits_the_correct_token() -> None:
    """Guards the sibling failure of the two tests above: a middleware that
    401s unconditionally would satisfy them while breaking the transport.

    The stream half of the same claim is
    :func:`test_sse_stream_path_admits_the_correct_token`.

    Unlike the Streamable HTTP path there is no payload that yields a 200 here:
    measured, ``/messages/`` answers 400 ``session_id is required`` to BOTH
    ``ping`` and ``initialize``, because the SSE write channel is only reachable
    with a session minted by an open ``GET /sse``. So the positive evidence is
    the transport's own 400 and its own body — which a middleware answering 401,
    403 or 500 cannot produce.
    """
    app = build(transport="sse", token=TOKEN)
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post(MESSAGE_PATH, headers={"Authorization": f"Bearer {TOKEN}"}, json=PING)
    assert resp.status_code == 400, (resp.status_code, resp.text[:200])
    assert "session_id" in resp.text, resp.text[:200]


# ---------------------------------------------------------------------------
# 4. Constant-time comparison
# ---------------------------------------------------------------------------


def test_token_comparison_goes_through_hmac_compare_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``==`` on the presented token leaks its length and prefix to a timing
    attacker. This is a wiring assertion by design: it patches the real ``hmac``
    module, so the implementation must reach the function through the module
    (``import hmac``), which is also how it stays patchable.

    Asserting the TOKEN appears in the recorded arguments — not merely that
    something called ``compare_digest`` — keeps an unrelated SDK call from
    turning this green.
    """
    import hmac

    calls: list[tuple[object, object]] = []
    real = hmac.compare_digest

    def spy(a: object, b: object) -> bool:
        calls.append((a, b))
        return real(a, b)  # type: ignore[arg-type]

    monkeypatch.setattr(hmac, "compare_digest", spy)

    app = build(transport="http", token=TOKEN)
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post(
            HTTP_PATH,
            headers={**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"},
            json=INITIALIZE,
        )

    # 200, not ``!= 401``: the spy must not be able to pass by wrapping a
    # middleware that rejects the request some other way.
    assert resp.status_code == 200, (resp.status_code, resp.text[:200])

    def seen(value: object) -> str:
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)

    assert any(TOKEN in (seen(a), seen(b)) for a, b in calls), calls


# ---------------------------------------------------------------------------
# 5. DNS-rebinding protection on a loopback bind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "value", "expected"),
    [
        ("Host", "evil.example.com", 421),
        ("Origin", "http://evil.example.com", 403),
    ],
)
def test_dns_rebinding_protection_is_on_for_a_loopback_bind(
    header: str, value: str, expected: int
) -> None:
    """A page on ``evil.example.com`` must not be able to drive the local
    daemon through the user's browser. 421 is the SDK's bad-Host answer, 403
    its bad-Origin answer.

    CHARACTERISATION, not a fix-detector: measured, the SDK already fills a
    ``None`` ``transport_security`` with protection ON for a loopback host, so
    this passes with or without the change. It is here to stop a hand-rolled
    ``TransportSecuritySettings`` from silently *weakening* the default — and
    it is scoped to loopback because that is the only bind where the SDK grants
    it. A ``0.0.0.0`` bind measures 400 for both headers (protection OFF), and
    closing THAT is a contract decision nobody has taken yet.
    """
    app = build(transport="http")
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post(HTTP_PATH, headers={**MCP_HEADERS, header: value}, json=PING)
    assert resp.status_code == expected


@pytest.mark.parametrize(
    ("headers", "rejected"),
    [
        ({"Host": "127.0.0.1:8000"}, 421),
        ({}, 403),
    ],
    ids=["loopback-host", "absent-origin"],
)
def test_legitimate_local_requests_are_not_rebinding_rejected(
    headers: dict[str, str], rejected: int
) -> None:
    """The other half of the guard: a real local client (and an ``Origin``-less
    request, which always passes per the spec) must still get through, or the
    protection would just be an outage. Also characterisation — it passes
    either way, and exists to catch an over-tight allow-list."""
    app = build(transport="http")
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post(HTTP_PATH, headers={**MCP_HEADERS, **headers}, json=PING)
    # Observed: 400 for both — inside the transport, not blocked at the edge.
    assert resp.status_code != rejected


# ---------------------------------------------------------------------------
# 6. Ordering — auth is the outermost layer
# ---------------------------------------------------------------------------


def test_auth_answers_before_transport_security() -> None:
    """An unauthenticated caller must learn nothing about the rebinding
    configuration: a 421 here would confirm the Host allow-list to someone who
    has not authenticated. Starlette inserts each ``add_middleware`` at index
    0, so the auth layer has to be added LAST.
    """
    app = build(transport="http", token=TOKEN)
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post(
            HTTP_PATH, headers={**MCP_HEADERS, "Host": "evil.example.com"}, json=PING
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 7. CLI wiring — the configured token actually reaches build_app
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry_point", "transport"),
    [("run_http", "http"), ("run_sse", "sse")],
)
@pytest.mark.asyncio
async def test_entry_points_pass_the_configured_token_to_build_app(
    entry_point: str,
    transport: str,
    monkeypatch: pytest.MonkeyPatch,
    fresh_settings: None,
) -> None:
    """The middleware is worthless if ``gflow serve`` never hands it the token.

    No socket is bound: the spy raises before any serving starts, and the
    module's ``server`` singleton is replaced so an implementation that ignored
    ``build_app`` entirely would fail fast on the missing raise rather than
    listen on a port.

    ``daemon_token`` is a ``SecretStr``; asserting the plain string pins that
    the entry point unwraps it (``hmac.compare_digest`` cannot consume a
    ``SecretStr``).
    """
    from unittest.mock import AsyncMock, MagicMock

    from gflow_cli.mcp import server as server_mod

    monkeypatch.setenv("GFLOW_CLI_DAEMON_TOKEN", TOKEN)

    fake_server = MagicMock()
    fake_server.run_streamable_http_async = AsyncMock()
    fake_server.run_sse_async = AsyncMock()
    monkeypatch.setattr(server_mod, "server", fake_server)
    monkeypatch.setattr(server_mod, "_configure_utf8_pipes", lambda: None)

    calls: list[dict[str, object]] = []

    def spy(**kwargs: object) -> Starlette:
        calls.append(kwargs)
        raise _StopBeforeServeError

    # Fails loudly today: the module has no `build_app` to replace.
    monkeypatch.setattr(server_mod, "build_app", spy)

    run = getattr(server_mod, entry_point)
    with pytest.raises(_StopBeforeServeError):
        await run(host=HOST, port=9999)

    assert len(calls) == 1
    assert calls[0]["token"] == TOKEN
    assert calls[0]["transport"] == transport
    assert calls[0]["host"] == HOST
