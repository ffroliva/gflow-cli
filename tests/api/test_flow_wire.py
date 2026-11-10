"""The direct ("wire") credits read: one shell GET, one ``nzlxg`` POST, no browser (#1229).

SCENARIO rows 1-8, 13, 14, 16, 21 (logs), 23 and 26 of
``docs/superpowers/plans/2026-11-10-flow-reader/SCENARIO.md``. Everything runs against
``httpx.MockTransport`` and fake cookie values; nothing here touches a real jar or the network.
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import structlog

from gflow_cli.api import flow_wire
from gflow_cli.api.flow_wire import WireFallbackReason, WireReadUnavailable
from gflow_cli.auth.cookies import CookieStoreUnreadable
from gflow_cli.errors import NetworkError, RateLimitError

_FAKE_COOKIES = [("SID", "fake-sid-value"), ("__Secure-3PSID", "fake-3psid"), ("OSID", "fake-osid")]
_FAKE_AT = "AFAKEATTOKEN:1700000000000"
_SHELL = (
    '<html><script>window.WIZ_global_data = {"SNlM0e":"' + _FAKE_AT + '",'
    '"FdrFJe":"-1234567890","cfb2h":"boq_labs-flow-frontend_20261110.00_p0"};</script></html>'
)
_SIGNED_OUT_SHELL = '<html><script>window.WIZ_global_data = {"cfb2h":"boq_x"};</script></html>'
_REPLY = (
    ')]}\'\n\n58\n[["wrb.fr","nzlxg","[42,null,null,null,null,42]",null,null,null,"generic"]]\n'
)


class _Flow:
    """A scripted flow.google.com: counts requests, records what each one carried."""

    def __init__(self) -> None:
        self.gets: list[httpx.Request] = []
        self.posts: list[httpx.Request] = []
        self.shell: Callable[[httpx.Request], httpx.Response] = lambda _r: httpx.Response(
            200,
            text=_SHELL,
            headers={"set-cookie": "SIDCC=fake-reminted; Path=/; Domain=.google.com"},
        )
        self.rpc: Callable[[httpx.Request], httpx.Response] = lambda _r: httpx.Response(
            200, text=_REPLY, headers={"set-cookie": "__Secure-1PSIDCC=fake-reminted2; Path=/"}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.gets.append(request)
            return self.shell(request)
        self.posts.append(request)
        return self.rpc(request)


@pytest.fixture(autouse=True)
def _fresh(fresh_wire: None) -> None:
    """Module state must never leak between tests (tests/conftest.py::fresh_wire)."""


@pytest.fixture
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = _home() / "profile_x"
    path.mkdir(parents=True)
    monkeypatch.setattr(flow_wire, "read_cookies_for", lambda _p: list(_FAKE_COOKIES))
    monkeypatch.setattr(flow_wire, "profile_last_version", lambda _p: "150.0.7311.42")
    return path


@pytest.fixture
def flow(monkeypatch: pytest.MonkeyPatch) -> _Flow:
    """Route every ``httpx.AsyncClient`` flow_wire builds through the scripted Flow.

    The real client class is kept (follow_redirects, the cookie jar, the timeouts are
    the code under test); only its transport is swapped.
    """
    fake = _Flow()
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(real, transport=httpx.MockTransport(fake))
    )
    return fake


def _home() -> Path:
    """Profiles must live under GFLOW_CLI_HOME: the wire checks that before anything else."""
    from gflow_cli.config import get_settings

    return get_settings().home / "profiles"


def _reason(exc: pytest.ExceptionInfo[WireReadUnavailable]) -> WireFallbackReason:
    return exc.value.reason


# --- the healthy read and its request budget (rows 16, 26) -------------------------------


async def test_healthy_read_is_one_get_and_one_post(profile: Path, flow: _Flow) -> None:
    info = await flow_wire.read_credits(profile)

    assert info.credits == 42
    assert len(flow.gets) == 1
    assert len(flow.posts) == 1


async def test_the_post_carries_the_shell_tokens_and_browser_headers(
    profile: Path, flow: _Flow
) -> None:
    await flow_wire.read_credits(profile)

    post = flow.posts[0]
    assert post.url.params["rpcids"] == "nzlxg"
    assert post.url.params["f.sid"] == "-1234567890"
    assert post.url.params["bl"] == "boq_labs-flow-frontend_20261110.00_p0"
    assert b"at=AFAKEATTOKEN" in post.content
    assert post.headers["x-same-domain"] == "1"
    assert post.headers["origin"] == "https://flow.google.com"
    assert post.headers["referer"] == "https://flow.google.com/"
    for request in (flow.gets[0], post):
        assert "Chrome/150.0.0.0" in request.headers["user-agent"]
        assert "python-httpx" not in request.headers["user-agent"]


async def test_a_second_read_in_the_process_reuses_at(profile: Path, flow: _Flow) -> None:
    await flow_wire.read_credits(profile)
    await flow_wire.read_credits(profile)

    assert len(flow.gets) == 1
    assert len(flow.posts) == 2


async def test_five_concurrent_reads_share_one_shell_get(profile: Path, flow: _Flow) -> None:
    """Row 14: the `at` fetch is single-flight under the lock."""
    results = await asyncio.gather(*(flow_wire.read_credits(profile) for _ in range(5)))

    assert [r.credits for r in results] == [42] * 5
    assert len(flow.gets) == 1
    assert len(flow.posts) == 5


async def test_the_token_cache_is_per_profile(tmp_path: Path, profile: Path, flow: _Flow) -> None:
    other = _home() / "profile_y"
    other.mkdir(parents=True)
    await flow_wire.read_credits(profile)
    await flow_wire.read_credits(other)

    assert len(flow.gets) == 2


def test_the_rpcid_allow_list_refuses_anything_but_nzlxg() -> None:
    tokens = flow_wire._Tokens(at="x", fsid="1", bl="b")  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="allow-list"):
        flow_wire._rpc_request("YhhmEf", tokens, {})  # pyright: ignore[reportPrivateUsage]


# --- Set-Cookie is dropped, never fed back (row 13) --------------------------------------


async def test_set_cookie_is_never_sent_back(profile: Path, flow: _Flow) -> None:
    await flow_wire.read_credits(profile)

    built = "; ".join(f"{n}={v}" for n, v in _FAKE_COOKIES)
    assert flow.gets[0].headers["cookie"] == built
    assert flow.posts[0].headers["cookie"] == built
    assert "reminted" not in flow.posts[0].headers["cookie"]


# --- every G3 failure class (rows 1-8) ----------------------------------------------------


async def test_signed_out_shell_falls_back_and_disables_the_wire(
    profile: Path, flow: _Flow
) -> None:
    flow.shell = lambda _r: httpx.Response(200, text=_SIGNED_OUT_SHELL)

    with pytest.raises(WireReadUnavailable) as first:
        await flow_wire.read_credits(profile)
    assert _reason(first) is WireFallbackReason.SIGNED_OUT_SHELL
    assert flow.posts == []

    with pytest.raises(WireReadUnavailable) as second:
        await flow_wire.read_credits(profile)
    assert _reason(second) is WireFallbackReason.DISABLED
    assert len(flow.gets) == 1  # the disabled wire sends nothing


async def test_http_401_falls_back_without_retry_and_disables_the_wire(
    profile: Path, flow: _Flow
) -> None:
    flow.rpc = lambda _r: httpx.Response(401, text="")

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.HTTP_401
    assert len(flow.posts) == 1

    with pytest.raises(WireReadUnavailable) as again:
        await flow_wire.read_credits(profile)
    assert _reason(again) is WireFallbackReason.DISABLED
    assert len(flow.posts) == 1


async def test_http_400_falls_back_without_retry_and_drops_the_cached_at(
    profile: Path, flow: _Flow
) -> None:
    flow.rpc = lambda _r: httpx.Response(400, text="")

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.HTTP_400
    assert len(flow.posts) == 1

    flow.rpc = lambda _r: httpx.Response(200, text=_REPLY)
    assert (await flow_wire.read_credits(profile)).credits == 42
    assert len(flow.gets) == 2  # a stale `at` is fetched again, not reused


@pytest.mark.parametrize("status", [403, 404, 418, 500, 502, 504])
async def test_any_other_status_falls_back_without_disabling(
    profile: Path, flow: _Flow, status: int
) -> None:
    """500/502/504 are not back-pressure (only 429/503 are): fall back, stay enabled, and
    drop the cached `at` so a stale token cannot stick in a long-lived process."""
    flow.rpc = lambda _r: httpx.Response(status, text="")

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.HTTP_ERROR
    assert len(flow.posts) == 1

    flow.rpc = lambda _r: httpx.Response(200, text=_REPLY)
    assert (await flow_wire.read_credits(profile)).credits == 42
    assert len(flow.gets) == 2


@pytest.mark.parametrize(
    "body",
    [
        "",
        ")]}'\n\n",
        ')]}\'\n[["wrb.fr","nzlxg",null,null,null,[3],"generic"]]\n',  # an `er`-style null frame
        ')]}\'\n[["wrb.fr","nzlxg","[\\"moved\\",42]",null,null,null,"generic"]]\n',
        ')]}\'\n[["wrb.fr","nzlxg","[-1]",null,null,null,"generic"]]\n',
        ')]}\'\n[["wrb.fr","other","[42]",null,null,null,"generic"]]\n',
        "<html>sign in</html>",
    ],
)
async def test_an_unparseable_reply_falls_back_and_never_reads_zero(
    profile: Path, flow: _Flow, body: str
) -> None:
    flow.rpc = lambda _r: httpx.Response(200, text=body)

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.UNPARSEABLE

    flow.rpc = lambda _r: httpx.Response(200, text=_REPLY)
    assert (await flow_wire.read_credits(profile)).credits == 42
    assert len(flow.gets) == 2  # the cached `at` was dropped with the bad reply


async def test_an_unreadable_cookie_store_falls_back_before_any_request(
    profile: Path, flow: _Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(_p: Path) -> list[tuple[str, str]]:
        raise CookieStoreUnreadable("PermissionError")

    monkeypatch.setattr(flow_wire, "read_cookies_for", locked)

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.STORE_UNREADABLE
    assert flow.gets == [] and flow.posts == []


async def test_a_profile_with_no_chrome_version_falls_back_before_the_cookie_read(
    profile: Path, flow: _Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Checked before the decrypt, and not reported as an unreadable store."""
    reads: list[Path] = []
    monkeypatch.setattr(flow_wire, "profile_last_version", lambda _p: None)
    monkeypatch.setattr(flow_wire, "read_cookies_for", lambda p: reads.append(p) or [])

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.NO_CHROME_VERSION
    assert reads == []
    assert flow.gets == []


@pytest.mark.parametrize(
    "error", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t"), httpx.RemoteProtocolError("p")]
)
async def test_a_network_error_falls_back_without_retry(
    profile: Path, flow: _Flow, error: Exception
) -> None:
    def fail(_r: httpx.Request) -> httpx.Response:
        raise error

    flow.shell = fail
    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.NETWORK
    assert len(flow.gets) == 1


async def test_a_connect_error_is_retried_once_then_falls_back(profile: Path, flow: _Flow) -> None:
    def refuse(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    flow.shell = refuse
    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.NETWORK
    assert len(flow.gets) == 2


async def test_one_connect_error_then_success_reads_the_balance(profile: Path, flow: _Flow) -> None:
    calls = {"n": 0}

    def flaky(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text=_SHELL)

    flow.shell = flaky
    assert (await flow_wire.read_credits(profile)).credits == 42
    assert len(flow.gets) == 2


async def test_the_whole_read_is_bounded_by_a_total_deadline(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hang(_r: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200, text=_SHELL)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(real, transport=httpx.MockTransport(hang))
    )
    monkeypatch.setattr(flow_wire, "_TOTAL_S", 0.05)

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.NETWORK


def test_the_client_timeouts_are_connect_3s_total_5s() -> None:
    assert flow_wire._CONNECT_S == 3.0  # pyright: ignore[reportPrivateUsage]
    assert flow_wire._TOTAL_S == 5.0  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_a_redirect_is_not_followed(profile: Path, flow: _Flow, status: int) -> None:
    flow.shell = lambda _r: httpx.Response(
        status, headers={"location": "https://accounts.google.com/ServiceLogin"}
    )

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.REDIRECT
    assert len(flow.gets) == 1
    assert all(r.url.host == "flow.google.com" for r in flow.gets)


async def test_a_redirect_on_the_post_is_not_followed(profile: Path, flow: _Flow) -> None:
    flow.rpc = lambda _r: httpx.Response(302, headers={"location": "https://accounts.google.com/"})

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.REDIRECT
    assert len(flow.posts) == 1


# --- the cookie read runs off the event loop (row 23) -------------------------------------


async def test_the_cookie_read_runs_in_a_worker_thread(
    profile: Path, flow: _Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def read(_p: Path) -> list[tuple[str, str]]:
        seen.append(threading.get_ident())
        return list(_FAKE_COOKIES)

    monkeypatch.setattr(flow_wire, "read_cookies_for", read)
    await flow_wire.read_credits(profile)

    assert seen and seen[0] != threading.get_ident()


# --- no secret reaches a log line (row 21) ------------------------------------------------


async def test_failure_logs_carry_no_secret(profile: Path, flow: _Flow) -> None:
    flow.rpc = lambda _r: httpx.Response(
        200, text=f"<html>{_FAKE_AT} SID=fake-sid-value https://x/y?Signature=fake</html>"
    )
    with structlog.testing.capture_logs() as logs, pytest.raises(WireReadUnavailable):
        await flow_wire.read_credits(profile)

    rendered = repr(logs)
    for secret in ("AFAKEATTOKEN", "fake-sid-value", "fake-3psid", "fake-osid", "reminted"):
        assert secret not in rendered


def test_the_reason_never_carries_detail() -> None:
    exc = WireReadUnavailable(WireFallbackReason.NETWORK)
    assert str(exc) == "network"
    assert exc.args == ("network",)


# --- back-pressure is not a fallback (row 29) ----------------------------------------------
# A page load sends the same RPCs to a throttled account. 429 and 503 raise the page path's
# own typed errors and open a per-profile window (Retry-After, else 60 s) inside which every
# read re-raises with no request and no page; after it, the wire is tried again.


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(flow_wire, "_clock", lambda: now[0])
    return now


@pytest.mark.parametrize("on", ["get", "post"])
@pytest.mark.parametrize(
    ("headers", "window"),
    [({"retry-after": "7"}, 7.0), ({}, 60.0), ({"retry-after": "900"}, 60.0)],
)
async def test_429_throttles_that_profile_for_its_window(
    profile: Path, flow: _Flow, clock: list[float], on: str, headers: dict[str, str], window: float
) -> None:
    reply = lambda _r: httpx.Response(429, headers=headers)  # noqa: E731
    if on == "get":
        flow.shell = reply
    else:
        flow.rpc = reply

    with pytest.raises(RateLimitError) as first:
        await flow_wire.read_credits(profile)
    assert first.value.status == 429
    assert first.value.retry_after == window
    sent = len(flow.gets) + len(flow.posts)
    assert sent == (1 if on == "get" else 2)  # no retry

    clock[0] += 2.0  # a SECOND read inside the window: typed error, nothing sent
    with pytest.raises(RateLimitError) as second:
        await flow_wire.read_credits(profile)
    assert second.value.retry_after == pytest.approx(window - 2.0)
    assert len(flow.gets) + len(flow.posts) == sent

    clock[0] += window  # after the window the wire is tried again
    flow.shell = lambda _r: httpx.Response(200, text=_SHELL)
    flow.rpc = lambda _r: httpx.Response(200, text=_REPLY)
    assert (await flow_wire.read_credits(profile)).credits == 42


async def test_503_throttles_that_profile_with_a_network_error(
    profile: Path, flow: _Flow, clock: list[float]
) -> None:
    flow.rpc = lambda _r: httpx.Response(503)

    with pytest.raises(NetworkError) as first:
        await flow_wire.read_credits(profile)
    assert first.value.status == 503
    with pytest.raises(NetworkError):
        await flow_wire.read_credits(profile)
    assert len(flow.posts) == 1


async def test_503_on_the_shell_get_raises_network_error(profile: Path, flow: _Flow) -> None:
    flow.shell = lambda _r: httpx.Response(503)

    with pytest.raises(NetworkError):
        await flow_wire.read_credits(profile)
    assert flow.posts == []


async def test_a_throttle_on_one_profile_leaves_the_others_alone(
    tmp_path: Path, profile: Path, flow: _Flow, clock: list[float]
) -> None:
    flow.rpc = lambda _r: httpx.Response(429)
    with pytest.raises(RateLimitError):
        await flow_wire.read_credits(profile)

    flow.rpc = lambda _r: httpx.Response(200, text=_REPLY)
    other = _home() / "profile_b"
    other.mkdir(parents=True)
    assert (await flow_wire.read_credits(other)).credits == 42


# --- an RPC error envelope is unparseable, and only its numeric code is logged (row 30) ----
# An in-body RESOURCE_EXHAUSTED stays `unparseable` until a real capture shows its shape.

_NULL_FRAME = ')]}\'\n[["wrb.fr","nzlxg",null,null,null,[8],"generic"]]\n'


async def test_an_rpc_error_envelope_logs_only_its_status_code(profile: Path, flow: _Flow) -> None:
    flow.rpc = lambda _r: httpx.Response(200, text=_NULL_FRAME)

    with structlog.testing.capture_logs() as logs, pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)

    assert _reason(exc) is WireFallbackReason.UNPARSEABLE
    (event,) = [e for e in logs if e["event"] == "credits.wire_rpc_error"]
    assert event["codes"] == [8]
    assert set(event) <= {"event", "log_level", "codes"}


async def test_signed_out_is_judged_by_the_token_never_by_size(profile: Path, flow: _Flow) -> None:
    """The spike's signed-out shell was ~161 KB: only a missing SNlM0e means signed out."""
    big = "<html>" + "x" * 170_000 + _SHELL + "</html>"
    flow.shell = lambda _r: httpx.Response(200, text=big)

    assert (await flow_wire.read_credits(profile)).credits == 42


# --- per-profile state (single flight, disable, lock) ---------------------------------------


async def test_concurrent_reads_stop_after_a_signed_out_shell(profile: Path, flow: _Flow) -> None:
    """Five reads queued on the profile's lock must not each send their own GET."""
    flow.shell = lambda _r: httpx.Response(200, text=_SIGNED_OUT_SHELL)

    results = await asyncio.gather(
        *(flow_wire.read_credits(profile) for _ in range(5)), return_exceptions=True
    )

    assert len(flow.gets) == 1
    later = [r for r in results[1:] if isinstance(r, WireReadUnavailable)]
    assert [r.reason for r in later] == [WireFallbackReason.DISABLED] * 4


async def test_concurrent_reads_during_a_429_all_raise_the_typed_error(
    profile: Path, flow: _Flow, clock: list[float]
) -> None:
    """None of them may become a page load: the caller builds no browser for these."""
    flow.shell = lambda _r: httpx.Response(429, headers={"retry-after": "5"})

    results = await asyncio.gather(
        *(flow_wire.read_credits(profile) for _ in range(5)), return_exceptions=True
    )

    assert len(flow.gets) == 1
    assert all(isinstance(r, RateLimitError) for r in results), results


async def test_a_signed_out_profile_does_not_turn_the_wire_off_for_another(
    tmp_path: Path, profile: Path, flow: _Flow
) -> None:
    flow.shell = lambda _r: httpx.Response(200, text=_SIGNED_OUT_SHELL)
    with pytest.raises(WireReadUnavailable):
        await flow_wire.read_credits(profile)

    flow.shell = lambda _r: httpx.Response(200, text=_SHELL)
    other = _home() / "profile_b"
    other.mkdir(parents=True)
    assert (await flow_wire.read_credits(other)).credits == 42

    with pytest.raises(WireReadUnavailable) as again:  # the signed-out one stays off
        await flow_wire.read_credits(profile)
    assert _reason(again) is WireFallbackReason.DISABLED


async def test_five_profiles_read_concurrently_each_on_its_own_lock(
    tmp_path: Path, profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow shell GET on one profile must not hold the others past the read's budget."""

    async def slow(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            await asyncio.sleep(0.2)
            return httpx.Response(200, text=_SHELL)
        return httpx.Response(200, text=_REPLY)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(real, transport=httpx.MockTransport(slow))
    )
    monkeypatch.setattr(flow_wire, "_TOTAL_S", 0.5)
    profiles: list[Path] = []
    for name in "abcde":
        (_home() / name).mkdir(parents=True)
        profiles.append(_home() / name)

    results = await asyncio.gather(*(flow_wire.read_credits(p) for p in profiles))

    assert [r.credits for r in results] == [42] * 5


# --- request shape --------------------------------------------------------------------------


async def test_a_cookie_flow_sets_never_reaches_the_client_jar(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 13, on the client itself: Flow's Set-Cookie must not be stored for a later send."""
    fake = _Flow()
    jars: list[httpx.Cookies] = []
    real = httpx.AsyncClient

    def build(**kwargs: object) -> httpx.AsyncClient:
        client = real(transport=httpx.MockTransport(fake), **kwargs)  # type: ignore[arg-type]
        jars.append(client.cookies)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", build)
    await flow_wire.read_credits(profile)

    assert [len(jar) for jar in jars] == [0]


async def test_an_empty_jar_is_unreadable_before_any_request(
    profile: Path, flow: _Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow_wire, "read_cookies_for", lambda _p: [])

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.STORE_UNREADABLE
    assert flow.gets == []


async def test_both_requests_carry_chrome_fetch_metadata(profile: Path, flow: _Flow) -> None:
    await flow_wire.read_credits(profile)

    get, post = flow.gets[0].headers, flow.posts[0].headers
    assert (get["sec-fetch-site"], get["sec-fetch-mode"], get["sec-fetch-dest"]) == (
        "none",
        "navigate",
        "document",
    )
    assert (post["sec-fetch-site"], post["sec-fetch-mode"], post["sec-fetch-dest"]) == (
        "same-origin",
        "cors",
        "empty",
    )
    assert "sec-ch-ua" not in get and "sec-ch-ua" not in post  # deferred: GREASE unknown


@pytest.mark.parametrize("var", ["HTTPS_PROXY", "ALL_PROXY", "https_proxy"])
async def test_an_environment_proxy_skips_the_wire(
    profile: Path, flow: _Flow, monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    """CodeRabbit 4526220434: the browser ignores these, httpx would honour them. The real
    `getproxies` (environment, and on Windows the system proxy) decides."""
    import urllib.request

    for name in (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(var, "http://127.0.0.1:3128")
    monkeypatch.setattr(flow_wire, "getproxies", urllib.request.getproxies)
    reads: list[Path] = []
    monkeypatch.setattr(flow_wire, "read_cookies_for", lambda p: reads.append(p) or [])

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.PROXY_CONFIGURED
    assert reads == [] and flow.gets == []


async def test_the_client_ignores_the_environment(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[dict[str, object]] = []
    real = httpx.AsyncClient

    def build(**kwargs: object) -> httpx.AsyncClient:
        built.append(kwargs)
        return real(transport=httpx.MockTransport(_Flow()), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", build)
    await flow_wire.read_credits(profile)

    assert [b.get("trust_env") for b in built] == [False]


async def test_a_configured_browser_proxy_skips_the_wire(
    profile: Path, flow: _Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Session cookies must never leave from a different IP than the browser's."""
    from gflow_cli.config import reset_settings

    reads: list[Path] = []
    monkeypatch.setattr(flow_wire, "read_cookies_for", lambda p: reads.append(p) or [])
    monkeypatch.setenv("GFLOW_CLI_BROWSER_PROXY", "http://127.0.0.1:3128")
    reset_settings()

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.PROXY_CONFIGURED
    assert reads == [] and flow.gets == []


async def test_a_blocking_cookie_read_is_bounded_by_the_deadline(
    profile: Path, flow: _Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Linux keyring that never answers must not hang the read."""

    def stuck(_p: Path) -> list[tuple[str, str]]:
        time.sleep(1.0)
        return []

    monkeypatch.setattr(flow_wire, "read_cookies_for", stuck)
    monkeypatch.setattr(flow_wire, "_TOTAL_S", 0.05)

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.STORE_UNREADABLE
