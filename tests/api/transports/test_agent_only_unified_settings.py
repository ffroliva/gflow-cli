"""Agent settings on Flow's 2026-11-20 build (#1387), against a real CSS engine.

The fixture is a scripted stand-in for the pane measured on 2026-11-21
(`docs/superpowers/spikes/2026-11-21-agent-settings-on-the-new-build.md`): a `settings_2`
gear in the agent panel's header while the panel is open, or the compact prompt box's
`button.agent-action-button` while it is closed (a closed panel is not in the DOM); a
first level, `flow-unified-settings-view`, with the confirm toggle group and one
`button.<section>-defaults-row` per section whose `.defaults-row-caption` summarises it;
a second level, `flow-generation-defaults-view`, with a `label.radio-option` model list and
aspect / count toggle groups. Every click queues a save, a batchexecute `Kcr7Ub` POST sent
after the previous one is answered, and there is no Save button. The tests answer those
saves themselves (`page.route`), slowly when it matters, and record what reached them. The
project header's own `settings_2` (the grid's view settings) sits outside the panel and
must never be clicked. Its markup follows the captures; its timing is invented and short.

No account, no network, no credits; skips when Chromium is absent.
"""

# ruff: noqa: E501 - the fixture is inline HTML/JS; wrapping it would hide its shape

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.transports import agent_only_composer as aoc
from gflow_cli.api.transports.migrated_composer import (
    IMAGE_MODEL_MENU_MATCHERS,
    READY_ANCHOR,
    VIDEO_MODEL_MENU_MATCHERS,
    MigratedComposer,
)
from gflow_cli.api.video import VideoModel
from gflow_cli.errors import ConfigurationError, UiSelectorDriftError

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

FIXTURE = r"""
<flow-project-header><button id="grid-gear"><mat-icon>settings_2</mat-icon></button></flow-project-header>
<div id="root"></div>
<script>
const S = Object.assign({panelOpen: true, lagMs: 0, deadCount: false, staleRow: false,
  twoGears: false, unlabelledModel: false, openControl: true, openFails: false}, window.SCENARIO || {});
window.log = {gridGear: 0, compactGear: 0, panelGear: 0, openPanel: 0, writes: 0};
let saves = Promise.resolve();
function save() {  // Flow's queue: one Kcr7Ub at a time, each carrying the current values
  window.log.writes++;
  const body = JSON.stringify({confirm: state.confirm, image: state.image, video: state.video});
  saves = saves.then(() => fetch('https://flow.google.com/_/LabsSandboxUi/data/batchexecute?rpcids=Kcr7Ub',
    {method: 'POST', mode: 'no-cors', body}).catch(() => {}));
}
const LIGS = {image: ['crop_16_9', 'crop_landscape', 'crop_square', 'crop_portrait', 'crop_9_16'],
  video: ['crop_16_9', 'crop_9_16']};
const TEXT = {crop_16_9: '16:9', crop_landscape: '4:3', crop_square: '1:1', crop_portrait: '3:4', crop_9_16: '9:16'};
const MODELS = {image: ['Nano Banana 2.1', 'Nano Banana Pro', 'Nano Banana 2 Lite'],
  video: ['Omni 1.2 Flash', 'Omni 1.1 Flash', 'Veo 3.1 - Lite', 'Veo 3.1 - Fast']};
const state = {confirm: 0, image: {aspect: 0, count: 1, model: 'Nano Banana 2.1'},
  video: {aspect: 0, count: 0, model: 'Omni 1.2 Flash'}};
window.state = state;
const frozen = {};
let panelOpen = S.panelOpen, view = 'chat';
const box = gear => `<flow-creative-agent-prompt-box><div class="ProseMirror" contenteditable="true">x</div>` +
  (gear ? '<button class="agent-action-button" id="compact-gear"><mat-icon>settings_2</mat-icon></button>' : '') +
  (gear && S.openControl ? '<button id="open-panel"><mat-icon>right_panel_open</mat-icon></button>' : '') +
  '<button class="agent-action-button"><mat-icon>add_2</mat-icon></button></flow-creative-agent-prompt-box>';
function caption(k) {
  const s = state[k];
  const text = `${k === 'image' ? '\u{1F34C} ' : ''}${s.model}, ${TEXT[LIGS[k][s.aspect]]}, ${s.count + 1} output${s.count ? 's' : ''}`;
  if (!(k in frozen)) frozen[k] = text;
  return S.staleRow ? frozen[k] : text;
}
function toggles(name, labels, checked) {
  return '<mat-button-toggle-group>' + labels.map((l, i) => `<mat-button-toggle><button role="radio" ` +
    `data-group="${name}" data-i="${i}" aria-checked="${checked === i}">${l}</button></mat-button-toggle>`).join('') +
    '</mat-button-toggle-group>';
}
function body() {
  if (view === 'chat') return box(false);
  if (view === 'unified') return `<flow-unified-settings-view>${toggles('confirm', ['Always', 'Never'], state.confirm)}` +
    ['image', 'video'].map(k => `<button class="${k}-defaults-row"><span class="defaults-row-title">${k}</span>` +
      `<span class="defaults-row-caption">${caption(k)}</span></button>`).join('') + '</flow-unified-settings-view>';
  const k = view, s = state[k];
  const models = MODELS[k].map(m => `<label class="radio-option${m === s.model ? ' radio-option-selected' : ''}" data-model="${m}">` +
    `<mat-icon>${m === s.model ? 'radio_button_checked' : 'radio_button_unchecked'}</mat-icon><span class="radio-option-text">` +
    (S.unlabelledModel ? m : `<span class="radio-option-label">${m}</span>`) + '</span></label>').join('');
  return `<flow-generation-defaults-view><flow-radio-group class="model-radio-group">${models}</flow-radio-group>` +
    toggles('aspect', LIGS[k].map(l => `<mat-icon>${l}</mat-icon>${TEXT[l]}`), s.aspect) +
    toggles('count', ['x1', 'x2', 'x3', 'x4'], s.count) +
    (k === 'image' ? toggles('thinking', ['Low', 'High'], 0) : '') + '</flow-generation-defaults-view>';
}
function render() {
  const root = document.getElementById('root');
  if (!panelOpen) {
    root.innerHTML = box(true) + '<flow-agent-mode-switch><button class="switch" aria-pressed="true">Agent</button></flow-agent-mode-switch>';
  } else {
    const back = view === 'chat' ? '' : '<button class="header-action" id="back"><mat-icon>arrow_back</mat-icon></button>';
    const second = S.twoGears ? '<button id="second-gear"><mat-icon>settings_2</mat-icon></button>' : '';
    root.innerHTML = `<flow-agent-panel><div class="panel-header">${back}<button id="panel-gear"><mat-icon>settings_2</mat-icon></button>` +
      `${second}<button><mat-icon>right_panel_close</mat-icon></button></div>${body()}</flow-agent-panel>`;
  }
  const op = root.querySelector('#open-panel');
  if (op) op.onclick = () => { window.log.openPanel++; if (!S.openFails) { panelOpen = true; view = 'chat'; render(); } };
  const cg = root.querySelector('#compact-gear');
  if (cg) cg.onclick = () => { window.log.compactGear++; panelOpen = true; view = 'unified'; render(); };
  for (const id of ['#panel-gear', '#second-gear']) {
    const g = root.querySelector(id);
    if (g) g.onclick = () => { window.log.panelGear++; view = 'unified'; render(); };
  }
  const b = root.querySelector('#back');
  if (b) b.onclick = () => { view = view === 'unified' ? 'chat' : 'unified'; render(); };
  root.querySelectorAll('.image-defaults-row, .video-defaults-row').forEach(r =>
    r.onclick = () => { view = r.className.split('-')[0]; render(); });
  root.querySelectorAll('label.radio-option').forEach(l =>
    l.onclick = () => { state[view].model = l.dataset.model; save(); render(); });
  root.querySelectorAll('button[role=radio]').forEach(btn => btn.onclick = () => {
    const g = btn.dataset.group, i = +btn.dataset.i;
    if (g === 'count' && S.deadCount) return;
    if (g === 'confirm') state.confirm = i; else if (g !== 'thinking') state[view][g] = i;
    save();  // queued now: there is no Save on this build
    const all = [...btn.closest('mat-button-toggle-group').querySelectorAll('button[role=radio]')];
    // Like Angular: the attribute lands after the click handler returns.
    setTimeout(() => all.forEach(x => x.setAttribute('aria-checked', String(x === btn))), S.lagMs);
  });
}
document.getElementById('grid-gear').onclick = () => { window.log.gridGear++; };
render();
</script>
"""

ORIGINAL = {
    "confirm": 0,
    "image": {"aspect": 0, "count": 1, "model": "Nano Banana 2.1"},
    "video": {"aspect": 0, "count": 0, "model": "Omni 1.2 Flash"},
}


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aoc, "RADIO_SETTLE_S", 0.6)
    monkeypatch.setattr(aoc, "PANE_S", 1.5)
    monkeypatch.setattr(aoc, "SAVE_QUIET_S", 0.3)
    monkeypatch.setattr(aoc, "SAVE_BUDGET_S", 5.0)


async def _load(
    page: Page,
    *,
    save_delay_s: float = 0.0,
    fail_saves: bool = False,
    fail_first: int = 0,
    **scenario: Any,
) -> list[dict[str, Any]]:
    """Serve the fixture; answer its saves after `save_delay_s` (or fail them all, or the
    first `fail_first`) and return the saves that reached the "server", in arrival order."""
    landed: list[dict[str, Any]] = []
    seen = [0]

    async def answer(route: Any) -> None:
        seen[0] += 1
        if fail_saves or seen[0] <= fail_first:
            await route.abort()
            return
        await asyncio.sleep(save_delay_s)
        landed.append(json.loads(route.request.post_data or "{}"))
        await route.fulfill(status=200, body=")]}'\n[]")

    await page.route("**/*rpcids=Kcr7Ub*", answer)
    await page.set_content(f"<script>window.SCENARIO={json.dumps(scenario)}</script>{FIXTURE}")
    return landed


async def _state(page: Page) -> dict[str, Any]:
    return await page.evaluate("window.state")


async def _log(page: Page) -> dict[str, Any]:
    return await page.evaluate("window.log")


async def _in_chat(page: Page) -> bool:
    return (
        await page.locator(aoc.EDITOR).last.is_visible()
        and not await page.locator(f"{aoc.UNIFIED_PANE}, {aoc.DEFAULTS_PANE}").count()
    )


@pytest.mark.asyncio
async def test_defaults_are_applied_from_the_open_panel_and_read_back(page: Page) -> None:
    await _load(page, lagMs=150)
    composer = aoc.AgentOnlyComposer()
    snapshot = await composer.apply_defaults(
        page,
        section="video",
        aspect_ligature="crop_9_16",
        count=2,
        model=VIDEO_MODEL_MENU_MATCHERS[VideoModel.VEO_3_1_LITE],
        confirm="never",
    )
    assert snapshot == aoc.DefaultsSnapshot(
        section="video",
        aspect_index=0,
        count_index=0,
        model_text="Omni 1.2 Flash",
        confirm_value="1",
    )
    state = await _state(page)
    assert state["video"] == {"aspect": 1, "count": 1, "model": "Veo 3.1 - Lite"}
    assert state["confirm"] == 1
    assert state["image"] == ORIGINAL["image"]
    assert await _in_chat(page)
    assert (await _log(page))["gridGear"] == 0


@pytest.mark.asyncio
async def test_a_closed_panel_is_opened_from_the_compact_box_and_restore_returns_everything(
    page: Page,
) -> None:
    await _load(page, panelOpen=False)
    composer = aoc.AgentOnlyComposer()
    snapshot = await composer.apply_defaults(
        page,
        section="image",
        aspect_ligature="crop_portrait",
        count=3,
        model=IMAGE_MODEL_MENU_MATCHERS[ImageModel.GEM_PIX_2],
        confirm="never",
    )
    state = await _state(page)
    assert state["image"] == {"aspect": 3, "count": 2, "model": "Nano Banana Pro"}
    assert state["confirm"] == 1
    log = await _log(page)
    assert log["compactGear"] == 1
    assert log["gridGear"] == 0

    await composer.restore_defaults(page, snapshot)
    assert await _state(page) == ORIGINAL
    assert await _in_chat(page)


@pytest.mark.asyncio
async def test_account_confirm_leaves_the_confirm_choice_alone(page: Page) -> None:
    await _load(page)
    await aoc.AgentOnlyComposer().apply_defaults(
        page, section="video", aspect_ligature="crop_16_9", count=1, model=None, confirm="account"
    )
    state = await _state(page)
    assert state["confirm"] == 0
    assert state["video"] == ORIGINAL["video"]


@pytest.mark.asyncio
async def test_a_failed_apply_writes_the_originals_back(page: Page) -> None:
    """Every click has already saved, so a run that fails halfway would otherwise leave the
    account on half of its values, and the next run would snapshot them as the originals."""
    await _load(page, deadCount=True)
    with pytest.raises(UiSelectorDriftError, match="count radio"):
        await aoc.AgentOnlyComposer().apply_defaults(
            page,
            section="video",
            aspect_ligature="crop_9_16",
            count=2,
            model=None,
            confirm="never",
        )
    assert await _state(page) == ORIGINAL
    assert await _in_chat(page)


@pytest.mark.asyncio
async def test_a_summary_row_that_does_not_show_the_run_stops_before_the_submit(
    page: Page,
) -> None:
    await _load(page, staleRow=True)
    with pytest.raises(UiSelectorDriftError, match="does not show"):
        await aoc.AgentOnlyComposer().apply_defaults(
            page,
            section="video",
            aspect_ligature="crop_9_16",
            count=1,
            model=None,
            confirm="account",
        )
    assert await _state(page) == ORIGINAL
    assert await _in_chat(page)


@pytest.mark.asyncio
async def test_two_visible_gears_are_a_miss_not_a_guess(page: Page) -> None:
    await _load(page, twoGears=True)
    with pytest.raises(UiSelectorDriftError, match="2 visible Agent settings gears"):
        await aoc.AgentOnlyComposer().apply_defaults(
            page, section="video", aspect_ligature="crop_9_16", count=1, model=None, confirm="never"
        )
    log = await _log(page)
    assert log["panelGear"] == 0
    assert log["writes"] == 0


@pytest.mark.asyncio
async def test_an_unreadable_model_changes_nothing(page: Page) -> None:
    await _load(page, unlabelledModel=True)
    with pytest.raises(UiSelectorDriftError, match="model choice is unreadable"):
        await aoc.AgentOnlyComposer().apply_defaults(
            page, section="video", aspect_ligature="crop_9_16", count=1, model=None, confirm="never"
        )
    assert (await _log(page))["writes"] == 0
    assert await _in_chat(page)


@pytest.mark.asyncio
async def test_the_older_pane_keeps_its_own_path(page: Page) -> None:
    await page.set_content(
        "<flow-creative-agent-prompt-box><button class='agent-action-button'>"
        "<mat-icon>tune</mat-icon></button></flow-creative-agent-prompt-box>"
    )
    assert not await aoc.AgentOnlyComposer()._is_unified(page)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_apply_returns_only_once_every_queued_save_has_landed(page: Page) -> None:
    """Measured 2026-11-21: the pane shows a click's value at once and sends its save about
    0.5 s later, one save at a time. A run that moved on as soon as the pane read right left
    saves unsent, and a restore that read back correctly on the page was gone next session."""
    landed = await _load(page, save_delay_s=0.4)
    await aoc.AgentOnlyComposer().apply_defaults(
        page,
        section="video",
        aspect_ligature="crop_9_16",
        count=2,
        model=VIDEO_MODEL_MENU_MATCHERS[VideoModel.VEO_3_1_LITE],
        confirm="never",
    )
    assert len(landed) == (await _log(page))["writes"] == 4
    assert landed[-1] == await _state(page)
    assert landed[-1]["video"] == {"aspect": 1, "count": 1, "model": "Veo 3.1 - Lite"}


@pytest.mark.asyncio
async def test_restore_returns_only_once_its_saves_have_landed(page: Page) -> None:
    landed = await _load(page, save_delay_s=0.4)
    composer = aoc.AgentOnlyComposer()
    snapshot = await composer.apply_defaults(
        page, section="video", aspect_ligature="crop_9_16", count=3, model=None, confirm="never"
    )
    await composer.restore_defaults(page, snapshot)
    assert landed[-1] == ORIGINAL


@pytest.mark.asyncio
async def test_a_failed_apply_waits_for_its_own_saves_before_the_rollback_returns(
    page: Page,
) -> None:
    """The apply's aspect and count saves are still queued when its model choice fails, and
    the rollback's saves queue behind them. A rollback that counted only its own requests saw
    the queue quiet between two slow saves and returned with the originals still unsent."""
    landed = await _load(page, save_delay_s=0.5)
    with pytest.raises(ConfigurationError, match="matched 0"):
        await aoc.AgentOnlyComposer().apply_defaults(
            page,
            section="video",
            aspect_ligature="crop_9_16",
            count=2,
            model=IMAGE_MODEL_MENU_MATCHERS[ImageModel.GEM_PIX_2],  # offered for images only
            confirm="account",
        )
    assert len(landed) == (await _log(page))["writes"] == 4
    assert landed[-1] == ORIGINAL


@pytest.mark.asyncio
async def test_an_apply_save_that_failed_does_not_cut_the_rollback_short(page: Page) -> None:
    """The rollback shares the apply's watch; the apply's failed save is the apply's error,
    so the rollback still waits for its own saves to land."""
    landed = await _load(page, save_delay_s=0.3, fail_first=1)
    with pytest.raises(UiSelectorDriftError, match="saves .Kcr7Ub. failed while applying"):
        await aoc.AgentOnlyComposer().apply_defaults(
            page, section="video", aspect_ligature="crop_9_16", count=2, model=None, confirm="never"
        )
    assert landed[-1] == ORIGINAL


@pytest.mark.asyncio
async def test_a_save_that_fails_is_drift_before_the_submit(page: Page) -> None:
    await _load(page, fail_saves=True)
    with pytest.raises(UiSelectorDriftError, match="saves .Kcr7Ub. failed"):
        await aoc.AgentOnlyComposer().apply_defaults(
            page, section="video", aspect_ligature="crop_9_16", count=1, model=None, confirm="never"
        )


# --- the agent-only verdict on the new build -------------------------------------------------

#: The 2026-11-20 build in agent mode with the panel closed: the classic trigger hidden,
#: the compact prompt box with its gear. With no switch, there is no classic arm.
NEW_BUILD_NO_SWITCH = """
<button hidden="" class="settings-trigger-button"></button>
<flow-creative-agent-prompt-box><div class="ProseMirror" contenteditable="true"></div>
  <button class="agent-action-button"><mat-icon>settings_2</mat-icon></button>
</flow-creative-agent-prompt-box>
"""


async def _verdict(page: Page, markup: str) -> bool:
    await page.set_content(markup)
    trigger = page.locator(READY_ANCHOR).first
    return await MigratedComposer._is_agent_only_composer(page, trigger)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_a_closed_panel_is_opened_before_the_submit(page: Page) -> None:
    # A closed panel is not in the DOM, so its gate would never be seen (measured 2026-11-21).
    await _load(page, panelOpen=False)
    await aoc.AgentOnlyComposer()._open_chat(page)  # noqa: SLF001
    assert await page.locator(aoc.AGENT_PANEL).first.is_visible()
    assert await _in_chat(page)
    assert (await _log(page))["openPanel"] == 1


@pytest.mark.asyncio
async def test_an_open_panel_is_left_as_it_is(page: Page) -> None:
    await _load(page)
    await aoc.AgentOnlyComposer()._open_chat(page)  # noqa: SLF001
    assert (await _log(page))["openPanel"] == 0


@pytest.mark.asyncio
async def test_a_page_without_the_open_control_is_left_as_it_is(page: Page) -> None:
    await _load(page, panelOpen=False, openControl=False)
    await aoc.AgentOnlyComposer()._open_chat(page)  # noqa: SLF001
    assert not await page.locator(aoc.AGENT_PANEL).count()


@pytest.mark.asyncio
async def test_a_panel_that_does_not_open_is_drift_and_charges_nothing(page: Page) -> None:
    await _load(page, panelOpen=False, openFails=True)
    with pytest.raises(UiSelectorDriftError, match="did not open") as raised:
        await aoc.AgentOnlyComposer()._open_chat(page)  # noqa: SLF001
    assert raised.value.charged == "no"
    assert (await _log(page))["openPanel"] == 1


@pytest.mark.asyncio
async def test_the_new_build_with_no_switch_is_the_agent_only_composer(
    page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports import _mc_agent_mode

    monkeypatch.setattr(_mc_agent_mode, "AGENT_ONLY_SETTLE_S", 0.1)
    assert await _verdict(page, NEW_BUILD_NO_SWITCH)


@pytest.mark.asyncio
async def test_the_new_build_with_a_switch_is_never_called_agent_only(
    page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.api.transports import _mc_agent_mode

    monkeypatch.setattr(_mc_agent_mode, "AGENT_ONLY_SETTLE_S", 0.1)
    switch = (
        '<flow-agent-mode-switch><button class="switch" aria-pressed="true">Agent</button>'
        "</flow-agent-mode-switch>"
    )
    assert not await _verdict(page, NEW_BUILD_NO_SWITCH + switch)


#: The panel open in agent mode; its close renders the compact box at once, gear included,
#: and the switch only `switchLagMs` later.
GEAR_BEFORE_SWITCH = """
<button hidden="" class="settings-trigger-button"></button>
<div id="root"><flow-agent-panel><div class="panel-header"><button><mat-icon>settings_2</mat-icon></button>
  <button id="close"><mat-icon>right_panel_close</mat-icon></button></div>
  <flow-creative-agent-prompt-box><div class="ProseMirror" contenteditable="true"></div></flow-creative-agent-prompt-box>
</flow-agent-panel></div>
<script>
document.getElementById('close').onclick = () => {
  const root = document.getElementById('root');
  root.innerHTML = '<flow-creative-agent-prompt-box><div class="ProseMirror" contenteditable="true"></div>' +
    '<button class="agent-action-button"><mat-icon>settings_2</mat-icon></button></flow-creative-agent-prompt-box>';
  setTimeout(() => {
    root.insertAdjacentHTML('beforeend', '<flow-agent-mode-switch><button class="switch" aria-pressed="true" ' +
      'id="switch">Agent</button></flow-agent-mode-switch>');
    document.getElementById('switch').onclick = e => { window.leftAgentMode = true; e.target.setAttribute('aria-pressed', 'false'); };
  }, 600);
};
</script>
"""


@pytest.mark.asyncio
async def test_a_gear_that_mounts_before_the_switch_does_not_hide_the_switch(page: Page) -> None:
    """The post-close wait ends on the toggle or the older `tune`, not on the new gear, which
    renders in the same compact box as the toggle on recoverable accounts. Ending on the gear
    would read the toggle before it mounted, as absent, and send a recoverable account to
    the agent-only verdict."""
    await page.set_content(GEAR_BEFORE_SWITCH)
    state, error = await MigratedComposer._exit_agent_mode(page)  # pyright: ignore[reportPrivateUsage]
    assert (state, error) == ("clicked", None)
    assert await page.evaluate("window.leftAgentMode === true")
