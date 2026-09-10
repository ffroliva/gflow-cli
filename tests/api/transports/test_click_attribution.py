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

    The detail is truncated to 500 chars at the raise site (``data/redaction.py``), so
    whatever must survive that cut has to come before the occluder's class list.
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


# ---------------------------------------------------------------------------
# One case per reading the post-mortem can report.
#
# The e2e proves the BROWSER really produces these states; these prove the message
# for each one. They are here and not only there because CI's coverage run excludes
# `-m e2e`, so a branch exercised solely by the browser reads as dead code to
# SonarCloud's new-code gate — the exact way PR #777 went red at 70%.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"hidden_attr": True, "hit_testable": False}, "bare `hidden` attribute"),
        ({"visible": False, "hit_testable": False}, "not rendered"),
        ({"enabled": False}, "it is disabled"),
        ({"body_blocked": True}, "accepting no pointer events at all"),
    ],
    ids=["hidden", "not-rendered", "disabled", "body-blocked"],
)
@pytest.mark.asyncio
async def test_each_readable_condition_is_named(
    composer: MigratedComposer, override: dict[str, Any], expected: str
) -> None:
    detail = (await _drift(composer, state={**_HEALTHY, **override})).detail or ""
    assert expected in detail, detail
    # Whatever fired, the locator is still the first thing the reader sees.
    assert ".settings-trigger-button" in detail


@pytest.mark.asyncio
async def test_agent_mode_leads_the_message_when_the_chip_is_pressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#752 finding #7's cause, and the only one here with a user action attached.

    It is reported FIRST because it is the only reading a user can act on directly, and
    because agent mode hides the trigger with a bare `hidden` that touches neither the
    body's pointer-events nor the hit test — nothing else in the probe would notice it.
    """

    async def _true(_page: object) -> bool:
        return True

    monkeypatch.setattr(MigratedComposer, "_agent_chip_pressed", staticmethod(_true))
    state = {**_HEALTHY, "hit_testable": False, "occluder": "div.cdk-overlay-backdrop"}
    detail = (await _drift(MigratedComposer(), state=state)).detail or ""
    assert "agent mode" in detail
    # Both facts are reported — the occluder is real too — but the actionable one leads.
    assert detail.index("agent mode") < detail.index("covered by")


@pytest.mark.asyncio
async def test_a_missing_reading_is_not_mistaken_for_health(
    composer: MigratedComposer,
) -> None:
    """An empty state dict must not read as "everything was fine".

    `locator.evaluate` returning a shape we did not expect (an older Chromium, a JS
    error swallowed into a partial object) would make every `.get()` falsy. Falling
    through to the healthy branch there would report "visible, enabled and hit-testable"
    about an element nothing was ever read from.
    """
    detail = (await _drift(composer, state={})).detail or ""
    assert "visible, enabled and hit-testable" not in detail, detail


@pytest.mark.asyncio
async def test_an_unrendered_element_reports_one_fact_once(composer: MigratedComposer) -> None:
    """ "Not rendered" and "answers no hit test" are the same fact, not two.

    `_CLICK_POSTMORTEM_JS` only calls `elementFromPoint` `if (box.width && box.height)`,
    so a zero-box element ALWAYS comes back `hit_testable: false, occluder: null` as well.
    Reported as independent readings that told the user the same thing twice in one
    sentence — the shape of over-reporting that makes a diagnostic harder to act on than
    a short one.
    """
    state = {**_HEALTHY, "visible": False, "hit_testable": False, "occluder": None}
    detail = (await _drift(composer, state=state)).detail or ""
    assert "not rendered" in detail
    assert "no hit test" not in detail


@pytest.mark.asyncio
async def test_disabled_and_blocked_are_reported_alongside_occlusion(
    composer: MigratedComposer,
) -> None:
    """Enabled-ness and a page-wide block are separate axes from occlusion.

    Chaining them onto the same `elif` ladder would hide a disabled control behind
    whatever covered it — two different remedies collapsed into one message.
    """
    state = {
        **_HEALTHY,
        "enabled": False,
        "body_blocked": True,
        "hit_testable": False,
        "occluder": "div.cdk-overlay-backdrop",
    }
    detail = (await _drift(composer, state=state)).detail or ""
    for fact in ("covered by", "it is disabled", "accepting no pointer events"):
        assert fact in detail, f"missing {fact!r}: {detail}"
