"""`services/credits.py` tries the wire before any browser exists, and falls back to it (#1229).

SCENARIO rows 10, 11, 15, 17, 18, 19, 22 and 28. The page path is a fake `FlowApiClient`;
the wire is a fake `flow_wire.read_credits` unless a row needs the real one.
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import structlog

from gflow_cli.api import flow_wire
from gflow_cli.api.dto import CreditsInfo
from gflow_cli.api.flow_wire import WireFallbackReason, WireReadUnavailable
from gflow_cli.config import get_settings, reset_settings
from gflow_cli.errors import (
    AuthExpiredError,
    NetworkError,
    ProfileLockedError,
    RateLimitError,
    SecurityError,
)
from gflow_cli.profile_store import ProfileMeta
from gflow_cli.services import credits


@pytest.fixture(autouse=True)
def _wire_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The offline suite defaults the wire off (tests/conftest.py); these tests are about it."""
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "1")
    reset_settings()
    monkeypatch.setattr(flow_wire, "_tokens", {})
    monkeypatch.setattr(flow_wire, "_signed_out", {})
    monkeypatch.setattr(flow_wire, "_throttled", {})
    monkeypatch.setattr(flow_wire, "_locks", {})


def _meta(name: str = "one") -> ProfileMeta:
    return ProfileMeta(
        name=name,
        profile_dir=get_settings().home / "profiles" / f"profile_{name}",
        cookies_present=True,
        last_used_at=None,
        is_default=True,
        google_account=None,
    )


class _Page:
    """Stands in for the browser read. Counts constructions: G2's sentinel."""

    built = 0
    result: CreditsInfo | Exception = CreditsInfo(credits=11)

    def __init__(self, profile_dir: Path, *, headless: bool = False) -> None:
        type(self).built += 1

    async def __aenter__(self) -> _Page:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_credits(self) -> CreditsInfo:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def page(monkeypatch: pytest.MonkeyPatch) -> type[_Page]:
    class Page(_Page):
        built = 0
        result: CreditsInfo | Exception = CreditsInfo(credits=11)

    monkeypatch.setattr(credits, "FlowApiClient", Page)
    return Page


def _wire(
    monkeypatch: pytest.MonkeyPatch, fn: Callable[[Path], Awaitable[CreditsInfo]]
) -> list[Path]:
    seen: list[Path] = []

    async def read(profile_dir: Path) -> CreditsInfo:
        seen.append(profile_dir)
        return await fn(profile_dir)

    monkeypatch.setattr(flow_wire, "read_credits", read)
    return seen


def _fails(reason: WireFallbackReason) -> Callable[[Path], Awaitable[CreditsInfo]]:
    async def fn(_p: Path) -> CreditsInfo:
        raise WireReadUnavailable(reason)

    return fn


async def _ok(_p: Path) -> CreditsInfo:
    return CreditsInfo(credits=42)


async def test_a_healthy_wire_never_builds_the_browser_client(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    """Row 28 / G2: FlowApiClient launches Chrome on entry, so it must not even be built."""
    _wire(monkeypatch, _ok)

    result = await credits._fetch_once(_meta())  # pyright: ignore[reportPrivateUsage]

    assert result["credits"] == 42
    assert result["status"] == "ok" and result["authenticated"] is True
    assert page.built == 0


@pytest.mark.parametrize("reason", list(WireFallbackReason))
async def test_every_wire_failure_falls_back_to_the_page_and_logs_the_reason(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page], reason: WireFallbackReason
) -> None:
    _wire(monkeypatch, _fails(reason))

    with structlog.testing.capture_logs() as logs:
        result = await credits._fetch_once(_meta())  # pyright: ignore[reportPrivateUsage]

    assert result["credits"] == 11
    assert page.built == 1
    fallback = [e for e in logs if e["event"] == "credits.wire_fallback"]
    assert [e["reason"] for e in fallback] == [reason.value]
    assert [e["source"] for e in logs if e["event"] == "credits.read"] == ["page"]


async def test_an_unexpected_wire_error_still_falls_back(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    async def boom(_p: Path) -> CreditsInfo:
        raise RuntimeError("secret-looking SID=fake-detail")

    _wire(monkeypatch, boom)
    with structlog.testing.capture_logs() as logs:
        result = await credits._fetch_once(_meta())  # pyright: ignore[reportPrivateUsage]

    assert result["credits"] == 11
    (event,) = [e for e in logs if e["event"] == "credits.wire_fallback"]
    assert event["reason"] == "unexpected"
    assert event["error_type"] == "RuntimeError"
    assert "fake-detail" not in repr(logs)


async def test_the_healthy_read_logs_its_source(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    _wire(monkeypatch, _ok)
    with structlog.testing.capture_logs() as logs:
        await credits._fetch_once(_meta())  # pyright: ignore[reportPrivateUsage]

    assert [e["source"] for e in logs if e["event"] == "credits.read"] == ["wire"]


async def test_when_both_fail_the_page_error_surfaces(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    """Row 10 and row 15: a generation holds the lease, the jar is locked -> exit 11 as today."""
    _wire(monkeypatch, _fails(WireFallbackReason.STORE_UNREADABLE))
    page.result = ProfileLockedError("profile in use")

    with pytest.raises(ProfileLockedError):
        await credits._fetch_once(_meta())  # pyright: ignore[reportPrivateUsage]


async def test_refresh_after_a_signed_out_wire_renews_and_reads_again(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    """Row 11: wire -> F -> page AuthExpired -> renew -> retried read."""
    _wire(monkeypatch, _fails(WireFallbackReason.SIGNED_OUT_SHELL))
    answers: list[CreditsInfo | Exception] = [
        AuthExpiredError("signed out"),
        CreditsInfo(credits=9),
    ]

    class Page(_Page):
        async def get_credits(self) -> CreditsInfo:
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(credits, "FlowApiClient", Page)
    renewed: list[Path] = []

    async def renew(profile_dir: Path, *, source: str) -> Any:
        renewed.append(profile_dir)
        return type("R", (), {"renewed": True, "result": "renewed"})()

    monkeypatch.setattr(credits, "renew_profile", renew)

    result = await credits._fetch(_meta(), refresh=True)  # pyright: ignore[reportPrivateUsage]

    assert result["credits"] == 9
    assert len(renewed) == 1


async def test_the_kill_switch_reads_no_cookie_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    """Row 17 / G7."""
    monkeypatch.setenv("GFLOW_CLI_WIRE_READS", "0")
    reset_settings()
    seen = _wire(monkeypatch, _ok)

    result = await credits._fetch_once(_meta())  # pyright: ignore[reportPrivateUsage]

    assert result["credits"] == 11
    assert seen == []


@pytest.mark.parametrize(
    ("platform", "default"), [("darwin", False), ("win32", True), ("linux", True)]
)
def test_the_wire_defaults_off_on_macos_only(
    monkeypatch: pytest.MonkeyPatch, platform: str, default: bool
) -> None:
    """Row 18: browser_cookie3 asks the Keychain on macOS, possibly on every read."""
    monkeypatch.delenv("GFLOW_CLI_WIRE_READS", raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    reset_settings()

    assert get_settings().wire_reads is default


async def test_a_profile_outside_home_raises_before_any_cookie_read(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page], tmp_path: Path
) -> None:
    """Row 19: the real wire, a real boundary check; the security error is not a fallback."""
    import browser_cookie3  # pyright: ignore[reportMissingTypeStubs]

    def never(**_k: Any) -> Any:
        raise AssertionError("cookie jar read outside GFLOW_CLI_HOME")

    monkeypatch.setattr(browser_cookie3, "chrome", never)
    meta = _meta()
    outside = ProfileMeta(
        name=meta.name,
        profile_dir=tmp_path / "outside",
        cookies_present=True,
        last_used_at=None,
        is_default=True,
    )

    with pytest.raises(SecurityError):
        await credits._fetch_once(outside)  # pyright: ignore[reportPrivateUsage]
    assert page.built == 0


@pytest.mark.parametrize(
    "error", [RateLimitError("HTTP 429", status=429), NetworkError("HTTP 503", status=503)]
)
async def test_back_pressure_surfaces_and_never_loads_the_page(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page], error: Exception
) -> None:
    """Row 29: the page would send the same RPCs to a throttled account."""

    async def throttled(_p: Path) -> CreditsInfo:
        raise error

    _wire(monkeypatch, throttled)

    with pytest.raises(type(error)):
        await credits._fetch_once(_meta())  # pyright: ignore[reportPrivateUsage]
    assert page.built == 0


async def test_a_throttled_profile_never_builds_the_browser_on_later_reads(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    """Review #1: the real wire, a 429, then a SECOND read. Both raise the typed error; the
    page client is never built and the second read sends nothing."""
    import functools

    import httpx

    posts: list[int] = []

    def flow(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text='{"SNlM0e":"AFAKE","FdrFJe":"-1","cfb2h":"b"}')
        posts.append(1)
        return httpx.Response(429, headers={"retry-after": "30"})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(real, transport=httpx.MockTransport(flow))
    )
    monkeypatch.setattr(flow_wire, "read_cookies_for", lambda _p: [("SID", "fake")])
    monkeypatch.setattr(flow_wire, "profile_last_version", lambda _p: "150.0.1.2")

    for _ in range(2):
        with pytest.raises(RateLimitError):
            await credits._fetch_once(_meta())  # pyright: ignore[reportPrivateUsage]
    assert page.built == 0
    assert posts == [1]


# --- a signed-out mark is lifted once the session is known good again (fix to #1236) ---------


def _mark_signed_out(meta: ProfileMeta) -> None:
    flow_wire._signed_out[meta.profile_dir] = WireFallbackReason.SIGNED_OUT_SHELL  # pyright: ignore[reportPrivateUsage]
    flow_wire._tokens[meta.profile_dir] = flow_wire._Tokens("stale-at", None, None)  # pyright: ignore[reportPrivateUsage]


async def test_a_wire_refused_page_served_profile_costs_one_shell_get_per_process(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    """A page read does not prove the saved cookies changed, so it lifts nothing: repeated
    reads of a profile the wire refuses send ONE shell GET per process, not one per read."""
    import functools

    import httpx

    meta = _meta()
    meta.profile_dir.mkdir(parents=True, exist_ok=True)
    gets: list[httpx.Request] = []

    def flow(request: httpx.Request) -> httpx.Response:
        gets.append(request)
        return httpx.Response(200, text="<html>signed out</html>")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(real, transport=httpx.MockTransport(flow))
    )
    monkeypatch.setattr(flow_wire, "read_cookies_for", lambda _p: [("SID", "fake-sid")])
    monkeypatch.setattr(flow_wire, "profile_last_version", lambda _p: "150.0.1.2")

    for _ in range(3):
        assert (await credits._fetch_once(meta))["credits"] == 11  # pyright: ignore[reportPrivateUsage]

    assert page.built == 3
    assert len(gets) == 1


async def test_a_failed_page_read_keeps_the_signed_out_mark(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    meta = _meta()
    _mark_signed_out(meta)
    page.result = AuthExpiredError("signed out")

    with pytest.raises(AuthExpiredError):
        await credits._fetch_once(meta)  # pyright: ignore[reportPrivateUsage]

    assert meta.profile_dir in flow_wire._signed_out  # pyright: ignore[reportPrivateUsage]


async def test_a_renewed_profile_has_its_signed_out_mark_lifted_before_the_retry(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    """After `--refresh` renews it, the retried read tries the wire once more."""
    meta = _meta()
    _mark_signed_out(meta)
    page.result = AuthExpiredError("signed out")
    tried: list[Path] = []

    async def wire(profile_dir: Path) -> CreditsInfo:
        tried.append(profile_dir)
        if profile_dir in flow_wire._signed_out:  # pyright: ignore[reportPrivateUsage]
            raise WireReadUnavailable(WireFallbackReason.DISABLED)
        return CreditsInfo(credits=5)

    monkeypatch.setattr(flow_wire, "read_credits", wire)

    async def renew(profile_dir: Path, *, source: str) -> Any:
        return type("R", (), {"renewed": True, "result": "renewed"})()

    monkeypatch.setattr(credits, "renew_profile", renew)

    result = await credits._fetch(meta, refresh=True)  # pyright: ignore[reportPrivateUsage]

    assert result["credits"] == 5  # the wire answered the retry, not the browser
    assert meta.profile_dir not in flow_wire._signed_out  # pyright: ignore[reportPrivateUsage]


async def test_a_page_read_never_lifts_a_throttle_window(
    monkeypatch: pytest.MonkeyPatch, page: type[_Page]
) -> None:
    meta = _meta()
    flow_wire._throttled[meta.profile_dir] = (1e18, 429)  # pyright: ignore[reportPrivateUsage]
    _wire(monkeypatch, _fails(WireFallbackReason.NETWORK))

    await credits._fetch_once(meta)  # pyright: ignore[reportPrivateUsage]

    assert meta.profile_dir in flow_wire._throttled  # pyright: ignore[reportPrivateUsage]
