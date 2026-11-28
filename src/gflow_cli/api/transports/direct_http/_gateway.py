# pyright: reportUnusedFunction=false
"""The batchexecute write path: one POST, sent once, with its fallback and reconcile rules."""

from __future__ import annotations

import secrets
from http.cookiejar import CookieJar, DefaultCookiePolicy
from pathlib import Path

import httpx

from gflow_cli.api import flow_wire
from gflow_cli.errors import NetworkError

_ORIGIN = "https://flow.google.com"
_RpcSpec = tuple[str, str, str, str]  # rpcid, reCAPTCHA action, noun, reconcile hint


class _DirectFallbackError(Exception):
    """Internal sentinel: nothing was accepted, so the UI transport may run instead."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _reconcile_error(detail: str, spec: _RpcSpec, *, status: int | None = None) -> NetworkError:
    """A failure after the POST was sent: never resent, the caller reconciles."""
    return NetworkError(
        detail=f"{detail}; not resent",
        status=status,
        route=f"batchexecute:{spec[0]}",
        next_action="reconcile",
        remediation_hint=spec[3],
    )


def _post_request(
    rpcid: str, project_id: str, tokens: flow_wire.Tokens, headers: dict[str, str], body: str
) -> httpx.Request:
    query = {
        "rpcids": rpcid,
        "source-path": f"/project/{project_id}",
        "bl": tokens.bl,
        "f.sid": tokens.fsid,
        "hl": "en",
        "_reqid": str(1000 + secrets.randbelow(9000)),
        "rt": "c",
    }
    return httpx.Request(
        "POST",
        f"{_ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute",
        params={k: v for k, v in query.items() if v is not None},
        content=body,
        headers={
            **headers,
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "x-same-domain": "1",
            "origin": _ORIGIN,
            "referer": f"{_ORIGIN}/project/{project_id}",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        },
    )


async def _send_once(
    client: httpx.AsyncClient, request: httpx.Request, profile_dir: Path, spec: _RpcSpec
) -> httpx.Response:
    """Write path: exactly one send.

    Nothing was accepted when the connection never opened, or when Flow redirected (3xx),
    refused the session (401) or the token (403): the UI transport may run instead.
    Anything else after the send may have been billed and is never resent. Every rejected
    reply drops the cached ``at`` so a stale one cannot stick in a long-lived process.
    """
    try:
        response = await client.send(request)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        raise _DirectFallbackError(f"connect:{type(exc).__name__}") from exc
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        cause = (str(exc).strip().splitlines() or [""])[0][:160]
        raise _reconcile_error(f"{type(exc).__name__} on an {spec[2]} ({cause})", spec) from exc
    status = response.status_code
    if status == 200:
        return response
    flow_wire.forget_tokens(profile_dir)
    if 300 <= status < 400 or status in (401, 403):
        raise _DirectFallbackError(f"http_{status}")
    raise _reconcile_error(f"HTTP {status} on an {spec[2]}", spec, status=status)


def _client(http_transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,  # nothing env-derived (proxies, netrc) reaches this client
        timeout=httpx.Timeout(120.0, connect=10.0),  # a generation takes 16-24 s
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
        transport=http_transport,  # None = httpx's default; a mock under test
    )
