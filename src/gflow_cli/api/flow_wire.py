"""Read the Flow credit balance straight from flow.google.com, with no browser (#1229).

The profile's saved cookies (``auth/cookies.read_cookies_for``) go on one GET of the app
shell, which carries the page's ``at`` token (``SNlM0e``), session id (``FdrFJe``) and
build label (``cfb2h``); then one batchexecute POST of ``nzlxg`` returns the balance,
decoded by ``CreditsInfo.from_nzlxg``. Spikes: 2026-11-10 cookie jar (#1234), #1192, #1221.

A failure raises :class:`WireReadUnavailable` carrying only an enum reason, and the caller
(``services/credits.py``) reads through the browser instead. The exception is back-pressure:
HTTP 429 and 5xx raise the page path's own ``RateLimitError`` / ``NetworkError`` and turn the
wire off for the process, because a page load would send the same RPCs to a throttled
account. Nothing here retries a 4xx, follows a redirect, keeps a cookie Flow sets, or names
anything but ``nzlxg``.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from enum import StrEnum
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx
import structlog

from gflow_cli.api._retry import parse_retry_after
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors
from gflow_cli.auth.cookies import CookieStoreUnreadable, read_cookies_for
from gflow_cli.browser_manager import profile_last_version
from gflow_cli.errors import NetworkError, RateLimitError

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger(__name__)

_ORIGIN = "https://flow.google.com"
_BATCHEXECUTE = f"{_ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute"
_ALLOWED_RPCIDS = frozenset({"nzlxg"})
_ROUTE = "credits_wire"
_THROTTLED_HINT = "Flow is throttling this account; wait a minute, then read the balance again."
_ER_FRAME = re.compile(r'\["er"((?:,(?:null|-?\d+|"[^"]*"))*)')
_CONNECT_S = 3.0
_TOTAL_S = 5.0
_UA_PLATFORM = {
    "win32": "Windows NT 10.0; Win64; x64",
    "darwin": "Macintosh; Intel Mac OS X 10_15_7",
}


class WireFallbackReason(StrEnum):
    """Why the direct read gave way to the browser. Logged as-is; never carries detail."""

    SIGNED_OUT_SHELL = "signed_out_shell"
    HTTP_401 = "http_401"
    HTTP_400 = "http_400"
    HTTP_ERROR = "http_error"
    UNPARSEABLE = "unparseable"
    STORE_UNREADABLE = "store_unreadable"
    NETWORK = "network"
    REDIRECT = "redirect"
    DISABLED = "disabled"
    UNEXPECTED = "unexpected"


class WireReadUnavailable(Exception):  # noqa: N818 - a signal to fall back, not an error
    """The direct read cannot answer; the reason is all it carries (no body, no str(exc))."""

    def __init__(self, reason: WireFallbackReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True)
class _Tokens:
    at: str
    fsid: str | None
    bl: str | None


# Per process: the shell's tokens per profile, and a disable once Flow said "signed out".
_tokens: dict[Path, _Tokens] = {}
_disabled: WireFallbackReason | None = None
_lock: asyncio.Lock | None = None
_reqid = random.randint(1000, 9999)  # noqa: S311 - a request counter, not a secret


def _user_agent(profile_dir: Path) -> str | None:
    """The UA the profile's own Chrome sends (reduced form: major version only)."""
    version = profile_last_version(profile_dir)
    major = version.split(".", 1)[0] if version else ""
    if not major.isdigit():
        return None
    platform = _UA_PLATFORM.get(sys.platform, "X11; Linux x86_64")
    return (
        f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{major}.0.0.0 Safari/537.36"
    )


def _wiz(text: str, key: str) -> str | None:
    match = re.search(rf'"{key}":"([^"]*)"', text)
    return match.group(1) if match else None


def _error_codes(text: str) -> list[int]:
    """Numeric status codes of the reply's error envelopes (null-payload frames, `er` rows)."""
    codes = [error.code for error in rpc_errors(text) if error.code is not None]
    for match in _ER_FRAME.finditer(text):
        codes += [int(v) for v in match.group(1).split(",") if v.lstrip("-").isdigit()][:1]
    return codes


def _fail(reason: WireFallbackReason, *, disable: bool = False) -> WireReadUnavailable:
    global _disabled
    if disable:
        _disabled = reason
    return WireReadUnavailable(reason)


def _check(response: httpx.Response, profile_dir: Path) -> None:
    global _disabled
    if names := sorted(
        {h.split("=", 1)[0].strip() for h in response.headers.get_list("set-cookie")}
    ):
        log.debug("credits.wire_set_cookie_dropped", names=names)
    status = response.status_code
    if 300 <= status < 400:
        raise _fail(WireFallbackReason.REDIRECT)
    if status == 401:
        raise _fail(WireFallbackReason.HTTP_401, disable=True)
    if status == 400:
        _tokens.pop(profile_dir, None)  # a stale `at` is fetched again next time, never retried now
        raise _fail(WireFallbackReason.HTTP_400)
    # Back-pressure is not a fallback: a page load sends the same RPCs to a throttled
    # account. Surface the page path's own typed errors and stay off for the process.
    if status == 429 or status >= 500:
        _disabled = WireFallbackReason.HTTP_ERROR
        detail = f"HTTP {status}"
        if status == 429:
            raise RateLimitError(
                detail,
                status=status,
                route=_ROUTE,
                retry_after=parse_retry_after(response),
                remediation_hint=_THROTTLED_HINT,
            )
        raise NetworkError(detail, status=status, route=_ROUTE)
    if status != 200:
        raise _fail(WireFallbackReason.HTTP_ERROR)


async def _send(client: httpx.AsyncClient, request: httpx.Request) -> httpx.Response:
    try:
        return await client.send(request)
    except httpx.ConnectError:  # one retry, connect errors only (nothing reached Flow)
        return await client.send(request)


def _rpc_request(rpcid: str, tokens: _Tokens, headers: dict[str, str]) -> httpx.Request:
    if rpcid not in _ALLOWED_RPCIDS:
        msg = f"rpcid {rpcid!r} is not on the wire allow-list"
        raise ValueError(msg)
    global _reqid
    _reqid += 100_000
    query = {"rpcids": rpcid, "source-path": "/", "bl": tokens.bl, "f.sid": tokens.fsid}
    query |= {"hl": "en", "_reqid": str(_reqid), "rt": "c"}
    body = urlencode({"f.req": json.dumps([[[rpcid, "[]", None, "generic"]]]), "at": tokens.at})
    return httpx.Request(
        "POST",
        f"{_BATCHEXECUTE}?{urlencode({k: v for k, v in query.items() if v is not None})}",
        content=body,
        headers={
            **headers,
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "x-same-domain": "1",
            "origin": _ORIGIN,
            "referer": f"{_ORIGIN}/",
        },
    )


async def _shell_tokens(
    client: httpx.AsyncClient, profile_dir: Path, headers: dict[str, str]
) -> _Tokens:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:  # single flight: concurrent reads share one shell GET
        if _disabled is not None:  # a read that held the lock just turned the wire off
            raise WireReadUnavailable(WireFallbackReason.DISABLED)
        if (cached := _tokens.get(profile_dir)) is not None:
            return cached
        nav = {
            **headers,
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "upgrade-insecure-requests": "1",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
        }
        response = await _send(client, client.build_request("GET", f"{_ORIGIN}/", headers=nav))
        _check(response, profile_dir)
        if not (at := _wiz(response.text, "SNlM0e")):
            raise _fail(WireFallbackReason.SIGNED_OUT_SHELL, disable=True)
        tokens = _Tokens(at, _wiz(response.text, "FdrFJe"), _wiz(response.text, "cfb2h"))
        _tokens[profile_dir] = tokens
        return tokens


async def read_credits(profile_dir: Path) -> CreditsInfo:
    """The balance over the wire, or :class:`WireReadUnavailable`. Never starts a browser."""
    if _disabled is not None:
        raise WireReadUnavailable(WireFallbackReason.DISABLED)
    started = time.perf_counter()
    try:
        cookies = await asyncio.to_thread(read_cookies_for, profile_dir)
    except CookieStoreUnreadable:
        raise _fail(WireFallbackReason.STORE_UNREADABLE) from None
    if (ua := _user_agent(profile_dir)) is None:
        raise _fail(WireFallbackReason.STORE_UNREADABLE)
    headers = {
        "user-agent": ua,
        "accept-language": "en-US,en;q=0.9",
    }
    if cookies:  # a browser with no cookies sends no Cookie header at all
        headers["cookie"] = "; ".join(f"{name}={value}" for name, value in cookies)
    read_at = time.perf_counter()
    try:
        async with (
            asyncio.timeout(_TOTAL_S),
            httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(_TOTAL_S, connect=_CONNECT_S),
                # Reject-all jar: a cookie Flow sets is dropped, never sent back or stored.
                cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
            ) as client,
        ):
            tokens = await _shell_tokens(client, profile_dir, headers)
            got_at = time.perf_counter()
            response = await _send(client, _rpc_request("nzlxg", tokens, headers))
            _check(response, profile_dir)
    except (TimeoutError, httpx.TransportError):
        raise _fail(WireFallbackReason.NETWORK) from None
    posted_at = time.perf_counter()
    frames = [payload for rpcid, payload in parse_frames(response.text) if rpcid == "nzlxg"]
    try:
        info = CreditsInfo.from_nzlxg(frames[0] if frames else None)
    except ValueError:
        if codes := _error_codes(response.text):
            # Only the numeric status: a real rate-limit code shows up here before we act on it.
            log.info("credits.wire_rpc_error", codes=codes)
        raise _fail(WireFallbackReason.UNPARSEABLE) from None
    done = time.perf_counter()
    log.info(
        "credits.wire_timing",
        cookies=len(cookies),
        cookies_ms=round((read_at - started) * 1000),
        get_ms=round((got_at - read_at) * 1000),
        post_ms=round((posted_at - got_at) * 1000),
        parse_ms=round((done - posted_at) * 1000),
    )
    return info
