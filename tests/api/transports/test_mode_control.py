"""Unit tests for the robust agentic↔classic mode-control component.

A tiny fake composer state machine (media / agent / sidebar) drives the same
transitions the live round-trip proved, so the switch logic is verified without
a browser: aria-pressed is the source of truth, apps_spark_2 is never consulted,
and clicks are state-aware (never blind).
"""

from __future__ import annotations

import pytest

from gflow_cli.api.transports import mode_control as mc


class _FakeLocator:
    def __init__(self, page: _FakePage, sel: str) -> None:
        self._page = page
        self._sel = sel

    @property
    def first(self) -> _FakeLocator:
        return self

    async def count(self) -> int:
        return self._page.count(self._sel)

    async def get_attribute(self, name: str) -> str | None:
        return self._page.attr(self._sel, name)

    async def click(self, **_kw: object) -> None:
        self._page.click(self._sel)


class _FakePage:
    """Models Flow's composer: state in {media, agent, sidebar}.

    - media:  crop_* present; toggle present with aria-pressed=false.
    - agent:  no crop; toggle present with aria-pressed=true; expand available.
    - sidebar: no crop; no in-composer toggle; the sidebar X (close) present.
    """

    def __init__(self, state: str = "media") -> None:
        self.state = state
        self.clicks: list[str] = []

    def locator(self, sel: str) -> _FakeLocator:
        return _FakeLocator(self, sel)

    async def wait_for_timeout(self, _ms: int) -> None:
        return None

    def count(self, sel: str) -> int:
        if sel in mc._CROP_SELECTORS:
            return 1 if self.state == "media" else 0
        if sel == mc.AGENT_TOGGLE_SELECTOR:
            return 1 if self.state in ("media", "agent") else 0
        if sel == mc.SIDEBAR_CLOSE_SELECTOR:
            return 1 if self.state == "sidebar" else 0
        return 0

    def attr(self, sel: str, name: str) -> str | None:
        if sel == mc.AGENT_TOGGLE_SELECTOR and name == "aria-pressed":
            if self.state == "agent":
                return "true"
            if self.state == "media":
                return "false"
        return None

    def click(self, sel: str) -> None:
        self.clicks.append(sel)
        if sel == mc.AGENT_TOGGLE_SELECTOR:
            self.state = "agent" if self.state == "media" else "media"
        elif sel == mc.SIDEBAR_CLOSE_SELECTOR:
            # Closing the sidebar returns to the composer, still agent-on
            # (aria-pressed=true) — matches the live round-trip (03_after_close).
            self.state = "agent"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected"),
    [("media", "media"), ("agent", "agent"), ("stuck", "unknown")],
)
async def test_read_mode(state: str, expected: str) -> None:
    page = _FakePage(state)
    assert await mc.read_mode(page) == expected  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_ensure_media_noop_when_already_media() -> None:
    page = _FakePage("media")
    acted = await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    assert acted is False
    assert page.clicks == []  # no blind click
    assert page.state == "media"


@pytest.mark.asyncio
async def test_ensure_media_from_agent_toggles_off() -> None:
    page = _FakePage("agent")
    acted = await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    assert acted is True
    assert page.clicks == [mc.AGENT_TOGGLE_SELECTOR]
    assert page.state == "media"


@pytest.mark.asyncio
async def test_ensure_media_from_sidebar_closes_then_toggles() -> None:
    page = _FakePage("sidebar")
    acted = await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    assert acted is True
    # X first (sidebar → agent), then toggle off (agent → media).
    assert page.clicks == [mc.SIDEBAR_CLOSE_SELECTOR, mc.AGENT_TOGGLE_SELECTOR]
    assert page.state == "media"


@pytest.mark.asyncio
async def test_ensure_media_gives_up_when_nothing_actionable() -> None:
    page = _FakePage("stuck")
    acted = await mc.ensure_media_mode(page)  # type: ignore[arg-type]
    assert acted is False
    assert page.clicks == []
