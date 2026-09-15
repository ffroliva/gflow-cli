"""#799's agent-only composer driver, against a real CSS engine.

The fixture is a scripted stand-in for the surface measured on 2026-09-15
(`docs/superpowers/spikes/2026-09-15-agent-only-composer-drive-surface.md`): ProseMirror
editor, generate/stop icon buttons, `flow-permission-message` credit gates with
`div[role=radio].option-row` rows that turn `.read-only` once answered, grid tiles whose
media are signed `flow-content.google/{image,video}/<uuid>` URLs, and the Agent settings
pane (aria-checked radios in `mat-button-toggle-group`s, `[role=menuitem]` model menus,
`settings-save-button`). Its markup follows the captures; its timing is invented and short.

No account, no network, no credits; skips when Chromium is absent.
"""

# ruff: noqa: E501 - the fixture is inline HTML/JS; wrapping it would hide its shape

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from gflow_cli.api import image as image_api
from gflow_cli.api import video as video_api
from gflow_cli.api.transports import agent_only_composer as aoc
from gflow_cli.api.transports.migrated_composer import (
    IMAGE_MODEL_MENU_MATCHERS,
    VIDEO_MODEL_MENU_MATCHERS,
)
from gflow_cli.errors import (
    FlowAgentUiError,
    FlowHostMigratedError,
    TransportTimeoutError,
)

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

FIXTURE = r"""
<div id="grid"></div>
<div id="chat"></div>
<flow-creative-agent-prompt-box>
  <flow-rich-text-editor><div class="ProseMirror" contenteditable="true"></div></flow-rich-text-editor>
  <span id="slot"></span>
  <button class="agent-action-button" id="tune"><mat-icon>tune</mat-icon></button>
</flow-creative-agent-prompt-box>
<div id="panel"></div>
<script>
const S = Object.assign({kind: 'image', gates: 0, produce: 1, delay: 120, idle: false,
  staleGate: false, preMedia: false, dupe: false, posterOnly: false}, window.SCENARIO || {});
window.log = {submitted: null, approvals: 0, saves: []};
const state = {groups: [0, 1, 0, 0],
  models: {image: '\u{1F34C} Nano Banana 2', video: 'Omni 1.1 Flash'}, confirm: 0};
const LIGS = [['crop_16_9', 'crop_landscape', 'crop_square', 'crop_portrait', 'crop_9_16'],
  null, ['crop_16_9', 'crop_9_16'], null];
const MENUS = {image: ['\u{1F34C} Nano Banana 2', '\u{1F34C} Nano Banana Pro', 'Nano Banana 2 Lite'],
  video: ['Omni 1.1 Flash', 'Veo 3.1 - Lite', 'Veo 3.1 - Fast', 'Veo 3.1 - Quality']};
const uid = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const cdn = (k, n) => `https://flow-content.google/${k}/${uid(n)}?Expires=1&Signature=secret`;
let seq = 100;
function setSubmit(inFlight) {
  const slot = document.getElementById('slot');
  slot.innerHTML = inFlight
    ? '<flow-stop-icon-button><button class="stop-button"><mat-icon>stop</mat-icon></button></flow-stop-icon-button>'
    : '<flow-generate-icon-button><button class="generate-icon-button"><mat-icon>arrow_forward</mat-icon></button></flow-generate-icon-button>';
  if (!inFlight) slot.querySelector('button').onclick = submit;
}
function tile(kind, n) {
  const t = document.createElement(kind === 'image' ? 'flow-image-tile' : 'flow-video-tile');
  t.innerHTML = `<img src="${cdn('image', n)}">`;
  document.getElementById('grid').appendChild(t);
  if (S.dupe) {
    const o = document.createElement('flow-a2ui-image-option');
    o.innerHTML = `<img src="${cdn('image', n)}">`;
    document.getElementById('chat').appendChild(o);
  }
  return t;
}
function gate() {
  const m = document.createElement('flow-permission-message');
  m.innerHTML = '<div role="radiogroup">' + ['check', 'check', 'close'].map(l =>
    `<div role="radio" class="option-row"><mat-icon>${l}</mat-icon></div>`).join('') + '</div>';
  m.querySelector('.option-row').onclick = () => {
    m.querySelectorAll('.option-row').forEach(r => r.classList.add('read-only'));
    window.log.approvals++;
    if (window.log.approvals < S.gates) setTimeout(gate, S.delay); else produce();
  };
  document.getElementById('chat').appendChild(m);
}
function produce() {
  setSubmit(true);
  setTimeout(() => {
    for (let i = 0; i < S.produce; i++) {
      const n = seq++;
      const t = tile(S.kind, n);
      if (S.kind === 'video' && !S.posterOnly) setTimeout(() =>
        t.insertAdjacentHTML('beforeend', `<video src="${cdn('video', n)}"></video>`), S.delay);
    }
    setTimeout(() => setSubmit(false), S.delay * 2);
  }, S.delay);
}
function submit() {
  window.log.submitted = document.querySelector('.ProseMirror').innerText;
  setSubmit(true);
  setTimeout(() => {
    if (S.idle) { setSubmit(false); return; }
    if (S.gates > 0) { setSubmit(false); gate(); return; }
    produce();
  }, S.delay);
}
function renderPane() {
  const p = document.getElementById('panel');
  const draft = JSON.parse(JSON.stringify(state));
  const groups = LIGS.map((ligs, gi) => '<mat-button-toggle-group>' +
    (ligs || ['x1', 'x2', 'x3', 'x4']).map((l, i) =>
      `<button role="radio" data-g="${gi}" aria-checked="${state.groups[gi] === i}">` +
      `${ligs ? `<mat-icon>${l}</mat-icon>` : l}</button>`).join('') + '</mat-button-toggle-group>');
  p.innerHTML = `<flow-agent-panel><flow-settings-view>
    <mat-radio-group>${[0, 1].map(i => `<mat-radio-button><input type="radio" name="c" data-c="${i}"
      ${state.confirm === i ? 'checked' : ''}></mat-radio-button>`).join('')}</mat-radio-group>
    ${groups[0]}${groups[1]}
    <button class="image-model-picker-button">${state.models.image}<mat-icon>arrow_drop_down</mat-icon></button>
    ${groups[2]}${groups[3]}
    <button class="video-model-picker-button">${state.models.video}<mat-icon>arrow_drop_down</mat-icon></button>
    <button class="settings-save-button">Save</button></flow-settings-view></flow-agent-panel>`;
  p.querySelectorAll('button[role=radio]').forEach(b => b.onclick = () => {
    const g = +b.dataset.g;
    const all = [...p.querySelectorAll(`button[data-g="${g}"]`)];
    draft.groups[g] = all.indexOf(b);
    all.forEach(x => x.setAttribute('aria-checked', String(x === b)));
  });
  p.querySelectorAll('input[data-c]').forEach(r => r.onchange = () => { draft.confirm = +r.dataset.c; });
  for (const k of ['image', 'video']) {
    const pick = p.querySelector(`.${k}-model-picker-button`);
    pick.onclick = () => {
      const menu = document.createElement('div');
      menu.className = 'cdk-overlay-pane';
      menu.innerHTML = MENUS[k].map(t => `<button role="menuitem">${t}</button>`).join('');
      menu.querySelectorAll('[role=menuitem]').forEach(it => it.onclick = () => {
        draft.models[k] = it.textContent; pick.firstChild.textContent = it.textContent; menu.remove();
      });
      document.body.appendChild(menu);
    };
  }
  p.querySelector('.settings-save-button').onclick = () => {
    Object.assign(state, draft);
    window.log.saves.push(JSON.parse(JSON.stringify(state)));
    p.innerHTML = '';
  };
}
document.getElementById('tune').onclick = renderPane;
setSubmit(false);
if (S.preMedia) tile('image', 1);
if (S.staleGate) gate();
</script>
"""


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aoc, "POLL_S", 0.05)
    monkeypatch.setattr(aoc, "IDLE_S", 0.8)
    monkeypatch.setattr(aoc, "GATE_SETTLE_S", 2.0)


async def _load(page: Page, **scenario: Any) -> None:
    await page.set_content(f"<script>window.SCENARIO={json.dumps(scenario)}</script>{FIXTURE}")


async def _log(page: Page) -> dict[str, Any]:
    return await page.evaluate("window.log")


# --- directives -------------------------------------------------------------------------


def test_image_directive() -> None:
    assert (
        aoc.compose_image_directive("a boat", 1, "9:16")
        == "Make me a picture of a boat in a 9:16 aspect ratio."
    )
    assert aoc.compose_image_directive("a boat", 3, "1:1").startswith("Make me 3 pictures of")


def test_video_directive() -> None:
    assert (
        aoc.compose_video_directive("a boat", 4, "9:16")
        == "Make me a 4 second video of a boat in a 9:16 aspect ratio."
    )
    assert (
        aoc.compose_video_directive("a boat", None, "16:9")
        == "Make me a video of a boat in a 16:9 aspect ratio."
    )


# --- generation: gates, baseline, completion ---------------------------------------------


@pytest.mark.asyncio
async def test_a_stale_gate_is_never_approved_and_our_own_gate_is(page: Page) -> None:
    await _load(page, kind="video", gates=1, staleGate=True)
    media = await aoc.AgentOnlyComposer().generate(
        page, "Make me a video", kind="video", count=1, budget_s=10
    )
    assert len(media) == 1 and "/video/" in media[0].src
    log = await _log(page)
    assert log["approvals"] == 1
    assert log["submitted"] == "Make me a video"
    stale_rows = page.locator("flow-permission-message").first.locator(".option-row.read-only")
    assert await stale_rows.count() == 0, "the pre-existing gate was clicked"


@pytest.mark.asyncio
async def test_a_second_gate_stops_the_run_after_one_approval(page: Page) -> None:
    await _load(page, kind="video", gates=2)
    with pytest.raises(FlowAgentUiError, match="second"):
        await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=10)
    assert (await _log(page))["approvals"] == 1


@pytest.mark.asyncio
async def test_a_poster_is_not_a_finished_video(page: Page) -> None:
    await _load(page, kind="video", posterOnly=True)
    with pytest.raises(TransportTimeoutError):
        await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=1.5)


@pytest.mark.asyncio
async def test_duplicates_and_pre_existing_media_are_not_results(page: Page) -> None:
    await _load(page, kind="image", produce=2, dupe=True, preMedia=True)
    media = await aoc.AgentOnlyComposer().generate(page, "x", kind="image", count=2, budget_s=10)
    uuids = [m.uuid for m in media]
    assert len(uuids) == 2 == len(set(uuids))
    assert "00000000-0000-4000-8000-000000000001" not in uuids


@pytest.mark.asyncio
async def test_more_images_than_requested_is_a_typed_failure(page: Page) -> None:
    await _load(page, kind="image", produce=3)
    with pytest.raises(FlowAgentUiError, match="3"):
        await aoc.AgentOnlyComposer().generate(page, "x", kind="image", count=1, budget_s=10)


@pytest.mark.asyncio
async def test_a_turn_that_makes_nothing_fails_fast(page: Page) -> None:
    await _load(page, kind="image", idle=True)
    with pytest.raises(FlowAgentUiError, match="no image"):
        await aoc.AgentOnlyComposer().generate(page, "x", kind="image", count=1, budget_s=30)


@pytest.mark.asyncio
async def test_signed_urls_never_reach_the_error_text(page: Page) -> None:
    await _load(page, kind="image", produce=2)
    with pytest.raises(FlowAgentUiError) as exc:
        await aoc.AgentOnlyComposer().generate(page, "x", kind="image", count=1, budget_s=10)
    assert "Signature" not in str(exc.value)


# --- Agent settings: apply, restore --------------------------------------------------------


@pytest.mark.asyncio
async def test_defaults_are_applied_then_restored_and_confirm_is_kept(page: Page) -> None:
    await _load(page)
    composer = aoc.AgentOnlyComposer()
    snap = await composer.apply_defaults(
        page,
        section="video",
        aspect_ligature="crop_9_16",
        count=2,
        model=VIDEO_MODEL_MENU_MATCHERS[video_api.VideoModel.VEO_3_1_LITE],
        confirm="never",
    )
    await composer.restore_defaults(page, snap)
    saves = (await _log(page))["saves"]
    assert len(saves) == 2
    applied, restored = saves
    assert applied["groups"][2:] == [1, 1]
    assert applied["models"]["video"] == "Veo 3.1 - Lite"
    assert applied["confirm"] == 1
    assert restored["groups"] == [0, 1, 0, 0]
    assert restored["models"]["video"] == "Omni 1.1 Flash"
    assert restored["confirm"] == 1, "confirm is a standing choice, never restored"


@pytest.mark.asyncio
async def test_account_confirm_leaves_the_radio_alone(page: Page) -> None:
    await _load(page)
    await aoc.AgentOnlyComposer().apply_defaults(
        page,
        section="image",
        aspect_ligature="crop_portrait",
        count=1,
        model=IMAGE_MODEL_MENU_MATCHERS[image_api.Model.NARWHAL],
        confirm="account",
    )
    (applied,) = (await _log(page))["saves"]
    assert applied["confirm"] == 0
    assert applied["groups"][:2] == [3, 0]


@pytest.mark.asyncio
async def test_defaults_are_restored_when_generation_fails(
    page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_AGENT_CONFIRM", "account")
    reset_settings()
    await _load(page, kind="image", idle=True)
    request = image_api.GenerateImageRequest(prompt="x", aspect=image_api.Aspect.SQUARE)
    with pytest.raises(FlowAgentUiError):
        await aoc.run_agent_images(page, request, project_id="p1")
    saves = (await _log(page))["saves"]
    assert len(saves) == 2
    assert saves[0]["groups"][:2] == [2, 0]
    assert saves[-1]["groups"] == [0, 1, 0, 0]


# --- routing: the migrated entry points hand an agent-only page to this driver -------------


@pytest.mark.asyncio
async def test_run_images_and_run_video_route_the_agent_only_composer_here(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gflow_cli.api.transports import migrated_composer as mc

    async def agent_only(*_: Any, **__: Any) -> str:
        return "agent_only"

    async def fake_images(*_: Any, **kw: Any) -> list[str]:
        return [f"images:{kw['project_id']}"]

    async def fake_video(*_: Any, **kw: Any) -> str:
        return f"video:{kw['project_id']}"

    monkeypatch.setattr(mc.MigratedComposer, "ensure_editor", agent_only)
    monkeypatch.setattr(aoc, "run_agent_images", fake_images)
    monkeypatch.setattr(aoc, "run_agent_video", fake_video)
    page = MagicMock(url="https://flow.google.com/project/p1")

    images = await mc.run_images(page, image_api.GenerateImageRequest(prompt="x"), project_id="p1")
    video = await mc.run_video(
        page,
        video_api.GenerateVideoRequest(prompt="x"),
        project_id="p1",
        out_dir=None,
        poll_timeout_s=5,
        download=False,
        on_started=None,
    )
    assert images == ["images:p1"]
    assert video == "video:p1"


# --- refused before touching the page -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_",
    [
        image_api.GenerateImageRequest(prompt="x", ref_paths=(MagicMock(),)),
        image_api.GenerateImageRequest(prompt="x", reference_entities=("e1",)),
        image_api.GenerateImageRequest(prompt="x", model=image_api.Model.IMAGEN_3_5),
    ],
)
async def test_unported_image_forms_are_refused(request_: Any) -> None:
    page = MagicMock()
    with pytest.raises(FlowHostMigratedError):
        await aoc.run_agent_images(page, request_, project_id="p1")
    page.locator.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"mode": video_api.Mode.I2V, "start_image": MagicMock()},
        {"count": 2},
        {"aspect": video_api.Aspect.SQUARE},
        {"reference_entities": ("e1",)},
    ],
)
async def test_unported_video_forms_are_refused(changes: dict[str, Any]) -> None:
    import dataclasses

    base = video_api.GenerateVideoRequest(prompt="x")
    request = dataclasses.replace(base, **changes)
    page = MagicMock()
    with pytest.raises(FlowHostMigratedError):
        await aoc.run_agent_video(
            page,
            request,
            project_id="p1",
            out_dir=None,
            poll_timeout_s=5,
            download=False,
            on_started=None,
        )
    page.locator.assert_not_called()
