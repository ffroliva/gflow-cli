"""The changelog modal must be cleared, provably, and never touched otherwise (#593).

Two layers, because they catch different things:

* **Real DOM** — the captured announcement markup driven by a headless Chromium.
  A mock cannot tell you whether ``[role='dialog']:has(a[href*='changelog']) button``
  actually resolves against Flow's markup; only a real CSS engine can. These tests
  need no account, no network and no credits, and skip when Chromium is absent.
* **Unit** — the decision logic (when Escape is allowed, what a failed dismissal
  returns), which is cheap to pin with mocks.

The #395 guard is the negative case: a healthy Flow dialog with no changelog anchor,
on a page that is *not* blocked, must come out completely untouched. That regression
sent a character generation out without ``entityContext`` and spent credits.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.api.transports.ui_automation import (
    OVERLAY_CLOSE_BUTTON_SELECTORS,
    UiAutomationTransport,
)

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator

    from playwright.async_api import Page

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "changelog_modal_page.html"

# The anchor the production cascade tries first. Named here so a rename in
# ui_automation.py that silently stops matching real markup fails this file.
_CHANGELOG_BUTTON = "[role='dialog']:has(a[href*='changelog']) button"

# A legitimate Flow surface: a dialog with no changelog anchor, on a page that is
# NOT blocked. This is the shape #395 destroyed — the character composer.
_HEALTHY_COMPOSER = """
<!doctype html><html><body style="pointer-events: auto">
  <div role="dialog" aria-label="composer">
    <a href="/fx/tools/flow/project/abc">Back to project</a>
    <textarea id="prompt">a cinematic portrait</textarea>
    <button type="button"><i class="google-symbols">close</i></button>
  </div>
</body></html>
"""


async def _body_pointer_events(page: Page) -> str:
    return await page.evaluate("() => getComputedStyle(document.body).pointerEvents")


@pytest.fixture
async def page() -> AsyncIterator[Page]:
    """A real headless Chromium page, or skip if the browser isn't installed."""
    playwright_api = pytest.importorskip("playwright.async_api")
    try:
        async with playwright_api.async_playwright() as pw:
            try:
                browser = await pw.chromium.launch()
            except Exception as exc:  # pragma: no cover — environment-dependent
                pytest.skip(f"chromium unavailable: {type(exc).__name__}: {exc}")
            ctx = await browser.new_context()
            new_page = await ctx.new_page()
            try:
                yield new_page
            finally:
                await ctx.close()
                await browser.close()
    except NotImplementedError as exc:  # pragma: no cover — no subprocess loop
        pytest.skip(f"playwright cannot start here: {exc}")


@pytest.mark.asyncio
class TestAgainstCapturedMarkup:
    """The production selectors, run against the announcement Flow actually served."""

    async def test_close_selector_matches_exactly_one_button(self, page: Page) -> None:
        """The changelog anchor resolves to exactly one button — not zero, not many.

        Zero would mean the cascade falls through to Escape; more than one would mean
        `.first` is a coin flip. The captured dialog carries a single button and no X.
        """
        await page.goto(_FIXTURE.as_uri())
        assert await page.locator(_CHANGELOG_BUTTON).count() == 1
        assert _CHANGELOG_BUTTON == OVERLAY_CLOSE_BUTTON_SELECTORS[0]

    async def test_blocked_page_is_recognised_as_blocking(self, page: Page) -> None:
        """`body{pointer-events:none}` is the signal, and the captured page has it."""
        await page.goto(_FIXTURE.as_uri())
        t = UiAutomationTransport()
        assert await t._overlay_blocks_page(page) is True  # type: ignore[attr-defined]

    async def test_dismissal_clears_the_modal_and_unblocks_the_app(self, page: Page) -> None:
        """End to end on real markup: dismissal reports success and the app is usable.

        The app control must go from covered to hit-testable — the exact transition
        measured live on 2026-08-27.
        """
        await page.goto(_FIXTURE.as_uri())
        t = UiAutomationTransport()

        assert await _body_pointer_events(page) == "none"
        assert await page.locator("[role='dialog']").count() == 1

        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]

        assert result is True
        assert await page.locator("[role='dialog']").count() == 0
        assert await _body_pointer_events(page) == "auto"
        assert await t._overlay_blocks_page(page) is False  # type: ignore[attr-defined]

    async def test_dismissal_never_matches_on_text(self, page: Page) -> None:
        """Relabel the button and dismissal still works — locale-invariance, proven.

        AGENTS.md forbids text-label selectors. Renaming 'Get started' to a
        Portuguese label must change nothing.
        """
        await page.goto(_FIXTURE.as_uri())
        await page.evaluate(
            "() => { const b = document.querySelector(\"[role='dialog'] button\");"
            " b.firstChild.textContent = 'Comece ja'; }"
        )
        t = UiAutomationTransport()

        assert await t._dismiss_blocking_overlays(page) is True  # type: ignore[attr-defined]
        assert await page.locator("[role='dialog']").count() == 0

    async def test_healthy_composer_dialog_is_left_untouched(self, page: Page) -> None:
        """#395 guard: a working Flow dialog on an unblocked page is not dismissed.

        A bare `[role='dialog']` detector once matched the character composer here,
        pressed Escape, and the generation went out without `entityContext` — billed,
        silently wrong. The dialog must survive with its prompt intact.
        """
        await page.set_content(_HEALTHY_COMPOSER)
        t = UiAutomationTransport()

        assert await t._overlay_blocks_page(page) is False  # type: ignore[attr-defined]
        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]

        assert result is False
        assert await page.locator("[role='dialog']").count() == 1
        assert await page.locator("#prompt").input_value() == "a cinematic portrait"


def _page_with_pointer_events(
    value: str | Exception,
    *,
    overlay_visible: bool = True,
    close_button_visible: bool = False,
) -> MagicMock:
    """Mock page whose body pointer-events reads *value* (or raises it)."""
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    page.screenshot = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    if isinstance(value, Exception):
        page.evaluate = AsyncMock(side_effect=value)
    else:
        page.evaluate = AsyncMock(return_value={"pointerEvents": value, "dialogs": 1})

    def _locator(sel: str) -> MagicMock:
        loc = MagicMock()
        is_overlay = "changelogs" in sel
        is_close = sel in OVERLAY_CLOSE_BUTTON_SELECTORS
        visible = (is_overlay and overlay_visible) or (is_close and close_button_visible)
        loc.is_visible = AsyncMock(return_value=visible)
        loc.click = AsyncMock()
        wrapper = MagicMock()
        wrapper.first = loc
        return wrapper

    page.locator = MagicMock(side_effect=_locator)
    return page


@pytest.mark.asyncio
class TestEscapeIsGated:
    """Escape is the #395 weapon — it may only fire when the page is really blocked."""

    async def test_escape_skipped_when_page_is_provably_clickable(self) -> None:
        """Detector says overlay, but the body is `auto` → do not press Escape.

        This is the structural kill for #395: on any page the app can still be
        clicked, the destructive fallback is off the table regardless of what the
        selector cascade thinks it saw.
        """
        page = _page_with_pointer_events("auto")
        t = UiAutomationTransport()

        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]

        assert result is False
        page.keyboard.press.assert_not_called()

    async def test_escape_still_used_when_blocking_is_unknown(self) -> None:
        """#26 regression: if we cannot read the body, keep the old behaviour.

        Refusing to act on an unreadable page would trade a known-good fallback for
        a mystery timeout. Only a *positive* 'auto' reading disables Escape.
        """
        page = _page_with_pointer_events(RuntimeError("evaluate unavailable"))
        t = UiAutomationTransport()

        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]

        assert result is True
        page.keyboard.press.assert_called_once_with("Escape")


@pytest.mark.asyncio
class TestPersistentBlockFailsLoudly:
    """A page that stays unclickable must abort pre-submit, not time out later."""

    async def test_raises_selector_drift_when_block_persists(self, tmp_path: Path) -> None:
        """Exit 23 with the probe name, at $0, instead of a bare TimeoutError.

        Everything downstream is doomed once the app cannot receive a click, so the
        honest move is to stop before submitting rather than hang on actionability.
        """
        from gflow_cli.errors import EXIT_CODE_MAP, UiSelectorDriftError

        page = _page_with_pointer_events("none")
        t = UiAutomationTransport()

        with pytest.raises(UiSelectorDriftError) as excinfo:
            await t._require_unblocked(page, tmp_path, epoch="project editor")  # type: ignore[attr-defined]

        assert "overlay_close_button" in str(excinfo.value)
        # Scripted callers branch on 23 = "the UI changed"; no new code is minted
        # because the remediation is byte-identical to the existing one.
        assert EXIT_CODE_MAP[UiSelectorDriftError] == 23

    async def test_transient_block_does_not_raise(self, tmp_path: Path) -> None:
        """Flow's own menus set the same property while open — one reading isn't proof.

        A Radix dropdown mid-open would otherwise hard-fail a healthy run, so the
        guard only fires when the block survives a settle.
        """
        page = _page_with_pointer_events("none")
        page.evaluate = AsyncMock(
            side_effect=[
                {"pointerEvents": "none", "dialogs": 1},
                {"pointerEvents": "auto", "dialogs": 0},
            ]
        )
        t = UiAutomationTransport()

        await t._require_unblocked(page, tmp_path, epoch="gallery")  # type: ignore[attr-defined]

    async def test_clear_page_never_probes_twice(self, tmp_path: Path) -> None:
        """The happy path costs exactly one probe and no settle."""
        page = _page_with_pointer_events("auto")
        t = UiAutomationTransport()

        await t._require_unblocked(page, tmp_path, epoch="project editor")  # type: ignore[attr-defined]

        assert page.evaluate.await_count == 1
        page.wait_for_timeout.assert_not_called()


@pytest.mark.asyncio
class TestDismissalIsVerified:
    """A dismissal that did not clear the block must not report success."""

    async def test_returns_false_when_overlay_survives_the_click(self) -> None:
        """Clicked the close button, page is still blocked → False, not True.

        Today the helper returns True the moment a click lands, so the log says
        `overlay_dismissed` and the run then times out somewhere else entirely.
        The success event lies; this is what stops it lying.
        """
        page = _page_with_pointer_events("none", close_button_visible=True)
        t = UiAutomationTransport()

        result = await t._dismiss_blocking_overlays(page)  # type: ignore[attr-defined]

        assert result is False
