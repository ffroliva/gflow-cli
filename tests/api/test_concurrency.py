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
    ctx.add_init_script = AsyncMock()
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


@pytest.mark.asyncio
async def test_aenter_partial_failure_tears_down_browser(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """If __aenter__ fails AFTER launching the persistent context, the browser
    MUST be torn down. __aexit__ is NOT invoked when __aenter__ raises, so an
    unguarded failure would orphan the chrome process and lock the profile dir
    (regression: next run spirals into about:blank spam + TargetClosedError).
    The partial-setup guard owns cleanup here."""
    fake_context.add_init_script = AsyncMock(side_effect=RuntimeError("boom mid-setup"))
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        with pytest.raises(RuntimeError, match="boom mid-setup"):
            await client.__aenter__()
        # The launched context + driver were closed even though __aexit__ never ran:
        fake_context.close.assert_awaited()
        pw.stop.assert_awaited()
        # Fields reset to a clean state so a retry/reuse starts fresh.
        assert client._context is None
        assert client._pw is None
        assert client._page_queue is None


@pytest.mark.asyncio
async def test_close_failure_force_closes_browser_before_stop(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """#293: context.close() is the graceful ask for system-Chrome to exit.
    When it fails, pw.stop() alone kills only the Node driver and the detached
    chrome tree survives holding the profile dir — teardown must force-close
    the browser before stopping the driver."""
    fake_context.close = AsyncMock(side_effect=RuntimeError("simulated wedged close"))
    fake_context.browser.close = AsyncMock()
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        async with client:
            pass
        fake_context.browser.close.assert_awaited()
        pw.stop.assert_awaited()
        assert client._context is None


@pytest.mark.asyncio
async def test_close_hang_times_out_and_force_closes(
    tmp_path: Path,
    settings_n4: Settings,
    fake_context: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#293: an UNBOUNDED context.close() can hang teardown forever on an
    errored page; it must be deadline-bounded, then fall back to force-close."""
    import gflow_cli.api._engine as engine_mod

    monkeypatch.setattr(engine_mod, "_CONTEXT_CLOSE_TIMEOUT_S", 0.05)

    async def _hang() -> None:
        await asyncio.Event().wait()

    fake_context.close = AsyncMock(side_effect=_hang)
    fake_context.browser.close = AsyncMock()
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        async with client:
            pass
        fake_context.browser.close.assert_awaited()
        pw.stop.assert_awaited()
        assert client._context is None


@pytest.mark.asyncio
async def test_force_close_runs_before_driver_stop(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """#293: the ORDER is the fix — the force-close must land while the Node
    driver is still alive; pw.stop() first would orphan the chrome tree."""
    order: list[str] = []
    fake_context.close = AsyncMock(side_effect=RuntimeError("wedged"))
    fake_context.browser.close = AsyncMock(side_effect=lambda: order.append("force_close"))
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock(side_effect=lambda: order.append("pw_stop"))
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        async with client:
            pass
        assert order == ["force_close", "pw_stop"]


@pytest.mark.asyncio
async def test_double_close_failure_still_stops_driver_and_resets(
    tmp_path: Path, settings_n4: Settings, fake_context: MagicMock
) -> None:
    """#293 last-resort branch: context.close AND browser.close both fail —
    teardown must not raise, must still stop the driver, and must reset the
    pool fields (the operator breadcrumb is the browser_teardown.force_close_failed
    log)."""
    fake_context.close = AsyncMock(side_effect=RuntimeError("wedged"))
    fake_context.browser.close = AsyncMock(side_effect=RuntimeError("also wedged"))
    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock()
        pw.chromium.launch_persistent_context = AsyncMock(return_value=fake_context)
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        async with client:
            pass
        pw.stop.assert_awaited()
        assert client._context is None
        assert client._pw is None


@pytest.mark.asyncio
async def test_launch_marker_message_raises_profile_locked_error(
    tmp_path: Path, settings_n4: Settings
) -> None:
    """The message-marker branch of _is_target_closed — real Playwright launch
    failures often arrive as a generically-named Error carrying only the
    'Target closed' marker text (the class-name branch is tested below)."""
    from gflow_cli.errors import ProfileLockedError

    class SomePlaywrightError(Exception):
        pass

    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock()
        pw.chromium.launch_persistent_context = AsyncMock(
            side_effect=SomePlaywrightError("Target closed")
        )
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        with pytest.raises(ProfileLockedError):
            await client.__aenter__()


@pytest.mark.asyncio
async def test_launch_target_closed_raises_profile_locked_error(
    tmp_path: Path, settings_n4: Settings
) -> None:
    """#293 DX half: a TargetClosedError at persistent-context LAUNCH almost
    always means a stale Chrome (crashed/leaked prior run) still holds the
    profile dir — surface it as ProfileLockedError with a remediation instead
    of falling through to 'Unexpected error.' exit 1."""
    from gflow_cli.errors import ProfileLockedError

    # Named exactly like Playwright's class but carrying NO marker message —
    # exercises the class-NAME branch of _is_target_closed in isolation (the
    # message-marker branch has its own test above).
    class TargetClosedError(Exception):
        pass

    with patch("gflow_cli.api.client.async_playwright") as mock_pw_factory:
        pw = MagicMock()
        pw.stop = AsyncMock()
        pw.chromium.launch_persistent_context = AsyncMock(
            side_effect=TargetClosedError("browser exited unexpectedly")
        )
        mock_pw_factory.return_value.start = AsyncMock(return_value=pw)
        client = FlowApiClient(profile_dir=tmp_path, settings=settings_n4)
        with pytest.raises(ProfileLockedError, match="holds it") as exc_info:
            await client.__aenter__()
        assert "stale Chrome" in (exc_info.value.remediation_hint or "")
        pw.stop.assert_awaited()  # partial-setup guard still tears down the driver
