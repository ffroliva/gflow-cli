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
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode
from urllib.request import getproxies

import httpx
import structlog

from gflow_cli.api._retry import parse_retry_after
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors
from gflow_cli.auth.cookies import CookieStoreUnreadable, read_cookies_for, require_in_home
from gflow_cli.browser_manager import profile_last_version
from gflow_cli.config import Settings, get_settings
from gflow_cli.errors import NetworkError, RateLimitError, SecurityError

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


# Named for what it signals (fall back to the browser), not as an error: hence N818.
class WireReadUnavailable(Exception):  # noqa: N818
    """The direct read cannot answer; the reason is all it carries (no body, no str(exc))."""

    def __init__(self, reason: WireFallbackReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True)
class _Tokens:
    at: str
    fsid: str | None
    bl: str | None


# Per process, per profile: the shell's tokens, profiles Flow said are signed out (with the
# reason it gave), throttle windows (monotonic deadline, the status that opened it), and one
# lock each.
_tokens: dict[Path, _Tokens] = {}
_signed_out: dict[Path, WireFallbackReason] = {}
_throttled: dict[Path, tuple[float, int]] = {}
_locks: dict[Path, asyncio.Lock] = {}
_clock = time.monotonic
_reqid = 1000 + secrets.randbelow(9000)  # the app's own counter starts at a random 4 digits


def reset_state() -> None:
    """Forget every profile's tokens, sign-out, throttle window and lock (tests, a new loop)."""
    _tokens.clear()
    _signed_out.clear()
    _throttled.clear()
    _locks.clear()


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
    if (reason := _signed_out.get(profile_dir)) is not None:
        raise WireReadUnavailable(reason)  # never DISABLED: that one names the kill switch
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
        _signed_out[profile_dir] = WireFallbackReason.HTTP_401
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
            _signed_out[profile_dir] = WireFallbackReason.SIGNED_OUT_SHELL
            raise WireReadUnavailable(WireFallbackReason.SIGNED_OUT_SHELL)
        tokens = _Tokens(at, _wiz(response.text, "FdrFJe"), _wiz(response.text, "cfb2h"))
        _tokens[profile_dir] = tokens
        return tokens


async def _read_cookies(profile_dir: Path) -> list[tuple[str, str]]:
    """``read_cookies_for`` on a daemon thread, bounded by ``_TOTAL_S``.

    Not ``asyncio.to_thread``: ``asyncio.run`` (``gflow doctor``) joins the default
    executor at shutdown, so a cookie store that never answers (a Linux keyring) would hold
    the process open long after the read gave up. A daemon thread is abandoned instead.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[list[tuple[str, str]]] = loop.create_future()

    def settle(result: list[tuple[str, str]] | None, error: BaseException | None) -> None:
        if future.done():  # the wait already gave up
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result or [])

    def run() -> None:
        try:
            result, error = read_cookies_for(profile_dir), None
        except BaseException as exc:  # noqa: BLE001 - handed to the waiting coroutine
            result, error = None, exc
        try:
            loop.call_soon_threadsafe(settle, result, error)
        except RuntimeError:  # the loop closed while the read was stuck
            pass

    threading.Thread(target=run, name="gflow-cookie-read", daemon=True).start()
    return await asyncio.wait_for(future, _TOTAL_S)


async def read_credits(profile_dir: Path) -> CreditsInfo:
    """The balance over the wire, or :class:`WireReadUnavailable`. Never starts a browser."""
    require_in_home(profile_dir)  # before anything reads the profile (row 19)
    _gate(profile_dir)
    if get_settings().browser_proxy or getproxies():
        # A proxy anywhere (gflow's, the environment's, the OS's) means the browser and a
        # direct request may leave by different routes; the session cookies must not.
        raise WireReadUnavailable(WireFallbackReason.PROXY_CONFIGURED)
    if (ua := _user_agent(profile_dir)) is None:
        raise WireReadUnavailable(WireFallbackReason.NO_CHROME_VERSION)
    started = time.perf_counter()
    try:
        cookies = await _read_cookies(profile_dir)
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
                trust_env=False,  # nothing env-derived (proxies, netrc) reaches this client
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
    """A renew reported the profile renewed (its saved cookies changed): lift the signed-out
    mark and the cached ``at`` so the next read tries the wire once more. A page read is not
    enough (the cookies the wire sends are the same), and a throttle window is time-based."""
    _signed_out.pop(profile_dir, None)
    _tokens.pop(profile_dir, None)


async def health(profile_dir: Path, settings: Settings | None = None) -> dict[str, Any]:
    """The direct read's health for ``gflow doctor`` and ``gflow_auth_status`` (G6).

    An explicit question, so it asks Flow afresh: one shell GET and one ``nzlxg`` POST, under
    every rule ``read_credits`` keeps, plus the kill switch / macOS default and the proxy rule
    from ``settings`` (the caller's, else the global ones). A profile already marked signed out
    is asked again; a throttle window is honoured and sends nothing. Returns only ``status``
    (``accepted``, ``throttled``, or a :class:`WireFallbackReason` value), ``build`` (Flow's
    build label, when read) and, when throttled, ``retry_after`` in seconds; never a cookie,
    token, id or URL. Raises only ``SecurityError`` (a profile outside GFLOW_CLI_HOME).
    """
    settings = settings or get_settings()
    if not settings.wire_reads:
        return {"status": WireFallbackReason.DISABLED.value, "build": None}
    if settings.browser_proxy:
        return {"status": WireFallbackReason.PROXY_CONFIGURED.value, "build": None}
    require_in_home(profile_dir)
    _signed_out.pop(profile_dir, None)  # re-ask; a still-signed-out shell marks it again
    _tokens.pop(profile_dir, None)  # a fresh shell GET, so `build` is current
    try:
        await read_credits(profile_dir)
    except WireReadUnavailable as exc:
        return {"status": exc.reason.value, "build": None}
    except (RateLimitError, NetworkError):  # only ever the throttle window here
        window = _throttled.get(profile_dir)
        retry = max(0, round(window[0] - _clock())) if window else None
        return {"status": "throttled", "build": None, "retry_after": retry}
    except SecurityError:
        raise
    except Exception as exc:  # noqa: BLE001 - a health probe reports, it never fails a caller
        log.warning("wire.health_unexpected", error_type=type(exc).__name__)
        return {"status": WireFallbackReason.UNEXPECTED.value, "build": None}
    tokens = _tokens.get(profile_dir)
    bl = tokens.bl if tokens else None
    return {"status": "accepted", "build": bl if bl and _BUILD.fullmatch(bl) else None}
