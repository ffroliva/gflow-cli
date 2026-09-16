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
    UiSelectorDriftError,
    is_retryable,
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
  document.getElementById('grid').prepend(t);  // newest first, as measured
  if (S.dupe) {
    const o = document.createElement('flow-a2ui-image-option');
    o.innerHTML = `<img src="${cdn('image', n)}">`;
    document.getElementById('chat').appendChild(o);
  }
  return t;
}
// Measured 2026-09-15: a video tile is a `flow-pending-tile` (queued, then a percentage)
// until it is REPLACED by the finished tile. On a fresh session the finished tile carries
// `<video src=flow-content.google/video/<uuid>>`; on a re-rendered grid it carries opaque
// `/asb/` media and mounts `<video>` only on hover. The chat option's poster names the uuid,
// and may appear before the clip is ready.
function videoTile(n) {
  const t = document.createElement('flow-video-tile');
  t.innerHTML = '<flow-pending-tile class="queued"><div class="header-leading queued">Queued</div></flow-pending-tile>';
  const opt = document.createElement('flow-a2ui-video-option');
  opt.innerHTML = S.optionEarly ? `<img src="${cdn('image', n)}">` : '<img>';
  document.getElementById('chat').appendChild(opt);
  // optionFirst: the chat names the clip before its pending tile mounts, so for a moment
  // nothing is pending and the newest finished tile is still the OLD one.
  if (S.optionFirst) { setTimeout(() => document.getElementById('grid').prepend(t), S.delay); }
  else document.getElementById('grid').prepend(t);
  setTimeout(() => { t.innerHTML = '<flow-pending-tile><div class="loading-percentage">40%</div></flow-pending-tile>'; }, S.delay);
  if (S.posterOnly) return;
  setTimeout(() => {
    opt.querySelector('img').src = cdn('image', n);
    if (S.readyMedia === 'asb') {
      const asb = `https://flow.google.com/asb/opaque-${n}`;
      t.innerHTML = `<img class="thumbnail" src="${asb}">`;
      t.onmouseenter = () => { if (!S.noHoverVideo && !t.querySelector('video')) t.insertAdjacentHTML('beforeend', `<video src="${asb}"></video>`); };
    } else {
      t.innerHTML = `<video src="${cdn('video', n)}"></video>`;
    }
  }, S.delay * 2);
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
      if (S.kind === 'video') videoTile(n); else tile('image', n);
    }
    setTimeout(() => setSubmit(false), S.delay * 2);
  }, S.delay);
}
function submit() {
  window.log.submitted = document.querySelector('.ProseMirror').innerText;
  // Measured: a re-render swaps an existing tile's cdn media for opaque /asb/ — same clip.
  if (S.rerenderOld) setTimeout(() => {
    const old = document.getElementById('old-video');
    old.innerHTML = '<img class="thumbnail" src="https://flow.google.com/asb/opaque-old">';
  }, S.delay);
  setSubmit(true);
  setTimeout(() => {
    if (S.idle) {
      setSubmit(false);
      if (S.reply) document.getElementById('chat').insertAdjacentHTML('beforeend', `<flow-chat-bubble>${S.reply}</flow-chat-bubble>`);
      return;
    }
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
  p.innerHTML = `<flow-agent-panel><button class="header-action" id="pane-back"><mat-icon>arrow_back</mat-icon></button><flow-settings-view>
    <mat-radio-group>${[0, 1].map(i => `<mat-radio-button><input type="radio" name="c" data-c="${i}" value="${i + 1}"
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
    // Like Angular: the attribute lands after the click handler returns, not during it.
    setTimeout(() => all.forEach(x => x.setAttribute('aria-checked', String(x === b))), S.radioLagMs || 0);
  });
  p.querySelectorAll('input[data-c]').forEach(r => r.onchange = () => { draft.confirm = +r.dataset.c; });
  for (const k of ['image', 'video']) {
    const pick = p.querySelector(`.${k}-model-picker-button`);
    pick.onclick = () => {
      if (S.noMenu) return;
      const menu = document.createElement('div');
      menu.className = 'cdk-overlay-pane';
      menu.innerHTML = MENUS[k].map(t => `<button role="menuitem">${t}</button>`).join('');
      menu.querySelectorAll('[role=menuitem]').forEach(it => it.onclick = () => {
        if (S.deadMenu) { menu.remove(); return; }
        draft.models[k] = it.textContent; pick.firstChild.textContent = it.textContent; menu.remove();
      });
      document.body.appendChild(menu);
    };
  }
  p.querySelector('#pane-back').onclick = () => { p.innerHTML = ''; };  // discards the draft
  p.querySelector('.settings-save-button').onclick = () => {
    Object.assign(state, draft);
    window.log.saves.push(JSON.parse(JSON.stringify(state)));
    p.innerHTML = '';
  };
}
document.getElementById('tune').onclick = renderPane;
setSubmit(false);
if (S.preMedia) tile('image', 1);
if (S.rerenderOld || S.oldVideo) {
  const old = document.createElement('flow-video-tile');
  old.id = 'old-video';
  old.innerHTML = `<video src="${cdn('video', 1)}"></video>`;
  document.getElementById('grid').prepend(old);
}
if (S.staleGate) gate();
if (S.prePending) {
  const busy = document.createElement('flow-video-tile');
  busy.innerHTML = '<flow-pending-tile><div class="loading-percentage">70%</div></flow-pending-tile>';
  document.getElementById('grid').prepend(busy);
}
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
    with pytest.raises(FlowAgentUiError, match="second") as exc:
        await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=10)
    assert (await _log(page))["approvals"] == 1
    _assert_not_retried(exc.value)


@pytest.mark.asyncio
async def test_a_pending_tile_is_not_a_finished_video_even_with_a_chat_poster(page: Page) -> None:
    await _load(page, kind="video", posterOnly=True, optionEarly=True)
    with pytest.raises(TransportTimeoutError):
        await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=1.5)


@pytest.mark.asyncio
async def test_a_re_rendered_old_tile_is_not_ready_while_the_new_clip_is_pending(
    page: Page,
) -> None:
    """Head changed (cdn -> /asb/) and the chat already names the new clip, but a
    `flow-pending-tile` is still there: not ready."""
    await _load(page, kind="video", rerenderOld=True, posterOnly=True, optionEarly=True)
    with pytest.raises(TransportTimeoutError):
        await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=1.5)


@pytest.mark.asyncio
async def test_a_re_rendered_old_tile_is_not_a_new_clip(page: Page) -> None:
    """Head changed and nothing is pending, but no new uuid exists: nothing was made."""
    await _load(page, kind="video", rerenderOld=True, idle=True)
    with pytest.raises(TransportTimeoutError):
        await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=1.5)


@pytest.mark.asyncio
async def test_a_finished_video_with_a_session_cdn_src(page: Page) -> None:
    await _load(page, kind="video")
    (media,) = await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=10)
    assert media.uuid == "00000000-0000-4000-8000-000000000100"
    assert media.src.startswith("https://flow-content.google/video/")


@pytest.mark.asyncio
async def test_a_finished_video_with_opaque_media_is_named_by_the_chat_poster(page: Page) -> None:
    """Live 2026-09-15: a run timed out on a clip that existed — its tile had no uuid."""
    await _load(page, kind="video", readyMedia="asb")
    (media,) = await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=10)
    assert media.uuid == "00000000-0000-4000-8000-000000000100"
    assert media.src == "https://flow.google.com/asb/opaque-100"


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
    with pytest.raises(FlowAgentUiError, match="3") as exc:
        await aoc.AgentOnlyComposer().generate(page, "x", kind="image", count=1, budget_s=10)
    _assert_not_retried(exc.value)


@pytest.mark.asyncio
async def test_a_turn_that_makes_nothing_fails_fast(page: Page) -> None:
    await _load(page, kind="image", idle=True)
    with pytest.raises(FlowAgentUiError, match="no image") as exc:
        await aoc.AgentOnlyComposer().generate(page, "x", kind="image", count=1, budget_s=30)
    _assert_not_retried(exc.value)


@pytest.mark.asyncio
async def test_signed_urls_in_the_agent_reply_never_reach_the_error_text(page: Page) -> None:
    """The last reply is quoted into the error; a signed URL in it must be redacted."""
    reply = "I could not. See https://flow-content.google/image/x?Expires=1&Signature=secret"
    await _load(page, kind="image", idle=True, reply=reply)
    with pytest.raises(FlowAgentUiError, match="I could not") as exc:
        await aoc.AgentOnlyComposer().generate(page, "x", kind="image", count=1, budget_s=30)
    assert "Signature=secret" not in str(exc.value)


def _assert_not_retried(exc: FlowAgentUiError) -> None:
    """After a submit a retry can bill again: never retryable, and never the default advice
    to switch Chrome profile and re-run."""
    assert is_retryable(exc) is False
    assert exc.remediation_hint == aoc._AFTER_SUBMIT_HINT  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_two_clips_queued_for_one_is_a_typed_failure(page: Page) -> None:
    """Review: one gate can cover several clips, so one approval did not bound the spend."""
    await _load(page, kind="video", gates=1, produce=2)
    with pytest.raises(FlowAgentUiError, match="2 videos were queued") as exc:
        await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=10)
    _assert_not_retried(exc.value)


@pytest.mark.asyncio
async def test_a_clip_already_pending_refuses_before_submitting(page: Page) -> None:
    """Review: after a timed-out run the old clip could finish during ours and be returned."""
    await _load(page, kind="video", prePending=True)
    with pytest.raises(FlowAgentUiError, match="still generating"):
        await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=10)
    assert (await _log(page))["submitted"] is None


@pytest.mark.asyncio
async def test_the_old_newest_tile_is_not_returned_while_the_new_clip_has_not_mounted(
    page: Page,
) -> None:
    """The chat names the new clip before its pending tile exists: for that moment nothing is
    pending and the head is the pre-submit tile. Only the head-changed guard waits it out."""
    await _load(page, kind="video", oldVideo=True, optionEarly=True, optionFirst=True, delay=400)
    (media,) = await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=10)
    assert media.uuid == "00000000-0000-4000-8000-000000000100"


@pytest.mark.asyncio
async def test_a_finished_tile_whose_video_never_mounts_keeps_polling(page: Page) -> None:
    """A poster-only tile with nothing pending is not ready; the hover probe must not abort."""
    await _load(page, kind="video", readyMedia="asb", noHoverVideo=True)
    with pytest.raises(TransportTimeoutError):
        await aoc.AgentOnlyComposer().generate(page, "x", kind="video", count=1, budget_s=2)


# --- download: the finished tile's src, redirects only to Google video hosts ----------------


class _Resp:
    def __init__(self, url: str, status: int, body: bytes) -> None:
        self.url, self.status, self._body = url, status, body

    async def body(self) -> bytes:
        return self._body


def _page_answering(resp: _Resp) -> Any:
    page = MagicMock()

    async def get(*_: Any, **__: Any) -> _Resp:
        return resp

    page.request.get = get
    return page


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16


@pytest.mark.asyncio
async def test_an_asb_src_redirected_to_googlevideo_downloads(tmp_path: Any) -> None:
    page = _page_answering(_Resp("https://rr2---sn-x.googlevideo.com/videoplayback?x=1", 200, MP4))
    path = await aoc.download_video(page, "https://flow.google.com/asb/opaque", "u-1", tmp_path)
    assert path.name == "u-1.mp4" and path.read_bytes() == MP4


@pytest.mark.asyncio
async def test_a_redirect_off_google_is_refused(tmp_path: Any) -> None:
    from gflow_cli.errors import WireFormatError

    page = _page_answering(_Resp("https://evil.example/v.mp4", 200, MP4))
    with pytest.raises(WireFormatError, match="evil.example"):
        await aoc.download_video(page, "https://flow.google.com/asb/opaque", "u-1", tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_a_body_that_is_not_an_mp4_is_refused(tmp_path: Any) -> None:
    from gflow_cli.errors import WireFormatError

    page = _page_answering(_Resp("https://rr2---sn-x.googlevideo.com/v", 200, b"<html>nope"))
    with pytest.raises(WireFormatError, match="MP4"):
        await aoc.download_video(page, "https://flow.google.com/asb/opaque", "u-1", tmp_path)


@pytest.mark.asyncio
async def test_a_non_google_src_is_never_requested(tmp_path: Any) -> None:
    from gflow_cli.errors import WireFormatError

    page = MagicMock()
    with pytest.raises(WireFormatError):
        await aoc.download_video(page, "https://evil.example/asb/x", "u-1", tmp_path)
    page.request.get.assert_not_called()


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
async def test_a_radio_that_updates_after_the_click_still_restores(page: Page) -> None:
    """Live 2026-09-15: restore read `aria-checked` in the click's own tick and failed."""
    await _load(page, radioLagMs=300)
    composer = aoc.AgentOnlyComposer()
    snap = await composer.apply_defaults(
        page,
        section="image",
        aspect_ligature="crop_portrait",
        count=2,
        model=None,
        confirm="account",
    )
    await composer.restore_defaults(page, snap)
    assert (await _log(page))["saves"][-1]["groups"] == [0, 1, 0, 0]


@pytest.mark.asyncio
async def test_a_failed_apply_discards_the_draft_instead_of_leaving_the_pane_open(
    page: Page,
) -> None:
    """Review finding: an apply that fails before Save left the pane open with the run's
    unsaved radios, so the next run in the session would snapshot them as the originals."""
    from gflow_cli.api.transports.migrated_composer import ModelMenuMatcher
    from gflow_cli.errors import ConfigurationError

    await _load(page)
    with pytest.raises(ConfigurationError):
        await aoc.AgentOnlyComposer().apply_defaults(
            page,
            section="video",
            aspect_ligature="crop_9_16",
            count=3,
            model=ModelMenuMatcher("A model Flow does not offer"),
            confirm="account",
        )
    assert await page.locator("flow-settings-view").count() == 0
    assert (await _log(page))["saves"] == []


@pytest.mark.asyncio
async def test_a_failed_restore_discards_the_draft_too(page: Page) -> None:
    """Review finding 1: a restore that failed mid-pane left it open, and the next run's
    snapshot took this run's values for the account's originals."""
    from gflow_cli.errors import ConfigurationError

    await _load(page)
    composer = aoc.AgentOnlyComposer()
    snap = aoc.DefaultsSnapshot(
        section="video", aspect_index=1, count_index=0, model_text="Gone from the menu"
    )
    with pytest.raises(ConfigurationError):
        await composer.restore_defaults(page, snap)
    assert await page.locator("flow-settings-view").count() == 0
    assert (await _log(page))["saves"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", [{"deadMenu": True}, {"noMenu": True}])
async def test_a_model_choice_that_does_not_take_is_drift_not_a_silent_old_model(
    page: Page, scenario: dict[str, Any]
) -> None:
    """Review: an unconfirmed menu click would generate, and bill, on the previous model."""
    await _load(page, **scenario)
    with pytest.raises(UiSelectorDriftError, match="model"):
        await aoc.AgentOnlyComposer().apply_defaults(
            page,
            section="video",
            aspect_ligature="crop_9_16",
            count=1,
            model=VIDEO_MODEL_MENU_MATCHERS[video_api.VideoModel.VEO_3_1_LITE],
            confirm="account",
        )
    assert (await _log(page))["saves"] == []
    assert await page.locator("flow-settings-view").count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("confirm", "saved"), [("always", 0), ("never", 1)])
async def test_confirm_is_chosen_by_the_radio_value_not_its_position(
    page: Page, confirm: aoc.Confirm, saved: int
) -> None:
    """Measured 2026-09-16: Always is value 1, Never is value 2. Swap the DOM order and the
    choice must still land on the right one."""
    await _load(page)
    await page.evaluate("document.getElementById('tune').click()")
    await page.evaluate(
        "(() => { const g = document.querySelector('mat-radio-group');"
        " g.prepend(g.lastElementChild); })()"
    )
    await aoc.AgentOnlyComposer().apply_defaults(
        page, section="image", aspect_ligature="crop_16_9", count=1, model=None, confirm=confirm
    )
    (applied,) = (await _log(page))["saves"]
    assert applied["confirm"] == saved


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


@pytest.mark.asyncio
async def test_run_agent_video_reports_the_clip_early_and_returns_it(
    page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The early media id is the crash-recovery record; an async callback must be awaited."""
    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_AGENT_CONFIRM", "account")
    reset_settings()
    await _load(page, kind="video", optionEarly=True)
    seen: list[str] = []

    async def on_started(started: video_api.VideoStarted) -> None:
        seen.append(f"{started.project_id}:{started.media_id}")

    result = await aoc.run_agent_video(
        page,
        video_api.GenerateVideoRequest(prompt="x"),
        project_id="p1",
        out_dir=None,
        poll_timeout_s=10,
        download=False,
        on_started=on_started,
    )
    assert seen == ["p1:00000000-0000-4000-8000-000000000100"]
    assert result.status.media_id == "00000000-0000-4000-8000-000000000100"
    assert len((await _log(page))["saves"]) == 2


@pytest.mark.asyncio
async def test_a_failed_restore_is_logged_and_never_hides_the_result(
    page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Credits were spent and the images exist: a restore failure is reported, not raised."""
    from structlog.testing import capture_logs

    from gflow_cli.config import reset_settings

    monkeypatch.setenv("GFLOW_CLI_AGENT_CONFIRM", "account")
    reset_settings()

    async def broken(*_: Any, **__: Any) -> None:
        raise UiSelectorDriftError(detail="restore broke")

    monkeypatch.setattr(aoc.AgentOnlyComposer, "restore_defaults", broken)
    await _load(page, kind="image")
    with capture_logs() as logs:
        images = await aoc.run_agent_images(
            page, image_api.GenerateImageRequest(prompt="x"), project_id="p1"
        )
    assert len(images) == 1
    (failed,) = [e for e in logs if e["event"] == "migrated.agent_only.defaults_restore_failed"]
    assert failed["section"] == "image"


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


@pytest.mark.asyncio
async def test_a_form_only_the_agent_composer_takes_is_not_refused_up_front(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live 2026-09-15: `--aspect 3:4` exited 36 before the page was opened, because the
    classic pane has no 3:4 radio. The agent-only pane does, so that refusal belongs to
    the classic composer only."""
    from gflow_cli.api.transports import migrated_composer as mc

    async def agent_only(*_: Any, **__: Any) -> str:
        return "agent_only"

    async def fake_images(*_: Any, **__: Any) -> list[str]:
        return ["driven"]

    async def classic(*_: Any, **__: Any) -> str:
        return "classic"

    request = image_api.GenerateImageRequest(
        prompt="x", aspect=image_api.Aspect.PORTRAIT_THREE_FOUR
    )
    page = MagicMock(url="https://flow.google.com/project/p1")
    monkeypatch.setattr(aoc, "run_agent_images", fake_images)

    monkeypatch.setattr(mc.MigratedComposer, "ensure_editor", agent_only)
    assert await mc.run_images(page, request, project_id="p1") == ["driven"]

    monkeypatch.setattr(mc.MigratedComposer, "ensure_editor", classic)
    with pytest.raises(FlowHostMigratedError, match="aspect"):
        await mc.run_images(page, request, project_id="p1")


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
