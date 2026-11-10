"""The direct ("wire") credits read: one shell GET, one ``nzlxg`` POST, no browser (#1229).

SCENARIO rows 1-8, 13, 14, 16, 21 (logs), 23 and 26 of
``docs/superpowers/plans/2026-11-10-flow-reader/SCENARIO.md``. Everything runs against
``httpx.MockTransport`` and fake cookie values; nothing here touches a real jar or the network.
"""

from __future__ import annotations

import asyncio
import functools
import threading
from collections.abc import Callable, Iterator
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
def _fresh_wire_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Module state (token cache, process-wide disable, lock) must never leak between tests."""
    monkeypatch.setattr(flow_wire, "_tokens", {})
    monkeypatch.setattr(flow_wire, "_disabled", None)
    monkeypatch.setattr(flow_wire, "_lock", None)
    yield


@pytest.fixture
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "profile_x"
    path.mkdir()
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
    other = tmp_path / "profile_y"
    other.mkdir()
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


@pytest.mark.parametrize("status", [403, 404, 418])
async def test_any_other_status_falls_back(profile: Path, flow: _Flow, status: int) -> None:
    flow.rpc = lambda _r: httpx.Response(status, text="")

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.HTTP_ERROR
    assert len(flow.posts) == 1


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


async def test_a_profile_with_no_chrome_version_falls_back_before_any_request(
    profile: Path, flow: _Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow_wire, "profile_last_version", lambda _p: None)

    with pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)
    assert _reason(exc) is WireFallbackReason.STORE_UNREADABLE
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
# A page load sends the same RPCs to a throttled account, so 429/5xx surface as the typed
# errors the page path already raises, and the wire stays off for the process.


@pytest.mark.parametrize("on", ["get", "post"])
@pytest.mark.parametrize(
    ("headers", "expected"),
    [({"retry-after": "7"}, 7.0), ({}, None), ({"retry-after": "900"}, 60.0)],
)
async def test_429_raises_rate_limit_and_disables_the_wire(
    profile: Path, flow: _Flow, on: str, headers: dict[str, str], expected: float | None
) -> None:
    reply = lambda _r: httpx.Response(429, headers=headers)  # noqa: E731
    if on == "get":
        flow.shell = reply
    else:
        flow.rpc = reply

    with pytest.raises(RateLimitError) as exc:
        await flow_wire.read_credits(profile)
    assert exc.value.retry_after == expected
    assert exc.value.status == 429
    assert len(flow.gets) + len(flow.posts) == (1 if on == "get" else 2)  # no retry

    with pytest.raises(WireReadUnavailable) as again:
        await flow_wire.read_credits(profile)
    assert _reason(again) is WireFallbackReason.DISABLED


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_5xx_raises_network_error_and_disables_the_wire(
    profile: Path, flow: _Flow, status: int
) -> None:
    flow.rpc = lambda _r: httpx.Response(status)

    with pytest.raises(NetworkError) as exc:
        await flow_wire.read_credits(profile)
    assert exc.value.status == status
    assert len(flow.posts) == 1

    with pytest.raises(WireReadUnavailable) as again:
        await flow_wire.read_credits(profile)
    assert _reason(again) is WireFallbackReason.DISABLED


# --- an RPC error envelope is unparseable, and only its numeric code is logged (row 30) ----


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (')]}\'\n[["er",null,null,null,null,429,null,null,null,8],["di",12]]\n', 429),
        (')]}\'\n[["wrb.fr","nzlxg",null,null,null,[8],"generic"]]\n', 8),
    ],
)
async def test_an_rpc_error_envelope_logs_only_its_status_code(
    profile: Path, flow: _Flow, body: str, code: int
) -> None:
    flow.rpc = lambda _r: httpx.Response(200, text=body)

    with structlog.testing.capture_logs() as logs, pytest.raises(WireReadUnavailable) as exc:
        await flow_wire.read_credits(profile)

    assert _reason(exc) is WireFallbackReason.UNPARSEABLE
    (event,) = [e for e in logs if e["event"] == "credits.wire_rpc_error"]
    assert event["codes"] == [code]
    assert set(event) <= {"event", "log_level", "codes"}


async def test_signed_out_is_judged_by_the_token_never_by_size(profile: Path, flow: _Flow) -> None:
    """The spike's signed-out shell was ~161 KB: only a missing SNlM0e means signed out."""
    big = "<html>" + "x" * 170_000 + _SHELL + "</html>"
    flow.shell = lambda _r: httpx.Response(200, text=big)

    assert (await flow_wire.read_credits(profile)).credits == 42


@pytest.mark.parametrize("status", [503, 500])
async def test_5xx_on_the_shell_get_raises_network_error(
    profile: Path, flow: _Flow, status: int
) -> None:
    flow.shell = lambda _r: httpx.Response(status)

    with pytest.raises(NetworkError):
        await flow_wire.read_credits(profile)
    assert flow.posts == []


@pytest.mark.parametrize(
    "shell",
    [
        lambda _r: httpx.Response(200, text=_SIGNED_OUT_SHELL),
        lambda _r: httpx.Response(429, headers={"retry-after": "5"}),
    ],
    ids=["signed_out", "429"],
)
async def test_concurrent_reads_stop_after_the_first_turns_the_wire_off(
    profile: Path, flow: _Flow, shell: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Five reads queued on the single-flight lock must not each send their own GET."""
    flow.shell = shell

    results = await asyncio.gather(
        *(flow_wire.read_credits(profile) for _ in range(5)), return_exceptions=True
    )

    assert len(flow.gets) == 1
    assert all(isinstance(r, Exception) for r in results)
    later = [r for r in results[1:] if isinstance(r, WireReadUnavailable)]
    assert [r.reason for r in later] == [WireFallbackReason.DISABLED] * 4


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


async def test_no_cookies_sends_no_cookie_header(
    profile: Path, flow: _Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow_wire, "read_cookies_for", lambda _p: [])
    flow.shell = lambda _r: httpx.Response(200, text=_SIGNED_OUT_SHELL)

    with pytest.raises(WireReadUnavailable):
        await flow_wire.read_credits(profile)
    assert "cookie" not in flow.gets[0].headers
