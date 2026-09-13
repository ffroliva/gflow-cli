"""#799's agent-only cohort, discriminated against a real CSS engine.

The whole fix turns on one piece of third-party behaviour: whether Playwright reports
a `<button hidden="">` as present-but-not-visible. If it reported it visible, the
readiness gate would never have failed and none of this code would run; if it reported
it absent, the guard would fall through to selector drift and #799 would be unchanged.
A fake answers whatever it was written to answer, so it cannot settle that — only a
real browser can, and AGENTS.md asks for exactly this before wiring in an assumption
about someone else's runtime.

The markup is the reporter's own, from the issue body — the `hidden` attribute, the
`settings-trigger-button` class among others, the `aria-label`. What this does NOT
prove is that Flow still serves it; that needs an account in that cohort, which is a
named external blocker on #799. It proves the discriminator is sound on the DOM we
were given, and that its three negative controls do not fire.

No account, no network, no credits; skips when Chromium is absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gflow_cli.api.transports.migrated_composer import MigratedComposer

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator

    from playwright.async_api import Page

#: The composer as #799 captured it: the trigger is in the page under a bare `hidden`,
#: and there is no agent-mode chip anywhere, because this account has no classic arm.
AGENT_ONLY = """
<flow-creative-agent-prompt-box>
  <button hidden="" aria-label="Settings trigger"
          class="mat-mdc-icon-button settings-trigger-button flow-icon-button-transparent">
    <mat-icon class="google-symbols">tune</mat-icon>
  </button>
  <div class="agent-footer-actions"><button class="agent-action-button">Settings</button></div>
</flow-creative-agent-prompt-box>
"""

#: #749's recoverable arm: same hidden trigger, but a chip exists to turn off. The
#: recovery owns this, and the guard must keep its hands off it.
AGENT_MODE_RECOVERABLE = """
<flow-creative-agent-prompt-box>
  <button hidden="" aria-label="Settings trigger" class="settings-trigger-button"></button>
  <button class="agent-mode-chip" aria-pressed="true">
    <span class="agent-mode-chip-label">Agent</span>
  </button>
</flow-creative-agent-prompt-box>
"""

#: Ordinary drift: the anchor is gone from the DOM entirely. Our bug, not a cohort.
TRIGGER_GONE = """
<flow-prompt-box><button class="some-renamed-button"></button></flow-prompt-box>
"""

#: A healthy classic composer — the trigger is right there and visible.
HEALTHY = """
<flow-prompt-box>
  <button aria-label="Settings trigger" class="settings-trigger-button">tune</button>
</flow-prompt-box>
"""


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


async def _verdict(page: Page, markup: str) -> bool:
    from gflow_cli.api.transports.migrated_composer import READY_ANCHOR

    await page.set_content(markup)
    trigger = page.locator(READY_ANCHOR).first
    return await MigratedComposer._is_agent_only_composer(page, trigger)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_a_bare_hidden_attribute_really_does_read_as_present_but_invisible(
    page: Page,
) -> None:
    """The load-bearing third-party assumption, measured rather than assumed."""
    from gflow_cli.api.transports.migrated_composer import READY_ANCHOR

    await page.set_content(AGENT_ONLY)
    trigger = page.locator(READY_ANCHOR).first

    assert await trigger.count() == 1, "the reporter's class list must still match"
    assert await trigger.is_visible() is False, "a bare `hidden` must not read as visible"


@pytest.mark.asyncio
async def test_the_reporters_captured_composer_is_recognised(page: Page) -> None:
    assert await _verdict(page, AGENT_ONLY) is True


@pytest.mark.asyncio
async def test_the_recoverable_agent_mode_is_left_to_its_recovery(page: Page) -> None:
    """A chip in the page means there IS a classic arm — #749 owns this, not #799."""
    assert await _verdict(page, AGENT_MODE_RECOVERABLE) is False


@pytest.mark.asyncio
async def test_a_vanished_trigger_stays_selector_drift(page: Page) -> None:
    assert await _verdict(page, TRIGGER_GONE) is False


@pytest.mark.asyncio
async def test_a_healthy_composer_is_never_called_a_cohort(page: Page) -> None:
    assert await _verdict(page, HEALTHY) is False
