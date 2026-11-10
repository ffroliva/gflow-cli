"""Read the Flow credit balance straight from flow.google.com, with no browser (#1229).

The profile's saved cookies (``auth/cookies.read_cookies_for``) go on one GET of the app
shell, which carries the page's ``at`` token (``SNlM0e``), session id (``FdrFJe``) and
build label (``cfb2h``); then one batchexecute POST of ``nzlxg`` returns the balance,
decoded by ``CreditsInfo.from_nzlxg``. Spikes: 2026-11-10 cookie jar (#1234), #1192, #1221.

A failure raises :class:`WireReadUnavailable` carrying only an enum reason, and the caller
(``services/credits.py``) reads through the browser instead. Back-pressure is the exception:
HTTP 429 and 503 raise the page path's own ``RateLimitError`` / ``NetworkError`` and open a
per-profile window (Retry-After, else 60 s) in which every read of that profile re-raises
with nothing sent, because a page load would send the same RPCs to a throttled account.
Nothing here retries a 4xx, follows a redirect, keeps a cookie Flow sets, or names anything
but ``nzlxg``. All state is per profile: one account's failure never affects another.
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
from gflow_cli.auth.cookies import CookieStoreUnreadable, read_cookies_for, require_in_home
from gflow_cli.browser_manager import profile_last_version
from gflow_cli.config import get_settings
from gflow_cli.errors import NetworkError, RateLimitError

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger(__name__)

_ORIGIN = "https://flow.google.com"
_BATCHEXECUTE = f"{_ORIGIN}/_/AiSandboxAngularFrontend/data/batchexecute"
_ALLOWED_RPCIDS = frozenset({"nzlxg"})
_ROUTE = "credits_wire"
_THROTTLED_HINT = "Flow is throttling this account; wait a minute, then read the balance again."
_THROTTLE_DEFAULT_S = 60.0
_BUILD = re.compile(r"boq_[A-Za-z0-9._-]{1,80}")  # cfb2h is scraped from remote HTML
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
    NO_CHROME_VERSION = "no_chrome_version"
    PROXY_CONFIGURED = "proxy_configured"
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


# Per process, per profile: the shell's tokens, profiles Flow said are signed out, throttle
# windows (monotonic deadline, the status that opened it), and one lock each.
_tokens: dict[Path, _Tokens] = {}
_signed_out: set[Path] = set()
_throttled: dict[Path, tuple[float, int]] = {}
_locks: dict[Path, asyncio.Lock] = {}
_clock = time.monotonic
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


def _throttled_error(status: int, retry_after: float) -> RateLimitError | NetworkError:
    if status == 429:
        return RateLimitError(
            f"HTTP {status}",
            status=status,
            route=_ROUTE,
            retry_after=retry_after,
            remediation_hint=_THROTTLED_HINT,
        )
    return NetworkError(f"HTTP {status}", status=status, route=_ROUTE)


def _gate(profile_dir: Path) -> None:
    """Refuse a read this profile must not make: signed out, or inside a throttle window."""
    if profile_dir in _signed_out:
        raise WireReadUnavailable(WireFallbackReason.DISABLED)
    if (window := _throttled.get(profile_dir)) is not None:
        deadline, status = window
        if (remaining := deadline - _clock()) > 0:
            raise _throttled_error(status, remaining)
        del _throttled[profile_dir]


def _check(response: httpx.Response, profile_dir: Path) -> None:
    if names := sorted(
        {h.split("=", 1)[0].strip() for h in response.headers.get_list("set-cookie")}
    ):
        log.debug("credits.wire_set_cookie_dropped", names=names)
    status = response.status_code
    if status == 200:
        return
    _tokens.pop(profile_dir, None)  # a stale `at` must not stick in a long-lived process
    if 300 <= status < 400:
        raise WireReadUnavailable(WireFallbackReason.REDIRECT)
    if status == 401:
        _signed_out.add(profile_dir)
        raise WireReadUnavailable(WireFallbackReason.HTTP_401)
    if status in (429, 503):
        window = parse_retry_after(response) or _THROTTLE_DEFAULT_S
        _throttled[profile_dir] = (_clock() + window, status)
        raise _throttled_error(status, window)
    if status == 400:
        raise WireReadUnavailable(WireFallbackReason.HTTP_400)
    raise WireReadUnavailable(WireFallbackReason.HTTP_ERROR)


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
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "sec-fetch-dest": "empty",
        },
    )


async def _shell_tokens(
    client: httpx.AsyncClient, profile_dir: Path, headers: dict[str, str]
) -> _Tokens:
    async with _locks.setdefault(profile_dir, asyncio.Lock()):  # single flight per profile
        _gate(profile_dir)  # a read that held the lock may just have closed the door
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
            _signed_out.add(profile_dir)
            raise WireReadUnavailable(WireFallbackReason.SIGNED_OUT_SHELL)
        tokens = _Tokens(at, _wiz(response.text, "FdrFJe"), _wiz(response.text, "cfb2h"))
        _tokens[profile_dir] = tokens
        return tokens


async def read_credits(profile_dir: Path) -> CreditsInfo:
    """The balance over the wire, or :class:`WireReadUnavailable`. Never starts a browser."""
    require_in_home(profile_dir)  # before anything reads the profile (row 19)
    _gate(profile_dir)
    if get_settings().browser_proxy:
        # The browser goes through the proxy; its cookies must not leave from another IP.
        raise WireReadUnavailable(WireFallbackReason.PROXY_CONFIGURED)
    if (ua := _user_agent(profile_dir)) is None:
        raise WireReadUnavailable(WireFallbackReason.NO_CHROME_VERSION)
    started = time.perf_counter()
    try:
        cookies = await asyncio.wait_for(asyncio.to_thread(read_cookies_for, profile_dir), _TOTAL_S)
    except (CookieStoreUnreadable, TimeoutError):
        raise WireReadUnavailable(WireFallbackReason.STORE_UNREADABLE) from None
    if not cookies:
        raise WireReadUnavailable(WireFallbackReason.STORE_UNREADABLE)
    headers = {
        "user-agent": ua,
        "accept-language": "en-US,en;q=0.9",
        "cookie": "; ".join(f"{name}={value}" for name, value in cookies),
    }
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
            _gate(profile_dir)  # another read's POST may have been throttled meanwhile
            response = await _send(client, _rpc_request("nzlxg", tokens, headers))
            _check(response, profile_dir)
    except (TimeoutError, httpx.TransportError):
        raise WireReadUnavailable(WireFallbackReason.NETWORK) from None
    frames = [payload for rpcid, payload in parse_frames(response.text) if rpcid == "nzlxg"]
    try:
        info = CreditsInfo.from_nzlxg(frames[0] if frames else None)
    except ValueError:
        _tokens.pop(profile_dir, None)
        if codes := [e.code for e in rpc_errors(response.text) if e.code is not None]:
            # Only the numeric status: a real rate-limit code shows up here before we act on it.
            log.info("credits.wire_rpc_error", codes=codes)
        raise WireReadUnavailable(WireFallbackReason.UNPARSEABLE) from None
    log.info("credits.wire_read", ms=round((time.perf_counter() - started) * 1000))
    return info


def session_recovered(profile_dir: Path) -> None:
    """The session is known good again (a page read answered, or a renew renewed it): lift
    the signed-out mark and the cached ``at`` so the next read tries the wire once more.
    A throttle window is time-based and stays."""
    _signed_out.discard(profile_dir)
    _tokens.pop(profile_dir, None)


async def health(profile_dir: Path) -> dict[str, str | None]:
    """The direct read's health for ``gflow doctor`` and ``gflow_auth_status`` (G6).

    The probe IS one credits read (one GET, one POST), under every rule ``read_credits``
    keeps, plus the kill switch / macOS default it leaves to its caller. Returns only an
    enum ``status`` (``accepted``, ``throttled``, or a :class:`WireFallbackReason` value) and
    the Flow build label when one was read; never a cookie, token, id or URL. Never raises.
    """
    status: str
    if not get_settings().wire_reads:
        status = WireFallbackReason.DISABLED.value
    elif profile_dir in _signed_out:
        # Flow already refused this session in this process; _gate would say DISABLED,
        # which reads as the kill switch. shortcut: a 401 also lands here; split if needed.
        status = WireFallbackReason.SIGNED_OUT_SHELL.value
    else:
        try:
            await read_credits(profile_dir)
        except WireReadUnavailable as exc:
            status = exc.reason.value
        except (RateLimitError, NetworkError):  # only ever the throttle window here
            status = "throttled"
        except Exception as exc:  # noqa: BLE001 - a health probe reports, it never fails a caller
            log.warning("wire.health_unexpected", error_type=type(exc).__name__)
            status = WireFallbackReason.UNEXPECTED.value
        else:
            tokens = _tokens.get(profile_dir)
            bl = tokens.bl if tokens else None
            return {"status": "accepted", "build": bl if bl and _BUILD.fullmatch(bl) else None}
    return {"status": status, "build": None}
