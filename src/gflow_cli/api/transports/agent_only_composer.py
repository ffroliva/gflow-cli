"""Driver for #799's agent-only composer on flow.google.com: text-to-image and text-to-video.

Some accounts are served a composer with no classic arm — the only prompt box is a chat
agent. Evidence for every anchor below:
``docs/superpowers/spikes/2026-09-15-agent-only-composer-drive-surface.md``.

Shape of a run:

1. **Agent settings** carry aspect / count / model as account defaults. They are set to the
   request, saved, and restored afterwards (measured: Save persists across a reload, and a
   set -> Save -> restore -> Save round trip returns the account to its originals). The
   "Confirm before generating" radio follows ``GFLOW_CLI_AGENT_CONFIRM`` and is NOT
   restored — it is the user's standing choice.
2. A natural-language **directive** goes into the ProseMirror editor and is submitted.
3. At most **one credit gate** that appeared after our own submit is approved. Gates from
   earlier sessions survive reloads, so a gate present before submit is never ours; a
   second gate means the agent asked for more than one generation, and the run stops
   without approving it.
4. Completion is observed in the **DOM**, not on the wire: this composer never fires the
   classic ``ogiZ0b`` image reply, and ``as29s`` records name uuids absent from the page.
   An image is a grid tile whose ``flow-content.google/image/<uuid>`` was not in the
   pre-submit baseline. A video is ready when no ``flow-pending-tile`` is left, the newest
   finished tile has changed, and a new uuid is named by that tile or by the chat reply's
   poster — the tile's own media may be opaque ``/asb/`` (no uuid), and its ``<video>``
   then mounts only on hover.

Every selector is structural (component tags, classes, ``mat-icon`` ligatures, roles).
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import structlog

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.transports.migrated_composer import (
    AGENT_ONLY_ANCHOR,
    ASPECT_LIGATURE,
    IMAGE_ASPECT_LIGATURE,
    IMAGE_MODEL_MENU_MATCHERS,
    VIDEO_MODEL_MENU_MATCHERS,
    ModelMenuMatcher,
    _raise_if_out_of_credits,  # pyright: ignore[reportPrivateUsage]
)
from gflow_cli.api.video import Aspect, Mode, VideoResult, VideoStarted, VideoStatus
from gflow_cli.errors import (
    ConfigurationError,
    FlowAgentUiError,
    FlowHostMigratedError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)
from gflow_cli.redaction import redact_sensitive_text

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from playwright.async_api import Page

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import GenerateVideoRequest, VideoStartedCallback

log = structlog.get_logger(__name__)

Section = Literal["image", "video"]
Confirm = Literal["account", "always", "never"]

PANE = "flow-settings-view"
SETTINGS_BUTTON = AGENT_ONLY_ANCHOR
TOGGLE_GROUP = f"{PANE} mat-button-toggle-group"
TOGGLE_RADIO = "button[role='radio']"
CONFIRM_INPUT = f"{PANE} mat-radio-button input[type='radio']"
SAVE_BUTTON = f"{PANE} button.settings-save-button"
#: Closes the pane without saving (measured 2026-09-15: `button.header-action` + `arrow_back`).
PANE_BACK = "flow-agent-panel button:has(mat-icon:text-is('arrow_back'))"
MODEL_PICKER = {
    "image": f"{PANE} button.image-model-picker-button",
    "video": f"{PANE} button.video-model-picker-button",
}
MENU_ITEM = "[role='menuitem']"
EDITOR = "div.ProseMirror"
SUBMIT = "flow-generate-icon-button button"
IN_FLIGHT = "flow-stop-icon-button"
#: A gate still waiting for an answer. Answered or abandoned ones keep `.read-only`.
LIVE_GATE = "flow-permission-message:has(div[role='radio'].option-row:not(.read-only))"
#: Approve is the first `check` row; the second `check` is "Always approve".
APPROVE_ROW = "div[role='radio'].option-row:not(.read-only):has(mat-icon:text-is('check'))"
LAST_REPLY = "flow-chat-bubble"

#: Toggle groups in DOM order (measured): image aspect, image count, video aspect, video count.
_GROUP_BASE: dict[Section, int] = {"image": 0, "video": 2}
#: "Confirm before generating" radios by their input's own ``value``, not their position.
#: Measured 2026-09-16 (``scripts/dev/spike_agent_confirm_radios.py``): Always is
#: ``value="1"``, Never is ``value="2"``, read against their English labels.
_CONFIRM_VALUE = {"always": "1", "never": "2"}

POLL_S = 1.0
#: An image turn that ends with no gate and no tile for this long made nothing.
IDLE_S = 20.0
#: How long an approved gate gets to turn `.read-only` before a re-count.
GATE_SETTLE_S = 10.0
#: Measured: image at ~45 s after submit. Headroom for a busy queue.
IMAGE_BUDGET_S = 240.0
PANE_S = 8.0
#: How long a clicked radio gets to report `aria-checked="true"`.
RADIO_SETTLE_S = 3.0

#: For raises AFTER a submit: credits may already be spent, so FlowAgentUiError's default
#: advice ("try a different Chrome profile") would send an agent to pay a second time.
_AFTER_SUBMIT_HINT = (
    "Check the Flow project and the agent's last reply before submitting again - the request "
    "was already sent, and a re-run submits (and may bill) another one."
)

_CDN_MEDIA_RE = re.compile(
    r"^https://flow-content\.google/(image|video)/([0-9a-fA-F-]{36})(?:[?#]|$)"
)

_TILES_JS = r"""
() => [...document.querySelectorAll('flow-image-tile, flow-video-tile')].flatMap(t => {
  const img = t.querySelector('img');
  const vid = t.querySelector('video');
  return [{
    tile: t.tagName.toLowerCase(),
    poster: img ? (img.currentSrc || img.src || '') : '',
    video: vid ? (vid.currentSrc || vid.src || '') : '',
    w: img ? img.naturalWidth : 0, h: img ? img.naturalHeight : 0,
  }];
})
"""

#: Video state, measured 2026-09-15: a tile holds `flow-pending-tile` (queued, then a
#: percentage) until the finished tile REPLACES it. The finished tile's media is
#: `flow-content.google/video/<uuid>` on the session that made it and opaque `/asb/` once
#: the grid re-renders; the chat option's poster names the uuid either way.
_VIDEO_JS = r"""
() => ({
  pending: document.querySelectorAll('flow-video-tile flow-pending-tile').length,
  finished: [...document.querySelectorAll('flow-video-tile')]
    .filter(t => !t.querySelector('flow-pending-tile'))
    .map(t => {
      const src = (e) => e ? (e.currentSrc || e.src || '') : '';
      return { video: src(t.querySelector('video')), img: src(t.querySelector('img')) };
    }),
  options: [...document.querySelectorAll('flow-a2ui-video-option img')]
    .map(i => i.currentSrc || i.src || ''),
})
"""
FINISHED_VIDEO_TILE = "flow-video-tile:not(:has(flow-pending-tile))"
#: `/asb/` clips redirect to Google's video CDN (measured: `*.googlevideo.com`).
_VIDEO_REDIRECT_HOST = "googlevideo.com"

_IMAGE_ASPECT_LABEL = {
    ImageAspect.PORTRAIT: "9:16",
    ImageAspect.LANDSCAPE: "16:9",
    ImageAspect.SQUARE: "1:1",
    ImageAspect.LANDSCAPE_FOUR_THREE: "4:3",
    ImageAspect.PORTRAIT_THREE_FOUR: "3:4",
}
_VIDEO_ASPECT_LABEL = {Aspect.PORTRAIT: "9:16", Aspect.LANDSCAPE: "16:9"}


@dataclass(frozen=True)
class TileMedia:
    """One finished result on the project grid."""

    uuid: str
    src: str
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class DefaultsSnapshot:
    """What the pane held before a run changed it."""

    section: Section
    aspect_index: int
    count_index: int
    model_text: str


def compose_image_directive(prompt: str, count: int, aspect_label: str) -> str:
    subject = "a picture" if count == 1 else f"{count} pictures"
    return f"Make me {subject} of {prompt} in a {aspect_label} aspect ratio."


def compose_video_directive(prompt: str, duration: int | None, aspect_label: str) -> str:
    length = f"{duration} second " if duration else ""
    return f"Make me a {length}video of {prompt} in a {aspect_label} aspect ratio."


def _cdn_uuid(src: str, kind: str) -> str | None:
    m = _CDN_MEDIA_RE.match(src or "")
    return m.group(2).lower() if m and m.group(1) == kind else None


def _picker_label(text: str) -> str:
    return text.replace("arrow_drop_down", "").strip()


def _video_uuids(state: dict[str, Any]) -> list[str]:
    """Every clip uuid the grid or the chat names, tiles first, in page order."""
    srcs = [t["video"] for t in state["finished"]] + [t["img"] for t in state["finished"]]
    seen: dict[str, None] = {}
    for src in [*srcs, *state["options"]]:
        m = _CDN_MEDIA_RE.match(src or "")
        if m:
            seen.setdefault(m.group(2).lower(), None)
    return list(seen)


def _tile_key(tile: dict[str, str]) -> str:
    return tile["video"] or tile["img"]


class AgentOnlyComposer:
    """Agent settings in and out, directive in, finished tiles out."""

    # --- Agent settings ------------------------------------------------------------------

    async def _open_pane(self, page: Page) -> None:
        pane = page.locator(PANE)
        if await pane.count():
            return
        button = page.locator(SETTINGS_BUTTON).last
        if not await button.count():
            raise UiSelectorDriftError(
                detail=f"agent-only composer: Agent settings button ({SETTINGS_BUTTON}) missing"
            )
        await button.click(timeout=5000)
        await pane.first.wait_for(state="visible", timeout=int(PANE_S * 1000))

    async def _checked_index(self, page: Page, group: int) -> int:
        index = (
            await page.locator(TOGGLE_GROUP)
            .nth(group)
            .evaluate(
                "g => [...g.querySelectorAll(\"button[role='radio']\")]"
                ".findIndex(b => b.getAttribute('aria-checked') === 'true')"
            )
        )
        return int(index)

    async def _check_radio(self, page: Page, group: int, radio: str | int, named: str) -> None:
        """Check one radio, by ligature (aspect) or by position (count, restore)."""
        radios = page.locator(TOGGLE_GROUP).nth(group).locator(TOGGLE_RADIO)
        if isinstance(radio, str):
            target = radios.filter(has=page.locator(f"mat-icon:text-is('{radio}')"))
        else:
            target = radios.nth(radio)
        if not await target.count():
            raise UiSelectorDriftError(
                detail=f"agent-only composer: no {named} radio {radio!r} in Agent settings"
            )
        first = target.first
        if await first.get_attribute("aria-checked") == "true":
            return
        await first.click(timeout=4000)
        # Angular re-renders `aria-checked` after the click, not during it — a read in the
        # same tick saw the old value on a live restore (2026-09-15) and left the account
        # on the run's defaults.
        deadline = time.monotonic() + RADIO_SETTLE_S
        while await first.get_attribute("aria-checked") != "true":
            if time.monotonic() >= deadline:
                raise UiSelectorDriftError(
                    detail=f"agent-only composer: {named} radio {radio!r} did not become checked"
                )
            await asyncio.sleep(0.1)

    async def _choose_model(
        self, page: Page, section: Section, wants: ModelMenuMatcher | str
    ) -> None:
        picker = page.locator(MODEL_PICKER[section]).first
        if not await picker.count():
            raise UiSelectorDriftError(
                detail=f"agent-only composer: {section} model picker missing from Agent settings"
            )

        def hit(text: str) -> bool:
            if isinstance(wants, str):
                return _picker_label(text) == wants
            return wants.matches(text)

        if hit(await picker.inner_text()):
            return
        await picker.click(timeout=4000)
        items = page.locator(MENU_ITEM)
        try:
            await items.first.wait_for(state="visible", timeout=5000)
        except Exception as exc:
            raise UiSelectorDriftError(
                detail=f"agent-only composer: the {section} model menu ({MENU_ITEM}) did not open"
            ) from exc
        offered = [t.strip() for t in await items.all_text_contents()]
        hits = [i for i, text in enumerate(offered) if hit(text)]
        if len(hits) != 1:
            await page.keyboard.press("Escape")
            raise ConfigurationError(
                detail=(
                    f"{section} model matched {len(hits)} Agent-settings entries; "
                    f"offered: {', '.join(offered)}"
                ),
                remediation_hint="Pass a --model that names one offered entry, or omit it.",
            )
        await items.nth(hits[0]).click(timeout=4000)
        # A click can report success and fire nothing; generating on the previous model
        # would bill at the wrong tier with no error, so the picker must read back the hit.
        deadline = time.monotonic() + RADIO_SETTLE_S
        while not hit(await picker.inner_text()):
            if time.monotonic() >= deadline:
                raise UiSelectorDriftError(
                    detail=(
                        f"agent-only composer: chose {offered[hits[0]]!r} but the {section} "
                        "model picker did not change"
                    )
                )
            await asyncio.sleep(0.1)
        log.info("migrated.agent_only.model_selected", section=section, model=offered[hits[0]])

    async def _discard_pane(self, page: Page) -> None:
        """Leave Agent settings WITHOUT saving, through the panel's own back arrow (measured)."""
        back = page.locator(PANE_BACK).last
        try:
            if await back.count():
                await back.click(timeout=4000)
                await page.locator(PANE).first.wait_for(
                    state="detached", timeout=int(PANE_S * 1000)
                )
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the real failure
            log.warning("migrated.agent_only.pane_discard_failed", error=str(exc)[:200])

    async def _save(self, page: Page) -> None:
        try:
            await page.locator(SAVE_BUTTON).first.click(timeout=4000)
            await page.locator(PANE).first.wait_for(state="detached", timeout=int(PANE_S * 1000))
        except Exception as exc:
            raise UiSelectorDriftError(
                detail=f"agent-only composer: Agent settings did not close on Save ({SAVE_BUTTON})"
            ) from exc

    async def _set_confirm(self, page: Page, confirm: Confirm) -> None:
        if confirm == "account":
            return
        radio = page.locator(f"{CONFIRM_INPUT}[value='{_CONFIRM_VALUE[confirm]}']")
        if await radio.count() != 1:
            raise UiSelectorDriftError(
                detail=f"agent-only composer: no single {confirm!r} confirm radio in Agent settings"
            )
        if not await radio.is_checked():
            await radio.check(timeout=4000)

    async def apply_defaults(
        self,
        page: Page,
        *,
        section: Section,
        aspect_ligature: str,
        count: int,
        model: ModelMenuMatcher | None,
        confirm: Confirm,
    ) -> DefaultsSnapshot:
        """Set this run's defaults, Save, and return what to restore."""
        await self._open_pane(page)
        base = _GROUP_BASE[section]
        try:
            snapshot = DefaultsSnapshot(
                section=section,
                aspect_index=await self._checked_index(page, base),
                count_index=await self._checked_index(page, base + 1),
                model_text=_picker_label(
                    await page.locator(MODEL_PICKER[section]).first.inner_text()
                ),
            )
            await self._check_radio(page, base, aspect_ligature, "aspect")
            await self._check_radio(page, base + 1, count - 1, "count")
            if model is not None:
                await self._choose_model(page, section, model)
            await self._set_confirm(page, confirm)
            await self._save(page)
        except Exception:
            # Unsaved radios left in an open pane would be snapshotted by the next run in
            # the session as the account's originals. A no-op once Save has closed it.
            await self._discard_pane(page)
            raise
        log.info(
            "migrated.agent_only.defaults_applied",
            section=section,
            aspect=aspect_ligature,
            count=count,
            confirm=confirm,
        )
        return snapshot

    async def restore_defaults(self, page: Page, snapshot: DefaultsSnapshot) -> None:
        """Put back aspect / count / model. Confirm is deliberately left as applied."""
        await self._open_pane(page)
        base = _GROUP_BASE[snapshot.section]
        try:
            if snapshot.aspect_index >= 0:
                await self._check_radio(page, base, snapshot.aspect_index, "aspect")
            if snapshot.count_index >= 0:
                await self._check_radio(page, base + 1, snapshot.count_index, "count")
            await self._choose_model(page, snapshot.section, snapshot.model_text)
            await self._save(page)
        except Exception:
            # The failed-apply hazard from the other end: left open, the next run would
            # take this run's values for the originals and "restore" them for good.
            await self._discard_pane(page)
            raise
        log.info("migrated.agent_only.defaults_restored", section=snapshot.section)

    # --- generation ----------------------------------------------------------------------

    async def _media(self, page: Page) -> dict[str, TileMedia]:
        """Finished images on the grid, by uuid (a tile and its chat twin share one)."""
        found: dict[str, TileMedia] = {}
        for tile in await page.evaluate(_TILES_JS):
            uuid = _cdn_uuid(tile["poster"], "image") if tile["tile"] == "flow-image-tile" else None
            if uuid:
                found.setdefault(
                    uuid, TileMedia(uuid, tile["poster"], int(tile["w"]), int(tile["h"]))
                )
        return found

    async def _hover_video_src(self, page: Page) -> str:
        """The newest finished tile mounts its `<video>` only while hovered (measured)."""
        tile = page.locator(FINISHED_VIDEO_TILE).first
        try:
            await tile.hover(timeout=5000)
            video = tile.locator("video").first
            await video.wait_for(state="attached", timeout=5000)
            return await video.get_attribute("src") or ""
        except Exception:  # noqa: BLE001 - a poster-only tile is not ready yet; poll again
            return ""
        finally:
            await page.mouse.move(0, 0)

    async def _any_uuids(self, page: Page) -> set[str]:
        uuids: set[str] = set()
        for tile in await page.evaluate(_TILES_JS):
            for src in (tile["poster"], tile["video"]):
                m = _CDN_MEDIA_RE.match(src or "")
                if m:
                    uuids.add(m.group(2).lower())
        return uuids

    async def _last_reply(self, page: Page) -> str:
        try:
            bubbles = page.locator(LAST_REPLY)
            if await bubbles.count():
                return redact_sensitive_text((await bubbles.last.inner_text())[:300])
        except Exception:  # noqa: BLE001 - diagnostics only
            pass
        return ""

    async def _approve_gate(self, page: Page, baseline_gates: int) -> None:
        await page.locator(LIVE_GATE).last.locator(APPROVE_ROW).first.click(timeout=5000)
        deadline = time.monotonic() + GATE_SETTLE_S
        while await page.locator(LIVE_GATE).count() > baseline_gates:
            if time.monotonic() >= deadline:
                raise UiSelectorDriftError(
                    detail="agent-only composer: an approved credit gate did not turn read-only"
                )
            await asyncio.sleep(POLL_S)
        log.info("migrated.agent_only.gate_approved")

    async def generate(
        self,
        page: Page,
        directive: str,
        *,
        kind: Section,
        count: int,
        budget_s: float,
        on_first_media: Callable[[str], object] | None = None,
    ) -> list[TileMedia]:
        """Submit ``directive`` and return the ``count`` new results it produced."""
        baseline_gates = await page.locator(LIVE_GATE).count()
        baseline_head: str | None = None
        if kind == "video":
            state0 = await page.evaluate(_VIDEO_JS)
            if state0["pending"]:
                # A clip still generating (a timed-out earlier run, say) would finish during
                # ours and pass every readiness check as this run's result.
                raise FlowAgentUiError(
                    detail=(
                        "agent-only composer: a video is still generating in this project, so "
                        "a new one could not be told apart from it; nothing was submitted"
                    ),
                    remediation_hint="Wait for the pending clip in the Flow project, then re-run.",
                )
            baseline = set(_video_uuids(state0))
            baseline_head = _tile_key(state0["finished"][0]) if state0["finished"] else None
        else:
            baseline = await self._any_uuids(page)

        editor = page.locator(EDITOR).last
        if not await editor.count():
            raise UiSelectorDriftError(detail=f"agent-only composer: editor ({EDITOR}) missing")
        await editor.click(timeout=5000)
        await page.keyboard.press("ControlOrMeta+a")
        await page.keyboard.press("Delete")
        await page.keyboard.insert_text(directive)
        submit = page.locator(SUBMIT).last
        if not await submit.count():
            await _raise_if_out_of_credits(page)
            raise UiSelectorDriftError(detail=f"agent-only composer: submit ({SUBMIT}) missing")
        await submit.click(timeout=5000)
        log.info("migrated.agent_only.submitted", kind=kind, count=count)

        deadline = time.monotonic() + budget_s
        approvals = 0
        started = False
        peak_pending = 0
        idle_since: float | None = None
        while True:
            if time.monotonic() >= deadline:
                raise TransportTimeoutError(
                    detail=(
                        f"agent-only composer: no finished {kind} within {budget_s:.0f}s of "
                        f"submit (approvals={approvals})"
                    ),
                    remediation_hint=(
                        "The generation may still finish in Flow — check the project before "
                        "re-running, because a re-run submits again (a video spends credits "
                        "again)."
                    ),
                )
            if await page.locator(LIVE_GATE).count() > baseline_gates:
                if approvals:
                    raise FlowAgentUiError(
                        detail=(
                            "agent-only composer: the agent asked for a second credit "
                            "confirmation in one run — it queued more than was requested, so "
                            "the second one was not approved. Last reply: "
                            f"{await self._last_reply(page)}"
                        ),
                        retryable=False,
                        remediation_hint=_AFTER_SUBMIT_HINT,
                    )
                await self._approve_gate(page, baseline_gates)
                approvals += 1
                continue

            in_flight = bool(await page.locator(IN_FLIGHT).count())
            if kind == "video":
                state = await page.evaluate(_VIDEO_JS)
                named = [u for u in _video_uuids(state) if u not in baseline]
                peak_pending = max(peak_pending, state["pending"])
                if peak_pending > count:
                    # One gate can cover several clips (its title names the count), so the
                    # one-approval rule alone does not stop an over-billed run.
                    raise FlowAgentUiError(
                        detail=(
                            f"agent-only composer: {peak_pending} videos were queued where "
                            f"{count} was requested. Last reply: {await self._last_reply(page)}"
                        ),
                        retryable=False,
                        remediation_hint=_AFTER_SUBMIT_HINT,
                    )
                if named and not started and on_first_media is not None:
                    started = True
                    maybe = on_first_media(named[0])
                    if asyncio.iscoroutine(maybe):
                        await maybe
                head = state["finished"][0] if state["finished"] else None
                # All three: a uuid names THIS clip, nothing is still pending, and the
                # newest finished tile is not the one that was newest before submit (a
                # re-render alone swaps a tile's cdn media for /asb/ — not a new clip).
                if named and head and not state["pending"] and _tile_key(head) != baseline_head:
                    uuid = _cdn_uuid(head["video"], "video") or named[-1]
                    src = head["video"] or await self._hover_video_src(page)
                    if not src:
                        await asyncio.sleep(POLL_S)
                        continue
                    log.info("migrated.agent_only.video_ready", media_id=uuid)
                    return [TileMedia(uuid, src)]
                await asyncio.sleep(POLL_S)
                continue
            fresh = {u: m for u, m in (await self._media(page)).items() if u not in baseline}
            if len(fresh) >= count and not in_flight:
                if len(fresh) > count:
                    raise FlowAgentUiError(
                        detail=(
                            f"agent-only composer: {len(fresh)} {kind} results appeared where "
                            f"{count} were requested ({', '.join(sorted(fresh))})"
                        ),
                        retryable=False,
                        remediation_hint=_AFTER_SUBMIT_HINT,
                    )
                return list(fresh.values())
            if kind == "image" and not approvals and not in_flight and not fresh:
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since >= IDLE_S:
                    raise FlowAgentUiError(
                        detail=(
                            "agent-only composer: the agent turn ended with no image and no "
                            f"credit gate. Last reply: {await self._last_reply(page)}"
                        ),
                        retryable=False,
                        remediation_hint=_AFTER_SUBMIT_HINT,
                    )
            else:
                idle_since = None
            await asyncio.sleep(POLL_S)


def _unported_image(request: GenerateImageRequest) -> str | None:
    if request.refs or request.ref_paths:
        return "image-to-image"
    if request.reference_entities:
        return "character references"
    if request.instructions:
        return "Agent instructions"
    if request.model not in IMAGE_MODEL_MENU_MATCHERS:
        return f"the {request.model.value} model"
    if not 1 <= request.count <= 4:
        return f"a count of {request.count}"
    return None


def _unported_video(request: GenerateVideoRequest) -> str | None:
    if request.mode is not Mode.T2V:
        return f"{request.mode.value}"
    if request.reference_entities:
        return "character references"
    if request.count != 1:
        return "more than one video per request"
    if request.aspect not in _VIDEO_ASPECT_LABEL:
        return f"the {request.aspect.value} aspect ratio"
    if request.model is not None and request.model not in VIDEO_MODEL_MENU_MATCHERS:
        return f"the {request.model.value} model"
    return None


def _refuse(what: str) -> FlowHostMigratedError:
    return FlowHostMigratedError(
        detail=(
            "this account is served Flow's agent-only composer, where gflow drives text-to-image "
            f"and text-to-video; {what} is not ported to it yet (#799)"
        )
    )


def _confirm_setting() -> Confirm:
    from gflow_cli.config import get_settings  # noqa: PLC0415

    return get_settings().agent_confirm


async def _restore_quietly(
    composer: AgentOnlyComposer, page: Page, snapshot: DefaultsSnapshot
) -> None:
    """Restore must never mask the run's own outcome; a failure is logged with the originals."""
    try:
        await composer.restore_defaults(page, snapshot)
    except Exception as exc:  # noqa: BLE001 - reported, never raised over the real result
        log.warning(
            "migrated.agent_only.defaults_restore_failed",
            section=snapshot.section,
            aspect_index=snapshot.aspect_index,
            count_index=snapshot.count_index,
            model=snapshot.model_text,
            error=str(exc)[:200],
        )


async def download_video(page: Page, src: str, uuid: str, out_dir: Path | None) -> Path:
    """Fetch a finished clip from its tile src and prove it is an MP4.

    The src is refused unless it is on an allowed Google host, and the response unless it
    ENDED on one: `/asb/` answers with a redirect to Google's video CDN, so redirects are
    followed here (unlike the signed-URL path), and the final host is what is checked.
    """
    from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415 - cycle
        _is_allowed_download_host,  # pyright: ignore[reportPrivateUsage]
    )

    if not _is_allowed_download_host(src):
        raise WireFormatError(
            detail=(
                f"agent-only composer: refusing to download a clip from "
                f"{urlsplit(src).hostname!r} (not an allowed Google host)"
            ),
            route="agent-only:video",
        )
    resp = await page.request.get(src, timeout=180_000, max_redirects=5)
    final_host = urlsplit(str(resp.url)).hostname or ""
    on_video_cdn = final_host == _VIDEO_REDIRECT_HOST or final_host.endswith(
        f".{_VIDEO_REDIRECT_HOST}"
    )
    if not (on_video_cdn or _is_allowed_download_host(str(resp.url))):
        raise WireFormatError(
            detail=f"agent-only composer: clip download was redirected to {final_host!r}",
            route="agent-only:video",
        )
    if resp.status >= 300:
        raise WireFormatError(
            detail=f"agent-only composer: clip download answered HTTP {resp.status}",
            status=resp.status,
            route="agent-only:video",
        )
    body = await resp.body()
    if body[4:8] != b"ftyp":
        raise WireFormatError(
            detail=(
                f"agent-only composer: the finished clip did not download as an MP4 ({len(body)} B)"
            ),
            route="agent-only:video",
        )
    target = out_dir or Path.cwd()
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{uuid}.mp4"
    path.write_bytes(body)
    log.info("migrated.agent_only.video_downloaded", path=str(path), bytes=len(body))
    return path


async def run_agent_images(
    page: Page,
    request: GenerateImageRequest,
    *,
    project_id: str,
) -> list[GeneratedImage]:
    """Text-to-image on the agent-only composer. Same return shape as the classic path."""
    unported = _unported_image(request)
    if unported is not None:
        raise _refuse(unported)
    composer = AgentOnlyComposer()
    snapshot = await composer.apply_defaults(
        page,
        section="image",
        aspect_ligature=IMAGE_ASPECT_LIGATURE[request.aspect],
        count=request.count,
        model=IMAGE_MODEL_MENU_MATCHERS[request.model],
        confirm=_confirm_setting(),
    )
    try:
        media = await composer.generate(
            page,
            compose_image_directive(
                request.prompt, request.count, _IMAGE_ASPECT_LABEL[request.aspect]
            ),
            kind="image",
            count=request.count,
            budget_s=IMAGE_BUDGET_S,
        )
    finally:
        await _restore_quietly(composer, page, snapshot)
    log.info("migrated.agent_only.images_done", project_id=project_id, count=len(media))
    return [
        GeneratedImage(
            media_name=m.uuid,
            workflow_id="",
            seed=0,
            prompt=request.prompt,
            model_name_type=request.model.value,
            aspect_ratio=request.aspect.value,
            fife_url=m.src,
            dimensions=(m.width, m.height),
        )
        for m in media
    ]


async def run_agent_video(
    page: Page,
    request: GenerateVideoRequest,
    *,
    project_id: str,
    out_dir: Path | None,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
) -> VideoResult:
    """Text-to-video on the agent-only composer. Same return shape as the classic path."""
    unported = _unported_video(request)
    if unported is not None:
        raise _refuse(unported)
    composer = AgentOnlyComposer()
    snapshot = await composer.apply_defaults(
        page,
        section="video",
        aspect_ligature=ASPECT_LIGATURE[request.aspect],
        count=1,
        model=VIDEO_MODEL_MENU_MATCHERS[request.model] if request.model else None,
        confirm=_confirm_setting(),
    )

    def started(media_id: str) -> object:
        if on_started is None:
            return None
        return on_started(VideoStarted(media_id=media_id, project_id=project_id))

    try:
        (media,) = await composer.generate(
            page,
            compose_video_directive(
                request.prompt, request.duration, _VIDEO_ASPECT_LABEL[request.aspect]
            ),
            kind="video",
            count=1,
            budget_s=poll_timeout_s,
            on_first_media=started,
        )
    finally:
        await _restore_quietly(composer, page, snapshot)
    local_path = await download_video(page, media.src, media.uuid, out_dir) if download else None
    return VideoResult(
        status=VideoStatus(media_id=media.uuid, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=local_path,
        project_id=project_id,
    )
