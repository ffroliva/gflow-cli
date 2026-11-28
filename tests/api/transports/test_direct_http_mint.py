"""The mint adapter: a mint-only page lock, four attempts in flight, quiesce, re-arm, discard."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from structlog.testing import capture_logs

from gflow_cli.api import flow_wire
from gflow_cli.api.image import GenerateImageRequest
from gflow_cli.api.submit_policy import MAX_IN_FLIGHT, DirectAttemptEnded
from gflow_cli.api.transports.base import SupportsQuiesce
from gflow_cli.api.transports.batchexecute import UNUSUAL_ACTIVITY_REASON
from gflow_cli.api.transports.direct_http import DirectHttpTransport
from gflow_cli.api.transports.direct_http._gateway import InFlightGate
from gflow_cli.api.transports.direct_http._mint import PageTokenSource
from gflow_cli.config import reset_settings
from gflow_cli.errors import ContentPolicyError, WafRejectionError
from tests.api.transports.test_migrated_images import image_payload

_SHELL_HTML = '<html>"SNlM0e":"at-x","FdrFJe":"sid","cfb2h":"boq_x"</html>'
_PROJECT = "https://flow.google.com/project/proj-123"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    flow_wire.reset_state()
    monkeypatch.setattr(flow_wire, "getproxies", dict)
    monkeypatch.setattr(
        "gflow_cli.api.transports.direct_http._images.build_ogiz0b_body",
        MagicMock(return_value="f.req=mock_body&at=at-x&"),
    )
    yield
    flow_wire.reset_state()
    reset_settings()


def _reply() -> str:
    frame = [["wrb.fr", "ogiZ0b", json.dumps(image_payload()), None, None, None, "generic"]]
    return f")]}}'\n{json.dumps(frame)}"


def _refusal() -> str:
    frame = [["wrb.fr", "ogiZ0b", None, None, None, [3, None, [[UNUSUAL_ACTIVITY_REASON]]], "g"]]
    return f")]}}'\n{json.dumps(frame)}"


def _page(url: str = _PROJECT) -> MagicMock:
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock(side_effect=lambda target, **_: setattr(page, "url", target))
    page.close = AsyncMock()
    page.context = MagicMock()
    page.context.cookies = AsyncMock(
        return_value=[{"name": "SID", "value": "sid", "domain": ".google.com", "path": "/"}]
    )
    return page


class _Posts:
    """An httpx handler that holds every POST until ``release`` is set."""

    def __init__(self, *replies: str) -> None:
        self.now = 0
        self.peak = 0
        self.total = 0
        self.release = asyncio.Event()
        self._replies = list(replies)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=_SHELL_HTML)
        self.now += 1
        self.total += 1
        self.peak = max(self.peak, self.now)
        try:
            await self.release.wait()
        finally:
            self.now -= 1
        return httpx.Response(200, text=self._replies.pop(0) if self._replies else _reply())


class FakeTokenSource:
    """A ``RecaptchaTokenSource`` with no browser behind it."""

    def __init__(self) -> None:
        self.actions: list[str] = []

    def for_project(self, project_id: str) -> FakeTokenSource:
        return self

    async def mint(self, action: str) -> str:
        self.actions.append(action)
        return "fake-token"

    def discard(self) -> None:
        pass


def _transport(tmp_path: Path, posts: _Posts, page: MagicMock | None = None) -> DirectHttpTransport:
    t = DirectHttpTransport(http_transport=httpx.MockTransport(posts))
    t._setup_done = True
    t._profile_dir = tmp_path
    t._page = page or _page()
    t.park_deferred_page = AsyncMock()  # type: ignore[method-assign]
    return t


def _generate(t: DirectHttpTransport, project_id: str = "proj-123") -> Any:
    return asyncio.create_task(
        t.generate_images(project_id=project_id, request=GenerateImageRequest(prompt="x"))
    )


async def _until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), timeout)


def _record_minter(monkeypatch: pytest.MonkeyPatch, delay: float = 0.0) -> dict[str, Any]:
    """Patch the minter: records the page of every mint and the peak of concurrent mints."""
    seen: dict[str, Any] = {"pages": [], "now": 0, "peak": 0}

    class Minter:
        def __init__(self, page: Any, **_: Any) -> None:
            self._page = page

        async def mint(self, action: str) -> str:
            seen["now"] += 1
            seen["peak"] = max(seen["peak"], seen["now"])
            await asyncio.sleep(delay)
            seen["now"] -= 1
            seen["pages"].append(self._page)
            return "tok"

    monkeypatch.setattr("gflow_cli.api.transports.direct_http._mint.TokenMinter", Minter)
    return seen


@pytest.mark.asyncio
async def test_lock_spans_mint_only_posts_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    minted = _record_minter(monkeypatch, delay=0.01)
    posts = _Posts()
    t = _transport(tmp_path, posts)
    jobs = [_generate(t), _generate(t)]
    await _until(lambda: posts.now == 2)  # both POSTs in flight at once
    posts.release.set()
    results = await asyncio.gather(*jobs)
    assert [r[0].transport for r in results] == ["direct_http", "direct_http"]
    assert minted["peak"] == 1  # the mints never overlapped
    assert posts.peak == 2


@pytest.mark.asyncio
async def test_at_most_four_posts_in_flight(tmp_path: Path) -> None:
    posts = _Posts()
    t = _transport(tmp_path, posts)
    t._token_source = FakeTokenSource()  # type: ignore[assignment]
    jobs = [_generate(t) for _ in range(8)]
    await _until(lambda: posts.now == MAX_IN_FLIGHT)
    await asyncio.sleep(0.05)
    assert posts.now == MAX_IN_FLIGHT == 4
    posts.release.set()
    results = await asyncio.gather(*jobs)
    assert all(r[0].transport == "direct_http" for r in results)
    assert posts.total == 8
    assert posts.peak == 4


@pytest.mark.asyncio
async def test_concurrency_setting_cannot_raise_direct_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_CLI_CONCURRENCY", "16")
    reset_settings()
    posts = _Posts()
    t = _transport(tmp_path, posts)
    t._token_source = FakeTokenSource()  # type: ignore[assignment]
    jobs = [_generate(t) for _ in range(8)]
    await _until(lambda: posts.now == 4)
    await asyncio.sleep(0.05)
    assert posts.now == 4
    posts.release.set()
    await asyncio.gather(*jobs)
    assert posts.peak == 4


@pytest.mark.asyncio
async def test_start_rate_warning_logged_not_blocking() -> None:
    clock = [1000.0]
    gate = InFlightGate(clock=lambda: clock[0])
    entered = 0
    with capture_logs() as logs:
        for _ in range(12):
            async with gate.slot():
                entered += 1
        clock[0] += 200.0  # a new window
        for _ in range(9):
            async with gate.slot():
                entered += 1
    assert entered == 21  # nothing waited or refused
    warnings = [e for e in logs if e["event"] == "direct.start_rate_high"]
    assert [(w["count"], w["window_s"], w["log_level"]) for w in warnings] == [
        (9, 120.0, "warning"),
        (9, 120.0, "warning"),
    ]


@pytest.mark.asyncio
async def test_quiesce_blocks_new_starts_and_waits_for_in_flight(tmp_path: Path) -> None:
    posts = _Posts()
    t = _transport(tmp_path, posts)
    t._token_source = FakeTokenSource()  # type: ignore[assignment]
    assert isinstance(t, SupportsQuiesce)
    first = _generate(t)
    await _until(lambda: posts.now == 1)

    entered, leave = asyncio.Event(), asyncio.Event()

    async def drain() -> None:
        async with t.quiesce():
            entered.set()
            await leave.wait()

    quiescing = asyncio.create_task(drain())
    await asyncio.sleep(0.05)
    assert not entered.is_set()  # the in-flight attempt is still running
    second = _generate(t)
    posts.release.set()
    await first
    await _until(entered.is_set)
    await asyncio.sleep(0.05)
    assert posts.total == 1  # no new attempt started while quiesced
    leave.set()
    await asyncio.gather(quiescing, second)
    assert posts.total == 2


@pytest.mark.asyncio
async def test_rearm_opens_new_page_after_wall_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    minted = _record_minter(monkeypatch)
    pool_page, second, third = _page(), _page("about:blank"), _page("about:blank")
    pool_page.context.new_page = AsyncMock(return_value=second)
    second.context.new_page = AsyncMock(return_value=third)
    clock = [5000.0]
    source = PageTokenSource(pool_page, lock=asyncio.Lock(), clock=lambda: clock[0])
    tokens = source.for_project("proj-123")

    await tokens.mint("IMAGE_GENERATION")
    clock[0] += 300.0
    await tokens.mint("IMAGE_GENERATION")
    clock[0] += 601.0
    await tokens.mint("IMAGE_GENERATION")
    assert minted["pages"] == [pool_page, pool_page, second]
    assert second.goto.await_args.args[0] == _PROJECT
    pool_page.close.assert_not_awaited()  # the pool page belongs to the client

    clock[0] += 601.0
    await tokens.mint("IMAGE_GENERATION")
    assert minted["pages"][-1] is third
    second.close.assert_awaited_once()  # a page the source opened is closed on re-arm


@pytest.mark.asyncio
async def test_refusal_discards_page_next_mint_uses_new_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    minted = _record_minter(monkeypatch)
    posts = _Posts(_refusal())
    posts.release.set()
    pool_page, fresh = _page(), _page("about:blank")
    pool_page.context.new_page = AsyncMock(return_value=fresh)
    t = _transport(tmp_path, posts, pool_page)

    with pytest.raises(WafRejectionError):
        await _generate(t)
    images = await _generate(t)

    assert images[0].transport == "direct_http"
    assert minted["pages"] == [pool_page, fresh]
    assert fresh.goto.await_args.args[0] == _PROJECT


@pytest.mark.asyncio
async def test_one_page_serves_two_projects_without_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    minted = _record_minter(monkeypatch)
    page = _page()
    source = PageTokenSource(page, lock=asyncio.Lock())
    await source.for_project("proj-123").mint("IMAGE_GENERATION")
    await source.for_project("proj-456").mint("IMAGE_GENERATION")
    assert minted["pages"] == [page, page]
    page.goto.assert_not_awaited()


def _content_refusal() -> str:
    reasons = [["PUBLIC_ERROR_UNSAFE_CONTENT"]]
    frame = [["wrb.fr", "ogiZ0b", None, None, None, [3, None, reasons], "g"]]
    return f")]}}'\n{json.dumps(frame)}"


@pytest.mark.asyncio
async def test_quiesce_holds_attempts_queued_on_a_slot(tmp_path: Path) -> None:
    posts = _Posts()
    t = _transport(tmp_path, posts)
    source = FakeTokenSource()
    t._token_source = source  # type: ignore[assignment]
    jobs = [_generate(t) for _ in range(MAX_IN_FLIGHT)]
    await _until(lambda: posts.now == MAX_IN_FLIGHT)
    queued = _generate(t)  # waits on a slot, past the open check
    await asyncio.sleep(0.05)

    entered, leave = asyncio.Event(), asyncio.Event()

    async def drain() -> None:
        async with t.quiesce():
            entered.set()
            await leave.wait()

    quiescing = asyncio.create_task(drain())
    await asyncio.sleep(0.01)
    posts.release.set()
    await asyncio.gather(*jobs)
    await _until(entered.is_set)
    await asyncio.sleep(0.05)
    assert (len(source.actions), posts.total) == (MAX_IN_FLIGHT, MAX_IN_FLIGHT)
    leave.set()
    await asyncio.gather(quiescing, queued)
    assert posts.total == MAX_IN_FLIGHT + 1


@pytest.mark.asyncio
async def test_nested_quiesce_keeps_gate_closed() -> None:
    gate = InFlightGate()
    started = asyncio.Event()

    async def attempt() -> None:
        async with gate.slot():
            started.set()

    async with gate.quiesce():
        async with gate.quiesce():
            pass
        waiting = asyncio.create_task(attempt())
        await asyncio.sleep(0.05)
        assert not started.is_set()  # the outer holder still has the gate closed
    await asyncio.wait_for(waiting, 1.0)
    assert started.is_set()


@pytest.mark.asyncio
async def test_failed_mint_after_rearm_does_not_rearm_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    minted = _record_minter(monkeypatch)
    pool_page, fresh = _page(), _page("about:blank")
    pool_page.context.new_page = AsyncMock(return_value=fresh)
    fresh.context.new_page = AsyncMock(return_value=_page("about:blank"))
    clock = [5000.0]
    source = PageTokenSource(pool_page, lock=asyncio.Lock(), clock=lambda: clock[0])
    tokens = source.for_project("proj-123")
    await tokens.mint("IMAGE_GENERATION")
    clock[0] += 601.0
    fresh.goto = AsyncMock(side_effect=RuntimeError("navigation failed"))
    with pytest.raises(DirectAttemptEnded):
        await tokens.mint("IMAGE_GENERATION")
    fresh.goto = AsyncMock(side_effect=lambda target, **_: setattr(fresh, "url", target))
    await tokens.mint("IMAGE_GENERATION")
    assert minted["pages"] == [pool_page, fresh]
    fresh.context.new_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_character_read_uses_rearmed_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.api.transports.test_direct_http import _project_with

    _record_minter(monkeypatch)
    read = AsyncMock(return_value=_project_with("Skipper"))
    monkeypatch.setattr("gflow_cli.api.transports.direct_http._images.read_project", read)
    posts = _Posts(_refusal())
    posts.release.set()
    pool_page, fresh = _page(), _page("about:blank")
    pool_page.context.new_page = AsyncMock(return_value=fresh)
    t = _transport(tmp_path, posts, pool_page)
    with pytest.raises(WafRejectionError):
        await _generate(t)  # discards the pool page; the next attempt re-arms
    await _generate(t)  # re-arms during its mint; the read before it ran on the pool page
    request = GenerateImageRequest(
        prompt="x", reference_entities=("entity-0",), reference_entity_names=("Skipper",)
    )
    await t.generate_images(project_id="proj-123", request=request)
    assert read.await_args is not None
    assert read.await_args.args[0] is fresh


@pytest.mark.asyncio
async def test_content_refusal_keeps_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    minted = _record_minter(monkeypatch)
    posts = _Posts(_content_refusal())
    posts.release.set()
    pool_page = _page()
    pool_page.context.new_page = AsyncMock(return_value=_page("about:blank"))
    t = _transport(tmp_path, posts, pool_page)
    with pytest.raises(ContentPolicyError):
        await _generate(t)
    await _generate(t)
    assert minted["pages"] == [pool_page, pool_page]
    pool_page.context.new_page.assert_not_awaited()
