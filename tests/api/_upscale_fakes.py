"""Fake Playwright page with a download menu, shared by the migrated-upscale tests.

The menu mirrors the measured DOM (2026-10-07): bare ``[role=menuitem]`` items that
differ only by text, in an order that is not stable across locales.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

MENU_SELECTOR = '[role="menuitem"]'
BATCHEXECUTE_URL = "https://flow.google.com/_/AiSandboxAngularFrontend/data/batchexecute"


class FakeItem:
    def __init__(
        self,
        text: str,
        *,
        disabled: bool = False,
        on_click: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.text = text
        self.disabled = disabled
        self.on_click = on_click
        self.clicked = False

    async def wait_for(self, **_: Any) -> None:
        return None

    async def is_disabled(self) -> bool:
        return self.disabled

    async def get_attribute(self, name: str) -> str | None:
        return "true" if self.disabled and name == "aria-disabled" else None

    async def click(self) -> None:
        self.clicked = True
        if self.on_click is not None:
            await self.on_click()


class _Absent:
    async def wait_for(self, **_: Any) -> None:
        raise PlaywrightTimeoutError("menu never opened")


class FakeTarget:
    """``page.locator(tile|download)`` — re-resolved on every action, like Playwright's."""

    def __init__(self, *, present: bool, what: str) -> None:
        self.present, self.what = present, what

    @property
    def first(self) -> FakeTarget:
        return self

    async def wait_for(self, **_: Any) -> None:
        if not self.present:
            raise PlaywrightTimeoutError(self.what)

    async def click(self) -> None:
        return None


class FakeMenu:
    """Stands in for ``page.locator('[role="menuitem"]')``."""

    def __init__(self, items: list[FakeItem]) -> None:
        self.items = items

    @property
    def first(self) -> FakeItem | _Absent:
        return self.items[0] if self.items else _Absent()

    def filter(self, *, has_text: re.Pattern[str]) -> FakeMenu:
        return FakeMenu([i for i in self.items if has_text.search(i.text)])

    async def count(self) -> int:
        return len(self.items)


def fake_page(
    menu: list[FakeItem],
    *,
    tile: bool = True,
    download: bool = True,
    captured_b64: str | None = None,
) -> MagicMock:
    """A page whose tile/download/menu lookups are driven by the arguments.

    ``captured_b64`` is what the video transport's ``window._videoCapturedBase64``
    poll returns. ``page.response_handlers`` holds the registered response hooks.
    """
    page = MagicMock()
    page.url = "https://flow.google.com/project/00000000-0000-4000-8000-000000000001"
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    handlers: list[Callable[[Any], Awaitable[None]]] = []
    page.response_handlers = handlers

    def _on(event: str, handler: Callable[[Any], Awaitable[None]]) -> None:
        if event == "response":
            handlers.append(handler)

    def _off(event: str, handler: Callable[[Any], Awaitable[None]]) -> None:
        if handler in handlers:
            handlers.remove(handler)

    page.on = MagicMock(side_effect=_on)
    page.remove_listener = MagicMock(side_effect=_off)

    async def _evaluate(expr: str, *_: Any) -> Any:
        return captured_b64 if "window._videoCapturedBase64" in expr else None

    page.evaluate = AsyncMock(side_effect=_evaluate)

    async def _wait_for_selector(sel: str, **_: Any) -> Any:
        # #957: the grid re-renders after it is found, so a handle resolved once is
        # stale by the time it is clicked. Only a lazily re-resolved Locator survives.
        handle = MagicMock()
        handle.click = AsyncMock(
            side_effect=PlaywrightError("ElementHandle.click: Element is not attached to the DOM")
        )
        return handle

    page.wait_for_selector = AsyncMock(side_effect=_wait_for_selector)

    def _locator(sel: str) -> Any:
        if "data-media-id" in sel:
            return FakeTarget(present=tile, what="no tile")
        if "download" in sel:
            return FakeTarget(present=download, what="no download button")
        assert sel == MENU_SELECTOR, sel
        return FakeMenu(menu)

    page.locator = MagicMock(side_effect=_locator)
    return page


async def emit_response(page: MagicMock, body: str, *, rpcid: str) -> None:
    res = MagicMock()
    res.url = f"{BATCHEXECUTE_URL}?rpcids={rpcid}"
    res.text = AsyncMock(return_value=body)
    for handler in list(page.response_handlers):
        await handler(res)
