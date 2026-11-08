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
3. At most **one credit gate** that appeared after our own submit is approved, and only
   after its title has been read: a gate quoting more clips than requested is rejected
   (#988). Gates from earlier sessions survive reloads, so a gate present before submit is
   never ours; a second gate means the agent asked for more than one generation, and the
   run stops without approving it.
4. Completion is observed in the **DOM**, not on the wire: this composer never fires the
   classic ``ogiZ0b`` image reply, and ``as29s`` records name uuids absent from the page.
   An image is a grid tile whose media id (``img[data-media-id]``; the src is an opaque
   ``flow.google.com/asb/...`` 512-px preview, fetched at original size via ``=s0``) was not
   in the pre-submit baseline. A video is ready when no ``flow-pending-tile`` is left, the newest
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
from playwright.async_api import Error as PlaywrightError

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
from gflow_cli.api.video import Aspect, Mode, VideoModel, VideoResult, VideoStarted, VideoStatus
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

    from playwright.async_api import Locator, Page

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import ClipSaver
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
#: Reject is the only `close` row (#988, measured on en and ru, 2026-10-23).
REJECT_ROW = "div[role='radio'].option-row:not(.read-only):has(mat-icon:text-is('close'))"
#: The gate's question: "...kick off this 1 video generation, costing 10 credits?" (en),
#: "Сгенерировать 1 видео за бонусы (10)?" (ru). Its words are translated, its number tokens
#: are not: count first, credits last on both (#988). Like the duration radio's number
#: (#963) that meets the locale-invariance exception bar, and a video gate whose numbers
#: cannot be read is never approved.
GATE_TITLE = "p.title"
LAST_REPLY = "flow-chat-bubble"

#: Toggle groups in DOM order (measured): image aspect, image count, video aspect, video count.
_GROUP_BASE: dict[Section, int] = {"image": 0, "video": 2}
#: "Confirm before generating" radios by their input's own ``value``, not their position.
#: Measured 2026-09-16 (``scripts/dev/spike_agent_confirm_radios.py``): Always is
#: ``value="1"``, Never is ``value="2"``, read against their English labels.
_CONFIRM_VALUE = {"always": "1", "never": "2"}
#: The ``route`` every clip-download failure carries.
_VIDEO_ROUTE = "agent-only:video"

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
#: Said only once a rejected gate was seen to turn read-only.
_REJECTED_HINT = (
    "The credit confirmation was rejected before approval, so nothing was approved. Check "
    "the Flow balance before re-running, and word the prompt so the agent asks for exactly "
    "what was requested."
)
_NUMBER_RE = re.compile(r"\d+")

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
    id: img ? (img.dataset.mediaId || '') : '',
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


#: Measured 2026-10-04: image tiles now load from an opaque `flow.google.com/asb/...` URL and
#: carry the media id on the `<img data-media-id>` attribute instead of in the src.
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


def _tile_uuid(tile: dict[str, Any], kind: str) -> str | None:
    uuid = _cdn_uuid(tile.get("poster", ""), kind)
    if uuid:
        return uuid
    media_id = tile.get("id") or ""
    return media_id.lower() if _UUID_RE.match(media_id) else None


#: A `/asb/` tile src ends in a size spec (measured: `=s512-rw`, a 512-px WebP preview). The
#: spec survives the redirect to `lh3.google.com/rd-asb/`, Google's image server, where `=s0`
#: means the original size. The classic composer never needs this: its ogiZ0b reply carries
#: a signed full-size URL, which the agent-only page never exposes.
_SIZE_SPEC_RE = re.compile(r"[A-Za-z0-9-]+")


def _full_size_src(src: str) -> str:
    """The original-size URL for an `/asb/` image src; any other src unchanged.

    String splits rather than one regex: `https://…/asb/<id>=<spec>` as a pattern backtracks
    super-linearly on a long src (Sonar S8786), and every step here is a single scan.
    """
    head, eq, spec = (src or "").rpartition("=")
    # The `/asb/` must follow the scheme and the head's last `=`, with an id after it.
    path = head[max(len("https://"), head.rfind("=") + 1) :]
    if (
        eq
        and head.startswith("https://")
        and "/asb/" in path[:-1]
        and "?" not in src
        and "#" not in src
        and _SIZE_SPEC_RE.fullmatch(spec)
    ):
        return f"{head}=s0"
    return src


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


def gate_quotes_more(title: str, count: int) -> bool | None:
    """Whether a credit gate's title quotes more than ``count`` generations (#988).

    None while the title is not readable: the count and the credits are both needed, so a
    title rendered halfway ("...kick off this 1") never parses. Every number but the last
    (the credits) is checked, so a digit the prompt slipped in can only refuse, not approve
    - as long as Flow writes the count as a digit, as it did on every measured title.
    """
    numbers = [int(n) for n in _NUMBER_RE.findall(title)]
    if len(numbers) < 2:
        return None
    return any(n > count for n in numbers[:-1])


def _tile_key(tile: dict[str, str]) -> str:
    return tile["video"] or tile["img"]


async def _click(target: Locator, what: str, timeout_ms: int = 4000) -> None:
    """A pre-submit click: a Playwright failure here is drift (exit 23), nothing was sent."""
    try:
        await target.click(timeout=timeout_ms)
    except PlaywrightError as exc:
        raise UiSelectorDriftError(detail=f"agent-only composer: could not click {what}") from exc


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
        await _click(button, "the Agent settings button", timeout_ms=5000)
        try:
            await pane.first.wait_for(state="visible", timeout=int(PANE_S * 1000))
        except PlaywrightError as exc:
            raise UiSelectorDriftError(
                detail=f"agent-only composer: Agent settings ({PANE}) did not open"
            ) from exc

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
        await _click(first, f"the {named} radio {radio!r}")
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
        await _click(picker, f"the {section} model picker")
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
        await _click(items.nth(hits[0]), f"the {section} model {offered[hits[0]]!r}")
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
            try:
                await radio.check(timeout=4000)
            except PlaywrightError as exc:
                raise UiSelectorDriftError(
                    detail=f"agent-only composer: could not check the {confirm!r} confirm radio"
                ) from exc

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
            uuid = _tile_uuid(tile, "image") if tile["tile"] == "flow-image-tile" else None
            if uuid:
                found.setdefault(uuid, TileMedia(uuid, tile["poster"]))
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
            if _UUID_RE.match(tile.get("id") or ""):
                uuids.add(tile["id"].lower())
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
        try:
            # The gate whose title was read, not ``.last``: with two live at once, the
            # newer one's count was never checked (#988 review).
            gate = page.locator(LIVE_GATE).nth(baseline_gates)
            await gate.locator(APPROVE_ROW).first.click(timeout=5000)
        except PlaywrightError as exc:
            # After the submit: whether the gate took the click is unknown, so never retry.
            raise FlowAgentUiError(
                detail="agent-only composer: could not approve the credit confirmation",
                retryable=False,
                remediation_hint=_AFTER_SUBMIT_HINT,
            ) from exc
        # The live-gate count is back at the baseline once the gate at that index is gone.
        settled = page.locator(LIVE_GATE).nth(baseline_gates)
        try:
            await settled.wait_for(state="detached", timeout=GATE_SETTLE_S * 1000)
        except PlaywrightError as exc:
            raise UiSelectorDriftError(
                detail="agent-only composer: an approved credit gate did not turn read-only"
            ) from exc
        log.info("migrated.agent_only.gate_approved")

    async def _gate_turn(self, page: Page, run: _Run) -> bool:
        """Enforce the budget and the one-approval rule; True when a gate was just approved."""
        if time.monotonic() >= run.deadline:
            raise TransportTimeoutError(
                detail=(
                    f"agent-only composer: no finished {run.kind} within {run.budget_s:.0f}s "
                    f"of submit (approvals={run.approvals})"
                ),
                retryable=False,
                remediation_hint=(
                    "The generation may still finish in Flow — check the project before "
                    "re-running, because a re-run submits again (a video spends credits "
                    "again)."
                ),
            )
        live = await page.locator(LIVE_GATE).count()
        if live <= run.baseline_gates:
            return False
        # Two new gates at once: only one can be read before an approval (#988 review).
        if run.approvals or live > run.baseline_gates + 1:
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
        if not await self._gate_within_count(page, run):
            await asyncio.sleep(POLL_S)  # the title is still rendering
            return True
        await self._approve_gate(page, run.baseline_gates)
        run.approvals += 1
        return True

    async def _gate_within_count(self, page: Page, run: _Run) -> bool:
        """False while the gate's title is unreadable; raises when it must not be approved."""
        gate = page.locator(LIVE_GATE).nth(run.baseline_gates)
        try:
            title = await gate.locator(GATE_TITLE).first.inner_text(timeout=2000)
        except PlaywrightError:
            title = ""
        quotes_more = gate_quotes_more(title, run.count)
        shown = redact_sensitive_text(title[:200]) or "(no title)"
        if quotes_more:
            rejected = await self._reject_gate(page, run.baseline_gates)
            log.warning("migrated.agent_only.gate_rejected", count=run.count, settled=rejected)
            raise FlowAgentUiError(
                detail=(
                    "agent-only composer: the credit confirmation quotes more than the "
                    f"{run.count} requested, so it was rejected, not approved. It read: {shown}"
                ),
                retryable=False,
                remediation_hint=_REJECTED_HINT if rejected else _AFTER_SUBMIT_HINT,
            )
        if quotes_more is False:
            return True
        if run.gate_seen_at is None:
            run.gate_seen_at = time.monotonic()
        if time.monotonic() - run.gate_seen_at < GATE_SETTLE_S:
            return False
        if run.kind == "image":
            # Unmeasured title shape. Images cost no credits, and _await_images still
            # refuses a run that made more than requested.
            log.warning("migrated.agent_only.gate_count_unread", kind=run.kind)
            return True
        raise FlowAgentUiError(
            detail=(
                "agent-only composer: could not read how many videos the credit confirmation "
                f"would bill, so it was not approved. It read: {shown}"
            ),
            retryable=False,
            remediation_hint=(
                "Nothing was approved, but the confirmation is still open in the Flow "
                "project: reject it there. Please report its wording in an issue so gflow "
                "can read it."
            ),
        )

    async def _reject_gate(self, page: Page, baseline_gates: int) -> bool:
        """Click Reject; True once the gate is seen to turn read-only."""
        gate = page.locator(LIVE_GATE).nth(baseline_gates)
        try:
            await gate.locator(REJECT_ROW).first.click(timeout=5000)
            await (
                page.locator(LIVE_GATE)
                .nth(baseline_gates)
                .wait_for(state="detached", timeout=GATE_SETTLE_S * 1000)
            )
        except PlaywrightError:
            return False
        return True

    async def _video_baseline(self, page: Page) -> tuple[set[str], str | None]:
        """Clip uuids already named, and the newest finished tile, before the submit."""
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
        head = _tile_key(state0["finished"][0]) if state0["finished"] else None
        return set(_video_uuids(state0)), head

    async def _submit(self, page: Page, directive: str) -> None:
        editor = page.locator(EDITOR).last
        if not await editor.count():
            raise UiSelectorDriftError(detail=f"agent-only composer: editor ({EDITOR}) missing")
        await _click(editor, "the editor", timeout_ms=5000)
        await page.keyboard.press("ControlOrMeta+a")
        await page.keyboard.press("Delete")
        await page.keyboard.insert_text(directive)
        submit = page.locator(SUBMIT).last
        if not await submit.count():
            await _raise_if_out_of_credits(page)
            raise UiSelectorDriftError(detail=f"agent-only composer: submit ({SUBMIT}) missing")
        await _click(submit, "submit", timeout_ms=5000)

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
        if kind == "video":
            baseline, baseline_head = await self._video_baseline(page)
        else:
            baseline, baseline_head = await self._any_uuids(page), None
        await self._submit(page, directive)
        log.info("migrated.agent_only.submitted", kind=kind, count=count)
        run = _Run(kind, budget_s, time.monotonic() + budget_s, baseline_gates, count)
        if kind == "video":
            return await self._await_video(
                page, run, count, baseline, baseline_head, on_first_media
            )
        return await self._await_images(page, run, count, baseline)

    async def _await_video(
        self,
        page: Page,
        run: _Run,
        count: int,
        baseline: set[str],
        baseline_head: str | None,
        on_first_media: Callable[[str], object] | None,
    ) -> list[TileMedia]:
        started = False
        peak_pending = 0
        while True:
            if await self._gate_turn(page, run):
                continue
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
                await _maybe_await(on_first_media(named[0]))
            ready = await self._ready_video(page, state, named, baseline_head)
            if ready is not None:
                return [ready]
            await asyncio.sleep(POLL_S)

    async def _ready_video(
        self, page: Page, state: dict[str, Any], named: list[str], baseline_head: str | None
    ) -> TileMedia | None:
        """This run's finished clip, or None while it is not ready yet."""
        head = state["finished"][0] if state["finished"] else None
        # All three: a uuid names THIS clip, nothing is still pending, and the newest
        # finished tile is not the one that was newest before submit (a re-render alone
        # swaps a tile's cdn media for /asb/ — not a new clip).
        if not (named and head and not state["pending"] and _tile_key(head) != baseline_head):
            return None
        uuid = _cdn_uuid(head["video"], "video") or named[-1]
        src = head["video"] or await self._hover_video_src(page)
        if not src:
            return None
        log.info("migrated.agent_only.video_ready", media_id=uuid)
        return TileMedia(uuid, src)

    async def _await_images(
        self, page: Page, run: _Run, count: int, baseline: set[str]
    ) -> list[TileMedia]:
        idle_since: float | None = None
        while True:
            if await self._gate_turn(page, run):
                continue
            in_flight = bool(await page.locator(IN_FLIGHT).count())
            fresh = {u: m for u, m in (await self._media(page)).items() if u not in baseline}
            if len(fresh) >= count and not in_flight:
                if len(fresh) > count:
                    raise FlowAgentUiError(
                        detail=(
                            f"agent-only composer: {len(fresh)} {run.kind} results appeared "
                            f"where {count} were requested ({', '.join(sorted(fresh))})"
                        ),
                        retryable=False,
                        remediation_hint=_AFTER_SUBMIT_HINT,
                    )
                return list(fresh.values())
            busy = bool(run.approvals or in_flight or fresh)
            idle_since = await self._idle_turn(page, idle_since, busy=busy)
            await asyncio.sleep(POLL_S)

    async def _idle_turn(self, page: Page, since: float | None, *, busy: bool) -> float | None:
        """Fail an image turn that ended with no gate and no tile for ``IDLE_S``."""
        if busy:
            return None
        since = since or time.monotonic()
        if time.monotonic() - since >= IDLE_S:
            raise FlowAgentUiError(
                detail=(
                    "agent-only composer: the agent turn ended with no image and no "
                    f"credit gate. Last reply: {await self._last_reply(page)}"
                ),
                retryable=False,
                remediation_hint=_AFTER_SUBMIT_HINT,
            )
        return since


@dataclass
class _Run:
    """One generation's budget and gate bookkeeping, shared by both result loops."""

    kind: Section
    budget_s: float
    deadline: float
    baseline_gates: int
    count: int
    approvals: int = 0
    #: When this run's gate was first seen with a title that could not be read yet.
    gate_seen_at: float | None = None


async def _maybe_await(value: object) -> None:
    if asyncio.iscoroutine(value):
        await value


def unported_image(request: GenerateImageRequest) -> str | None:
    if request.refs or request.ref_paths:
        return "image-to-image"
    if request.reference_entities or request.reference_entity_names:
        return "character references"
    if request.instructions:
        return "Agent instructions"
    if request.model not in IMAGE_MODEL_MENU_MATCHERS:
        return f"the {request.model.value} model"
    if not 1 <= request.count <= 4:
        return f"a count of {request.count}"
    return None


def unported_video(request: GenerateVideoRequest) -> str | None:
    if request.mode is not Mode.T2V:
        return f"{request.mode.value}"
    if request.reference_entities or request.reference_entity_names:
        return "character references"
    if request.count != 1:
        return "more than one video per request"
    if request.aspect not in _VIDEO_ASPECT_LABEL:
        return f"the {request.aspect.value} aspect ratio"
    if request.model is VideoModel.OMNI_1_2_FLASH:
        return "Omni 1.2 on the agent-only composer (not measured)"
    if request.model is not None and request.model not in VIDEO_MODEL_MENU_MATCHERS:
        return f"the {request.model.value} model"
    if request.resolution is not None:
        return "an explicit resolution"
    return None


def refuse_unported(what: str) -> FlowHostMigratedError:
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
    except Exception as exc:  # noqa: BLE001 - reported and never raised over the real result
        log.warning(
            "migrated.agent_only.defaults_restore_failed",
            section=snapshot.section,
            aspect_index=snapshot.aspect_index,
            count_index=snapshot.count_index,
            model=snapshot.model_text,
            error=str(exc)[:200],
        )


async def download_video(page: Page, src: str, uuid: str, save: ClipSaver) -> Path:
    """Fetch a finished clip from its tile src, prove it is an MP4, and store it by ``save``.

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
            route=_VIDEO_ROUTE,
        )
    resp = await page.request.get(src, timeout=180_000, max_redirects=5)
    final_host = urlsplit(str(resp.url)).hostname or ""
    on_video_cdn = final_host == _VIDEO_REDIRECT_HOST or final_host.endswith(
        f".{_VIDEO_REDIRECT_HOST}"
    )
    if not (on_video_cdn or _is_allowed_download_host(str(resp.url))):
        raise WireFormatError(
            detail=f"agent-only composer: clip download was redirected to {final_host!r}",
            route=_VIDEO_ROUTE,
        )
    if resp.status >= 300:
        raise WireFormatError(
            detail=f"agent-only composer: clip download answered HTTP {resp.status}",
            status=resp.status,
            route=_VIDEO_ROUTE,
        )
    body = await resp.body()
    if body[4:8] != b"ftyp":
        raise WireFormatError(
            detail=(
                f"agent-only composer: the finished clip did not download as an MP4 ({len(body)} B)"
            ),
            route=_VIDEO_ROUTE,
        )
    path = await save(uuid, body)
    log.info("migrated.agent_only.video_downloaded", path=str(path), bytes=len(body))
    return path


async def run_agent_images(
    page: Page,
    request: GenerateImageRequest,
    *,
    project_id: str,
) -> list[GeneratedImage]:
    """Text-to-image on the agent-only composer. Same return shape as the classic path."""
    unported = unported_image(request)
    if unported is not None:
        raise refuse_unported(unported)
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
    images: list[GeneratedImage] = []
    for m in media:
        src = _full_size_src(m.src)
        size = await _natural_size(page, src)
        _warn_on_aspect_mismatch(request.aspect, size)
        images.append(
            GeneratedImage(
                media_name=m.uuid,
                workflow_id="",
                seed=0,
                prompt=request.prompt,
                # #789/#1012: nothing here observes which model rendered, and the agent can
                # ignore the Agent-settings default, so echoing the request would be a guess.
                model_name_type=None,
                aspect_ratio=request.aspect.value,
                fife_url=src,
                dimensions=size,
            )
        )
    return images


#: The tile's `naturalWidth` is the 512-px grid preview's (#824 printed 382x512 for an
#: 896x1200 file), so size the URL the download fetches. 0x0 when it cannot load.
_NATURAL_SIZE_JS = r"""
async (src) => {
  const img = new Image();
  img.src = src;
  try { await img.decode(); } catch (e) { return [0, 0]; }
  return [img.naturalWidth, img.naturalHeight];
}
"""


async def _natural_size(page: Page, src: str) -> tuple[int, int]:
    try:
        w, h = await page.evaluate(_NATURAL_SIZE_JS, src)
    except PlaywrightError:  # the size is a report, never worth failing a billed run over
        w = h = 0
    if not (w and h):
        log.warning("migrated.agent_only.image_size_unknown", src_kind=urlsplit(src).hostname)
    return int(w), int(h)


def _orientation(w: int, h: int) -> int:
    return (w > h) - (w < h)


def _warn_on_aspect_mismatch(aspect: ImageAspect, size: tuple[int, int]) -> None:
    """The directive asks for an aspect the agent may ignore. Warn, don't fail: the run is
    already billed and the file is valid (PLAN decision 4)."""
    w, h = size
    a, b = (int(x) for x in _IMAGE_ASPECT_LABEL[aspect].split(":"))
    if w and h and _orientation(w, h) != _orientation(a, b):
        log.warning("migrated.agent_only.aspect_mismatch", requested=aspect.value, got=f"{w}x{h}")


async def run_agent_video(
    page: Page,
    request: GenerateVideoRequest,
    *,
    project_id: str,
    save: ClipSaver,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
) -> VideoResult:
    """Text-to-video on the agent-only composer. Same return shape as the classic path."""
    unported = unported_video(request)
    if unported is not None:
        raise refuse_unported(unported)
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
    local_path = await download_video(page, media.src, media.uuid, save) if download else None
    return VideoResult(
        status=VideoStatus(media_id=media.uuid, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=local_path,
        project_id=project_id,
    )
