"""Speed pin: the ``page`` fixture reuses one Chromium per module, not one per test."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

_BROWSERS: list[object] = []


async def test_first_page(page: Page) -> None:
    _BROWSERS.append(page.context.browser)


async def test_second_page(page: Page) -> None:
    _BROWSERS.append(page.context.browser)


async def test_third_page_and_one_browser_launched(page: Page) -> None:
    _BROWSERS.append(page.context.browser)
    assert len({id(b) for b in _BROWSERS}) == 1


async def test_pages_do_not_share_state(page: Page) -> None:
    await page.goto("about:blank")
    assert await page.evaluate("window.name") == ""
    await page.evaluate("window.name = 'dirty'")


async def test_pages_do_not_share_state_again(page: Page) -> None:
    await page.goto("about:blank")
    assert await page.evaluate("window.name") == ""
