"""Offline guards for the click post-mortem (#776).

The behaviour itself is proven in the browser by
``tests/e2e/test_click_attribution_bdd.py`` — Playwright's actionability gate is what
fails, and no mock can express it. These two cases are the opposite: they are about what
the helper must *not* do, and both are cheap to pin without a browser.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.errors import UiSelectorDriftError


class _Locator:
    """A locator that fails a click the way we ask, and reads back what we say.

    Deliberately a plain class, not ``MagicMock``: a mock answers every attribute with a
    truthy child, which is exactly how a guard silently stops guarding
    (memory ``magicmock-truthy-getattr-silences-guards``).
    """

    def __init__(self, *, raises: BaseException, state: dict[str, Any] | None = None) -> None:
        self._raises = raises
        self._state = state or {}

    async def click(self, **_: object) -> None:
        raise self._raises

    async def evaluate(self, _js: str) -> dict[str, Any]:
        return self._state


class _Page:
    """Just enough Page for the post-mortem: it is only asked for the agent chip."""

    def locator(self, _sel: str) -> Any:  # pragma: no cover - never reached here
        raise AssertionError("the post-mortem must read through the LOCATOR, not the page")


_HEALTHY = {
    "visible": True,
    "hidden_attr": False,
    "enabled": True,
    "hit_testable": True,
    "body_blocked": False,
    "occluder": None,
}


@pytest.fixture
def composer(monkeypatch: pytest.MonkeyPatch) -> MigratedComposer:
    """A composer whose agent-chip probe answers False, so it is never the cause."""
    monkeypatch.setattr(
        MigratedComposer, "_agent_chip_pressed", staticmethod(lambda _page: _false())
    )
    return MigratedComposer()


async def _false() -> bool:
    return False


@pytest.mark.asyncio
async def test_a_non_timeout_click_failure_is_not_reinterpreted(
    composer: MigratedComposer,
) -> None:
    """Only an actionability timeout becomes selector drift.

    A closed page, a detached frame or a navigation abort is a different failure with a
    different remedy. Converting those too would relabel every browser mishap as "Flow
    changed its frontend" and send users to file drift bugs about their own laptop.
    """
    boom = RuntimeError("Target page, context or browser has been closed")
    with pytest.raises(RuntimeError) as caught:
        await composer._click(  # noqa: SLF001
            _Page(), _Locator(raises=boom), named=".x", timeout=1000
        )
    assert caught.value is boom


@pytest.mark.asyncio
async def test_a_click_timeout_names_the_locator_first(composer: MigratedComposer) -> None:
    """The locator leads the message, ahead of anything variable-length.

    The queued MCP path raw-slices ``detail`` to 500 chars while the CLI path does not
    (``data/redaction.py``), so whatever both surfaces must always show has to come first.
    """
    error = await _drift(composer, state=_HEALTHY)
    assert error.detail is not None
    assert error.detail.index(".settings-trigger-button") < error.detail.index("did not accept")


@pytest.mark.asyncio
async def test_an_unreadable_element_still_reports_the_failure(
    composer: MigratedComposer,
) -> None:
    """A diagnostic may never replace the failure it was called to describe."""

    class _Unreadable(_Locator):
        async def evaluate(self, _js: str) -> dict[str, Any]:
            raise RuntimeError("Execution context was destroyed")

    with pytest.raises(UiSelectorDriftError) as caught:
        await composer._click(  # noqa: SLF001
            _Page(),
            _Unreadable(raises=PlaywrightTimeoutError("Timeout 5000ms exceeded")),
            named=".settings-trigger-button",
            timeout=5000,
        )
    detail = caught.value.detail or ""
    assert ".settings-trigger-button" in detail
    assert "could not be read back" in detail


@pytest.mark.asyncio
async def test_nothing_readable_wrong_is_reported_as_such(composer: MigratedComposer) -> None:
    """When every reading is healthy, say so — do not pick a cause.

    This is the countermeasure to #770, where a typed error named a "most likely" cause
    that was wrong for the reporting account. Three of Playwright's four conditions are
    eliminated here; naming the fourth as a *possibility* is honest, asserting it is not.
    """
    detail = (await _drift(composer, state=_HEALTHY)).detail or ""
    assert "visible, enabled and hit-testable" in detail
    for invented in ("agent mode", "covered by", "announcement", "changelog"):
        assert invented not in detail.lower(), f"invented {invented!r}: {detail}"


@pytest.mark.asyncio
async def test_the_occluder_report_is_bounded(composer: MigratedComposer) -> None:
    """A named occluder must not be able to crowd the message out.

    The JS caps the class list at three framework-prefixed tokens, so even a pathological
    CDK class soup leaves the 500-char MCP slice intact. Pinned here because the cap lives
    in a JS string that no type checker and no linter can see.
    """
    state = {**_HEALTHY, "hit_testable": False, "occluder": "div." + ".".join(["cdk-x"] * 3)}
    detail = (await _drift(composer, state=state)).detail or ""
    assert "div.cdk-x.cdk-x.cdk-x" in detail
    assert len(detail) < 500, f"a single occluder should not fill the MCP budget: {detail}"


@pytest.mark.asyncio
async def test_a_failed_hit_test_without_an_occluder_is_not_called_healthy(
    composer: MigratedComposer,
) -> None:
    """`elementFromPoint` can miss and name nothing — a zero-box or an out-of-document
    overlay. Falling through to the healthy branch would then claim hit-testable of an
    element that had just failed the hit test."""
    state = {**_HEALTHY, "hit_testable": False, "occluder": None}
    detail = (await _drift(composer, state=state)).detail or ""
    assert "no hit test" in detail
    assert "hit-testable at the moment" not in detail


async def _drift(composer: MigratedComposer, *, state: dict[str, Any]) -> UiSelectorDriftError:
    with pytest.raises(UiSelectorDriftError) as caught:
        await composer._click(  # noqa: SLF001
            _Page(),
            _Locator(raises=PlaywrightTimeoutError("Timeout 5000ms exceeded"), state=state),
            named=".settings-trigger-button",
            timeout=5000,
        )
    return caught.value
