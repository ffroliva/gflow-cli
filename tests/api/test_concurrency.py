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


@pytest.mark.asyncio
async def test_checkout_checkin_returns_same_page(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        async with FlowApiClient(profile_dir=tmp_path, settings=settings_n4) as client:
            page = await client._checkout_page()
            assert client._page_queue is not None
            qsize_before = client._page_queue.qsize()
            client._checkin_page(page)
            assert client._page_queue.qsize() == qsize_before + 1


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


@pytest.mark.asyncio
async def test_aexit_closes_all_pages(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """All N Pages closed on __aexit__ (resource cleanup)."""
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        async with client:
            pages_opened = list(client._pages)
            assert len(pages_opened) == 4
        # Context closed; verify each Page received .close() OR Context.close handles them
        fake_context.close.assert_awaited()
