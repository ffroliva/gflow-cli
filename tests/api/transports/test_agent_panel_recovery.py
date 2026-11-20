"""#1374 / #1384: the editor gate returns to the classic composer from Flow's agent mode.

Driven against a real CSS engine, because the fix is anchors plus ordering. A fake page
answers whatever it was written to answer, so it cannot show that a selector misses the
decoy beside the button it is meant to hit.

The markup is a small state machine built from the structure the 2026-11-20 spike recorded
(`docs/superpowers/spikes/2026-11-20-agent-mode-switch-and-panel-rendering.md`) for the new
build. For the old build it uses the 2026-11-19 spike. Only tags, classes, `aria-pressed`,
`hidden` and ligatures are reproduced; no text. Clicking a control changes the state the way
Flow did in the spike:

* the switch (new) or chip (old) toggles agent mode;
* the expand control opens the agent panel;
* the panel's header close button (`right_panel_close` new, `close` old) closes it.

While the panel is open the toggle is not in the DOM, which is what both builds did.

Two decoys stay on the page in every state:

* a `close` button on an attachment chip inside the agent prompt box;
* the library menu's `settings_2` in `flow-tile-view-header`.

`window.CLICKS` records every click as `<container>:<ligature or class>`, so each test can
assert exactly which controls the gate touched.

What this does not prove is that Flow still renders it. That is the job of
`tests/e2e/test_agent_panel_recovery_bdd.py`.
"""

# ruff: noqa: E501 - the inline page script keeps its shape

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from gflow_cli.api.transports import _mc_editor_gate
from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.errors import UiSelectorDriftError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

FLOW_PAGE = r"""
<flow-tile-view-header>
  <button class="flow-icon-button-transparent" aria-haspopup="menu">
    <mat-icon class="google-symbols">settings_2</mat-icon>
  </button>
</flow-tile-view-header>
<div id="root"></div>
<script>
const S = window.SCENARIO;
window.CLICKS = [];
const icon = (l) => `<mat-icon class="google-symbols">${l}</mat-icon>`;
const trigger = (hidden) =>
  `<button ${hidden ? 'hidden=""' : ''} aria-label="Settings trigger" class="settings-trigger-button">${icon('crop_16_9')}</button>`;
function toggle() {
  if (!S.classicArm) return '';
  if (S.build === 'new') {
    return `<flow-agent-mode-switch><button type="button" class="switch${S.agent ? ' checked' : ''}" aria-pressed="${S.agent}">${icon(S.agent ? 'spark' : 'spark_off')}</button></flow-agent-mode-switch>`;
  }
  return `<flow-agent-mode-toggle-chip><button class="agent-mode-chip" aria-pressed="${S.agent}">Agent</button></flow-agent-mode-toggle-chip>`;
}
function agentBox(inPanel) {
  const gear = S.build === 'new'
    ? (inPanel ? '' : `<button class="agent-action-button" aria-pressed="false">${icon('settings_2')}</button>`)
    : `<button class="agent-action-button">${icon('article_spark')}</button><button class="agent-action-button">${icon('tune')}</button>`;
  const expand = inPanel ? '' : `<button>${icon(S.build === 'new' ? 'right_panel_open' : 'expand_content')}</button>`;
  const decoy = `<flow-image-ingredient-chip><button>${icon('close')}</button></flow-image-ingredient-chip>`;
  return `<flow-creative-agent-prompt-box><flow-base-prompt-box>${decoy}${expand}`
    + `<flow-add-menu><button>${icon('add')}</button></flow-add-menu>${gear}`
    + `${inPanel ? '' : toggle()}${trigger(true)}`
    + `<flow-generate-icon-button><button>${icon('arrow_forward')}</button></flow-generate-icon-button>`
    + `</flow-base-prompt-box></flow-creative-agent-prompt-box>`;
}
function render() {
  let html;
  if (!S.agent) {
    html = `<flow-prompt-box><flow-base-prompt-box>${toggle()}${trigger(false)}</flow-base-prompt-box></flow-prompt-box>`;
  } else if (!S.panel) {
    html = `<flow-prompt-box>${agentBox(false)}</flow-prompt-box>`;
  } else {
    const head = S.build === 'new' ? ['forum', 'settings_2', 'right_panel_close'] : ['menu', 'edit_square', 'close'];
    html = `<mat-sidenav-content><flow-agent-panel>`
      + head.map((l) => `<button class="flow-icon-button-transparent">${icon(l)}</button>`).join('')
      + `<flow-chat-view></flow-chat-view>${agentBox(true)}</flow-agent-panel></mat-sidenav-content>`;
  }
  document.getElementById('root').innerHTML = html;
}
document.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  const lig = (b.querySelector('mat-icon')?.textContent || '').trim();
  const where = b.closest('flow-creative-agent-prompt-box, flow-tile-view-header, flow-agent-panel, flow-prompt-box');
  window.CLICKS.push(`${where ? where.tagName.toLowerCase() : 'page'}:${lig || b.className}`);
  if (b.matches('.switch, .agent-mode-chip')) S.agent = !S.agent;
  else if (where && where.tagName === 'FLOW-AGENT-PANEL' && (lig === 'right_panel_close' || lig === 'close')) S.panel = false;
  else if (lig === 'right_panel_open' || lig === 'expand_content') S.panel = true;
  render();
});
render();
</script>
"""

DECOYS = ("flow-creative-agent-prompt-box:close", "flow-tile-view-header:settings_2")


@pytest.fixture(autouse=True)
def _fast_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the gate's waits. These pages either settle at once or never will."""
    monkeypatch.setattr(_mc_editor_gate, "AGENT_ONLY_EARLY_S", 0.5)
    monkeypatch.setattr(_mc_editor_gate, "AGENT_ONLY_SETTLE_S", 0.2)
    # How long the gate waits for the toggle after closing the panel. raising=False: the
    # constant arrives with the fix, and this red-first module predates it.
    monkeypatch.setattr(_mc_editor_gate, "AGENT_PANEL_SETTLE_S", 0.3, raising=False)


async def _run(page: Page, **scenario: Any) -> tuple[str, list[str], dict[str, Any]]:
    """Serve the project URL with `scenario`, run `ensure_editor`, return (kind, clicks, state)."""
    body = f"<script>window.SCENARIO={json.dumps(scenario)}</script>{FLOW_PAGE}"

    async def _serve(route: Any) -> None:
        await route.fulfill(body=body, content_type="text/html")

    await page.route("https://flow.google.com/**", _serve)
    kind = await MigratedComposer().ensure_editor(page, "p1", timeout_s=3.0)
    clicks: list[str] = await page.evaluate("window.CLICKS")
    state: dict[str, Any] = await page.evaluate("window.SCENARIO")
    return kind, clicks, state


# --- the new build (2026-11-20): the switch ---------------------------------------------


@pytest.mark.asyncio
async def test_new_build_panel_open_returns_to_classic(page: Page) -> None:
    """#1384 as served: panel open, no switch in the DOM. Close the panel, then the switch."""
    kind, clicks, state = await _run(page, build="new", agent=True, panel=True, classicArm=True)
    assert kind == "classic"
    assert clicks == ["flow-agent-panel:right_panel_close", "flow-creative-agent-prompt-box:spark"]
    assert state["agent"] is False and state["panel"] is False


@pytest.mark.asyncio
async def test_new_build_agent_mode_with_the_panel_closed_returns_to_classic(page: Page) -> None:
    """#749's case on the new build: the pressed switch is the way out."""
    kind, clicks, state = await _run(page, build="new", agent=True, panel=False, classicArm=True)
    assert kind == "classic"
    assert clicks == ["flow-creative-agent-prompt-box:spark"]
    assert state["agent"] is False


@pytest.mark.asyncio
async def test_new_build_healthy_classic_page_is_not_touched(page: Page) -> None:
    """Guard (passes before the fix too): a recovery that fires on a healthy page would
    switch it INTO agent mode."""
    kind, clicks, _ = await _run(page, build="new", agent=False, panel=False, classicArm=True)
    assert kind == "classic"
    assert clicks == []


@pytest.mark.asyncio
async def test_new_build_with_no_classic_arm_fails_closed_as_drift(page: Page) -> None:
    """Unobserved on the new build: an account with no switch at all. The panel is closed
    once, no switch appears, and today's agent-only check finds no `tune`, so the run is
    drift, never a guessed agent-only verdict."""
    with pytest.raises(UiSelectorDriftError):
        await _run(page, build="new", agent=True, panel=True, classicArm=False)
    clicks: list[str] = await page.evaluate("window.CLICKS")
    assert clicks == ["flow-agent-panel:right_panel_close"]


# --- the old build (2026-11-19): the chip ----------------------------------------------


@pytest.mark.asyncio
async def test_old_build_panel_open_returns_to_classic(page: Page) -> None:
    """#1374 as served: panel open, chip gone. It used to be read as agent-only."""
    kind, clicks, state = await _run(page, build="old", agent=True, panel=True, classicArm=True)
    assert kind == "classic"
    assert clicks == [
        "flow-agent-panel:close",
        "flow-creative-agent-prompt-box:agent-mode-chip",
    ]
    assert state["agent"] is False and state["panel"] is False


@pytest.mark.asyncio
async def test_old_build_pressed_chip_still_returns_to_classic(page: Page) -> None:
    """Guard: #749 on the old build is unchanged."""
    kind, clicks, _ = await _run(page, build="old", agent=True, panel=False, classicArm=True)
    assert kind == "classic"
    assert clicks == ["flow-creative-agent-prompt-box:agent-mode-chip"]


@pytest.mark.asyncio
async def test_old_build_agent_only_page_keeps_its_verdict_after_the_panel_close(
    page: Page,
) -> None:
    """#799's account rendered the panel. After the close no chip appears, so today's
    discriminator (`tune`) decides, unchanged."""
    kind, clicks, _ = await _run(page, build="old", agent=True, panel=True, classicArm=False)
    assert kind == "agent_only"
    assert clicks == ["flow-agent-panel:close"]


# --- decoys --------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("build", ["new", "old"])
async def test_decoys_are_never_clicked(page: Page, build: str) -> None:
    """The attachment chip's `close` sits inside the panel; the library `settings_2` is on
    every page. Neither may be clicked by the recovery."""
    _, clicks, _ = await _run(page, build=build, agent=True, panel=True, classicArm=True)
    assert not set(clicks) & set(DECOYS), clicks
