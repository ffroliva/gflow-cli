"""Shared fixtures for transport unit tests.

`detect_ui_mode` polls the DOM for up to several seconds before defaulting to
classic (so the live driver doesn't race the agentic composer's render — see
`drivers/factory.py`). Transport unit tests drive heavily-mocked pages that
expose no cohort signal, which would make every `get_ui_driver` call sit through
the full poll window. Collapse the window to zero here so those tests stay fast;
detection logic still runs (one probe pass), and `test_factory.py` exercises the
real polling behaviour via explicit `timeout_s` arguments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from gflow_cli.api.transports.drivers import factory

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator

    from playwright.async_api import Browser, Page


@pytest.fixture(autouse=True)
def _fast_ui_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory, "_DETECT_TIMEOUT_S", 0.0)
    monkeypatch.setattr(factory, "_DETECT_POLL_INTERVAL_S", 0.0)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run every test that takes ``page`` on its module's event loop.

    The Chromium process below is shared by a module, and a Playwright browser is bound
    to the loop that launched it, so the tests using it must run on that same loop.
    Put first so it wins over a test's own bare ``@pytest.mark.asyncio``.
    """
    for item in items:
        if "page" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.asyncio(loop_scope="module"), append=False)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _browser() -> AsyncIterator[Browser]:
    """One headless Chromium per module (per xdist worker), or skip if not installed.

    Launching a browser per test cost ~1 s each (~110 tests); a fresh context per test
    keeps cookies, storage and pages isolated, which is all these tests rely on.
    """
    playwright_api = pytest.importorskip("playwright.async_api")
    try:
        async with playwright_api.async_playwright() as pw:
            try:
                browser = await pw.chromium.launch()
            except Exception as exc:  # pragma: no cover — environment-dependent
                pytest.skip(f"chromium unavailable: {type(exc).__name__}: {exc}")
            try:
                yield browser
            finally:
                await browser.close()
    except NotImplementedError as exc:  # pragma: no cover — no subprocess loop
        pytest.skip(f"playwright cannot start here: {exc}")


@pytest_asyncio.fixture(loop_scope="module")
async def page(_browser: Browser) -> AsyncIterator[Page]:
    """A fresh page in its own context on the module's shared headless Chromium.

    Shared because three files drive production selectors against captured markup and
    each had grown its own byte-identical copy (council D14). Named `page` so no test
    signature changes; a file needing different launch options can still shadow it.

    Why a REAL browser and not a fake: a fake answers whatever it was written to
    answer, so it cannot settle what a CSS engine actually does with the markup — see
    `test_agent_only_composer.py`'s module docstring for the worked example.
    """
    ctx = await _browser.new_context()
    try:
        yield await ctx.new_page()
    finally:
        await ctx.close()
