"""Per-worker Page pool tests for FlowApiClient.

Asserts: __aenter__ opens N Pages, checkout/checkin invariants hold,
parallel checkouts can hold N distinct Pages simultaneously, and Pages
return to the pool during backoff sleeps.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.config import Settings

_STRATEGY_STUBS_XFAIL = pytest.mark.xfail(
    reason="strategy stubs raise NotImplementedError; resolved in Phase B",
    strict=False,
)


@pytest.fixture
def settings_n4(tmp_path: Path) -> Settings:
    return Settings(concurrency=4, profile="t", output_dir=tmp_path)


@pytest.fixture
def fake_context() -> MagicMock:
    """A MagicMock BrowserContext whose ``new_page`` returns distinct Pages."""
    ctx = MagicMock()
    pages = [MagicMock(name=f"Page{i}") for i in range(16)]
    for p in pages:
        # Page.goto is awaited inside __aenter__; make it an AsyncMock.
        p.goto = AsyncMock()
    ctx.pages = []  # empty initially
    counter = {"i": 0}

    async def _new_page() -> MagicMock:
        i = counter["i"]
        counter["i"] += 1
        return pages[i]

    ctx.new_page = AsyncMock(side_effect=_new_page)
    ctx.close = AsyncMock()
    return ctx


@_STRATEGY_STUBS_XFAIL
@pytest.mark.asyncio
async def test_aenter_opens_n_pages(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """N=4 → 4 Pages opened on __aenter__."""
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        async with client:
            assert fake_context.new_page.await_count == 4
            assert client._page_queue is not None
            assert client._page_queue.qsize() == 4


@_STRATEGY_STUBS_XFAIL
@pytest.mark.asyncio
async def test_checkout_checkin_preserves_identity_and_fifo(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """Checkout returns slot-0 first (FIFO); checkin + re-checkout returns the
    SAME Page object — proves identity is preserved across the queue round-trip
    (not just the qsize delta)."""
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        async with FlowApiClient(profile_dir=tmp_path, settings=settings_n4) as client:
            page = await client._checkout_page()
            assert page is client._pages[0]  # FIFO: slot 0 first
            assert client._page_queue is not None
            qsize_before = client._page_queue.qsize()
            client._checkin_page(page)
            assert client._page_queue.qsize() == qsize_before + 1
            # After full round, all 4 pages should cycle through and the original
            # slot-0 Page should re-appear (FIFO across 4 cycles).
            seen = [await client._checkout_page() for _ in range(4)]
            assert seen[-1] is page  # slot-0 came back last after the cycle


@_STRATEGY_STUBS_XFAIL
@pytest.mark.asyncio
async def test_aenter_reuses_existing_first_page(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """When ``launch_persistent_context`` opens a default Page (the common case),
    __aenter__ MUST reuse it as slot 0 — total Pages = N, not N+1."""
    existing_page = MagicMock(name="ExistingDefaultPage")
    existing_page.goto = AsyncMock()
    fake_context.pages = [existing_page]
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        async with FlowApiClient(profile_dir=tmp_path, settings=settings_n4) as client:
            # N=4, one already existed → open exactly 3 more (NOT 4).
            assert fake_context.new_page.await_count == 3
            assert client._pages[0] is existing_page
            assert len(client._pages) == 4


@_STRATEGY_STUBS_XFAIL
@pytest.mark.asyncio
async def test_parallel_checkouts_hold_n_distinct_pages(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """N=4 concurrent checkouts must each get a distinct Page (no contention)."""
    gate = asyncio.Event()
    held: list[MagicMock] = []

    async def hold_then_release(client: FlowApiClient) -> None:
        page = await client._checkout_page()
        held.append(page)
        await gate.wait()
        client._checkin_page(page)

    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        async with FlowApiClient(profile_dir=tmp_path, settings=settings_n4) as client:
            tasks = [asyncio.create_task(hold_then_release(client)) for _ in range(4)]
            # Wait until all 4 have checked out a Page
            for _ in range(50):
                if len(held) == 4:
                    break
                await asyncio.sleep(0.01)
            assert len(held) == 4
            assert len({id(p) for p in held}) == 4  # all distinct
            assert client._page_queue is not None
            assert client._page_queue.qsize() == 0  # pool exhausted
            gate.set()
            await asyncio.gather(*tasks)
            assert client._page_queue.qsize() == 4  # all returned


@_STRATEGY_STUBS_XFAIL
@pytest.mark.asyncio
async def test_aenter_with_concurrency_1_opens_one_page(
    tmp_path: Path, fake_context: MagicMock
) -> None:
    """Default N=1 retains single-Page behavior."""
    settings = Settings(concurrency=1, profile="t", output_dir=tmp_path)
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        async with FlowApiClient(profile_dir=tmp_path, settings=settings) as client:
            assert fake_context.new_page.await_count == 1
            assert client._page_queue is not None
            assert client._page_queue.qsize() == 1


@_STRATEGY_STUBS_XFAIL
@pytest.mark.asyncio
async def test_aexit_closes_browser_context(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """__aexit__ closes the BrowserContext. (BrowserContext.close closes its
    child Pages implicitly per Playwright's API contract — no per-Page close
    call is needed.)"""
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        async with client:
            assert len(client._pages) == 4
        fake_context.close.assert_awaited()
        pw.stop.assert_awaited()


@_STRATEGY_STUBS_XFAIL
@pytest.mark.asyncio
async def test_aexit_resets_pool_even_when_close_raises(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """Spec § 3.2: cleanup MUST run even if context.close() raises. The H6 fix
    (logger.warning instead of silent pass) preserves the finally-block reset
    of pool fields. A regression that moves the reset OUT of finally would
    leave the next client instance with dangling Pages."""
    fake_context.close = AsyncMock(side_effect=RuntimeError("simulated browser crash"))
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        async with client:
            assert client._page_queue is not None
        # close() raised, but cleanup ran in `finally`:
        assert client._pages == []
        assert client._page_queue is None
        assert client._page is None
        assert client._context is None
        assert client._pw is None
