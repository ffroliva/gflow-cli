"""#799's agent-only cohort, discriminated against a real CSS engine.

The whole fix turns on one piece of third-party behaviour: whether Playwright reports
a `<button hidden="">` as present-but-not-visible. If it reported it visible, the
readiness gate would never have failed and none of this code would run; if it reported
it absent, the guard would fall through to selector drift and #799 would be unchanged.
A fake answers whatever it was written to answer, so it cannot settle that — only a
real browser can, and AGENTS.md asks for exactly this before wiring in an assumption
about someone else's runtime.

Provenance, precisely: the reporter's issue posts an elided `<button hidden="" ...
aria-label="Settings trigger" class="... settings-trigger-button ...">`, and those
attributes are theirs verbatim. The surrounding component chain
(`flow-creative-agent-prompt-box`, `agent-footer-actions`) comes from the #749 spike
that measured this host. That is not circular — neither selector under test touches
any of it; only the `hidden` attribute and the two class names are load-bearing.

What this does NOT prove is that Flow still serves it; that needs an account in that
cohort, which is a named external blocker on #799. It proves the discriminator is
sound on the DOM we were given, and that its negative controls do not fire.

Two of those controls were also measured against LIVE Flow at $0 (council D6), since
synthetic markup is not the live arm:

* healthy migrated composer (`ffroliva`, a real project page) — trigger present and
  visible, `button.agent-mode-chip` present and **un-pressed**, verdict `False`. This
  is the load-bearing one: a normal account on this host HAS a chip, so having none
  is genuinely anomalous rather than merely unmatched.
* live `/about` landing (`denon82`, #756) — trigger absent, verdict `False`, i.e. the
  `TRIGGER_GONE` arm reached on a real page rather than a written one.

No account, no network, no credits; skips when Chromium is absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gflow_cli.api.transports.migrated_composer import MigratedComposer

if TYPE_CHECKING:  # pragma: no cover
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


# --- readiness end to end on a real CSS engine: `ensure_editor` navigates, so the project
# URL is served the captured markup through `page.route`. ---------------------------------


async def _ensure_editor_on(page: Page, markup: str, *, timeout_s: float) -> tuple[str, float]:
    import time

    async def _serve(route: object) -> None:
        await route.fulfill(body=markup, content_type="text/html")  # type: ignore[attr-defined]

    await page.route("https://flow.google.com/**", _serve)
    t0 = time.monotonic()
    kind = await MigratedComposer().ensure_editor(page, "p1", timeout_s=timeout_s)
    return kind, time.monotonic() - t0


@pytest.mark.asyncio
async def test_the_agent_only_composer_is_reported_without_waiting_out_the_full_gate(
    page: Page,
) -> None:
    """Every agent-only run used to pay the whole 30 s readiness wait before the
    discriminator ran. It is reported after the short early wait instead."""
    from gflow_cli.api.transports.migrated_composer import AGENT_ONLY_EARLY_S

    kind, elapsed = await _ensure_editor_on(page, AGENT_ONLY, timeout_s=30.0)
    assert kind == "agent_only"
    assert elapsed < AGENT_ONLY_EARLY_S + 5


@pytest.mark.asyncio
async def test_a_healthy_composer_is_reported_classic_through_ensure_editor(page: Page) -> None:
    kind, _ = await _ensure_editor_on(page, HEALTHY, timeout_s=30.0)
    assert kind == "classic"


@pytest.mark.asyncio
async def test_a_chip_that_mounts_late_is_not_called_agent_only(
    page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review: both facts are absences, so a slow classic mount (hidden trigger now, chip a
    second later) passed as agent-only and would have had account defaults written to it."""
    from gflow_cli.api.transports import migrated_composer as mc

    monkeypatch.setattr(mc, "AGENT_ONLY_SETTLE_S", 0.6)
    late_chip = AGENT_ONLY + (
        "<script>setTimeout(() => document.body.insertAdjacentHTML('beforeend',"
        ' \'<button class="agent-mode-chip" aria-pressed="true"></button>\'), 200)</script>'
    )
    assert await _verdict(page, late_chip) is False


@pytest.mark.asyncio
async def test_no_agent_settings_button_is_not_agent_only(page: Page) -> None:
    """The verdict needs one presence: the agent composer's own settings button."""
    without_tune = AGENT_ONLY.replace('<mat-icon class="google-symbols">tune</mat-icon>', "")
    assert await _verdict(page, without_tune) is False
