"""Driver for #799's agent-only composer on flow.google.com: text-to-image and text-to-video.

Some accounts are served a composer with no classic arm — the only prompt box is a chat
agent. Evidence for every anchor below:
``docs/superpowers/spikes/2026-09-15-agent-only-composer-drive-surface.md``.

Shape of a run:

1. **Agent settings** carry aspect / count / model as account defaults. They are set to the
   request, saved, and restored afterwards (measured: Save persists across a reload, and a
   set -> Save -> restore -> Save round trip returns the account to its originals). The
   "Confirm before generating" radio follows ``GFLOW_CLI_AGENT_CONFIRM`` (or ``always``
   under ``--max-credits``) and is restored afterwards too. Flow's 2026-11-20 build (#1387)
   moved them behind a ``settings_2`` gear into a two-level pane that saves on every click
   and has no Save: there a failed apply is rolled back by writing the snapshot again, and
   the section's summary row is read back before anything is submitted
   (``docs/superpowers/spikes/2026-11-21-agent-settings-on-the-new-build.md``).
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
   then mounts only on hover. When every URL on the page is ``/asb/`` (#1354), the uuid
   comes from the project's ``Zzl0ze`` listing: the record whose poster shares the new
   tile's ``/asb/`` token, finished, not on the page before this run's submit, and created
   after it.

Every selector is structural (component tags, classes, ``mat-icon`` ligatures, roles).
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, urlsplit

import structlog
from playwright.async_api import Error as PlaywrightError

from gflow_cli.api.credit_cap import parse_credit_quote
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import ThinkingLevel
from gflow_cli.api.transports._mc_common import (
    AGENT_PANEL,
    AGENT_PANEL_OPEN,
    AGENT_SETTINGS_GEAR,
    AGENT_SETTINGS_TUNE,
    LISTING_READ_BUDGET_S,
    LISTING_RPC,
    PROMPT_MATCH_MARGIN_S,
)
from gflow_cli.api.transports._mc_submit_observe import read_project_listing
from gflow_cli.api.transports.batchexecute import (
    asb_token,
    generation_record_by_poster,
    rpc_errors,
)
from gflow_cli.api.transports.migrated_composer import (
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
    CreditCapExceededError,
    FlowAgentUiError,
    FlowHostMigratedError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
    nothing_submitted,
)
from gflow_cli.redaction import redact_sensitive_text

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Awaitable, Callable

    from playwright.async_api import Locator, Page

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.transports.migrated_composer import ClipSaver
    from gflow_cli.api.video import GenerateVideoRequest, VideoStartedCallback

    #: Reads the project's `Zzl0ze` listing within a budget in seconds (#1354).
    ListingReader = Callable[[float], Awaitable[Any]]

log = structlog.get_logger(__name__)

Section = Literal["image", "video"]
Confirm = Literal["account", "always", "never"]

PANE = "flow-settings-view"
#: The 2026-09-15 build's opener. The 2026-11-20 build's is `AGENT_SETTINGS_GEAR` (#1387).
SETTINGS_BUTTON = AGENT_SETTINGS_TUNE
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
#: Flow's 2026-11-20 build (#1387, measured 2026-11-21 on zh-CN and en accounts in agent mode).
#: The gear opens the first level: the "Confirm before generating" toggle group (Always,
#: then Never) and one summary row per section. A row opens the section's second level:
#: a model radio list, then toggle groups for aspect (`crop_*` ligatures) and count
#: (x1-x4); the image page adds a thinking-level group that is never touched. Every click
#: saves, per project, through a queue (`AGENT_SETTINGS_SAVE_RPC`); there is no Save button,
#: and the panel header's `arrow_back` steps back one level.
UNIFIED_PANE = "flow-unified-settings-view"
DEFAULTS_PANE = "flow-generation-defaults-view"
UNIFIED_TOGGLE_GROUP = f"{UNIFIED_PANE} mat-button-toggle-group"
DEFAULTS_TOGGLE_GROUP = f"{DEFAULTS_PANE} mat-button-toggle-group"
DEFAULTS_ROW: dict[Section, str] = {
    "image": f"{UNIFIED_PANE} button.image-defaults-row",
    "video": f"{UNIFIED_PANE} button.video-defaults-row",
}
#: The row's summary, e.g. "Omni 1.2 Flash, 16:9, 1 output" (the image row starts with a 🍌).
DEFAULTS_ROW_CAPTION = ".defaults-row-caption"
MODEL_OPTION = f"{DEFAULTS_PANE} flow-radio-group.model-radio-group label.radio-option"
MODEL_OPTION_SELECTED = f"{MODEL_OPTION}.radio-option-selected"
#: The model's name; a retirement note sits beside it in `.radio-option-caption`.
MODEL_OPTION_LABEL = ".radio-option-label"
#: The pane's save: one batchexecute `Kcr7Ub` per click, carrying the whole section (model,
#: aspect, count) under a field mask. Flow sends them one at a time, about 0.5 s apiece
#: (measured 2026-11-21), so the pane shows the new values a second or more before the last
#: save lands, and a page closed or navigated in between drops the rest unsent.
AGENT_SETTINGS_SAVE_RPC = "Kcr7Ub"
#: Quiet time after the last save before the queue counts as drained, and the budget for it.
SAVE_QUIET_S = 1.0
SAVE_BUDGET_S = 20.0
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
#: The same two choices on the 2026-11-20 build, by position in the first level's toggle
#: group: Always first, Never second (the same order on zh-CN and en; labels never read).
_UNIFIED_CONFIRM_INDEX = {"1": 0, "2": 1}
#: The ``route`` every clip-download failure carries.
_VIDEO_ROUTE = "agent-only:video"

POLL_S = 1.0
#: The chat's gates and replies must read the same this many polls in a row before the
#: pre-submit gate count is taken, within `CHAT_SETTLE_S` (headroom, not measured).
CHAT_POLL_S = 0.5
CHAT_STABLE_POLLS = 3
CHAT_SETTLE_S = 10.0
#: Least seconds between two project-listing reads for one clip (#1354): each opens a tab.
LISTING_RETRY_S = 10.0
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
#: the grid re-renders; the chat option's poster then named the uuid. Reported 2026-11-19
#: (#1354): the tile, its hover player and the chat option can all be `/asb/` from the start,
#: so no uuid is on the page at all and the project listing has to name the clip.
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
    confirm_value: str = "1"
    thinking_index: int = -1


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


def _asb_tokens(state: dict[str, Any]) -> set[str]:
    """Every `/asb/` token the grid or the chat shows (#1354)."""
    srcs = [s for t in state["finished"] for s in (t["video"], t["img"])] + state["options"]
    return {token for src in srcs if (token := asb_token(src))}


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


def gate_quoted_credits(title: str) -> int | None:
    """Return a strict, unambiguous quote, excluding the generation count."""
    count = _NUMBER_RE.search(title)
    return parse_credit_quote(title[: count.start()] + title[count.end() :]) if count else None


def _unapproved_media_cap_error(
    max_credits: int, media_id: str | None = None
) -> CreditCapExceededError:
    error = CreditCapExceededError(
        quoted=None,
        max_credits=max_credits,
        route="agent-only composer without an approved quote",
        completed_media_ids=[media_id] if media_id else None,
    )
    error.charged, error.next_action = "maybe", "reconcile"
    error.remediation_hint = (
        "A clip appeared without an approved quote; reconcile the Flow project before retrying."
    )
    return error


async def _report_video_started(
    run: _Run,
    started: bool,
    media_id: str | None,
    pending: int,
    on_first_media: Callable[[str], object] | None,
) -> bool:
    if media_id and not started:
        started = True
        if on_first_media is not None:
            await _maybe_await(on_first_media(media_id))
    if (media_id or pending) and run.max_credits is not None and not run.approvals:
        raise _unapproved_media_cap_error(run.max_credits, media_id)
    return started


def _bare(model: str) -> str:
    """A model name as a summary row and the model list both show it: no banana, no comma."""
    return model.replace("\N{BANANA}", "").strip().rstrip(",\N{FULLWIDTH COMMA}、").strip()


def _tile_key(tile: dict[str, str]) -> str:
    return tile["video"] or tile["img"]


async def _click(target: Locator, what: str, timeout_ms: int = 4000) -> None:
    """A pre-submit click: a Playwright failure here is drift (exit 23), nothing was sent."""
    try:
        await target.click(timeout=timeout_ms)
    except PlaywrightError as exc:
        raise UiSelectorDriftError(detail=f"agent-only composer: could not click {what}") from exc


class _SaveWatch:
    """Counts the 2026-11-20 pane's saves (`AGENT_SETTINGS_SAVE_RPC`) while it is driven.

    The pane shows a click's value at once and queues the save behind the previous one, so
    reading the pane back proves nothing about the account. `settled` waits until every save
    sent has been answered and the queue has been quiet for `SAVE_QUIET_S`: a run that leaves
    earlier, or a browser closed after it, loses the unsent ones (measured 2026-11-21: a
    restore read back correctly on the page, and the next session found the run's values).
    """

    def __init__(self, page: Page) -> None:
        self._page = page
        self.sent = 0
        # The saves themselves, not a count: Playwright fires `response` and then
        # `requestfailed` for a save whose body breaks after its headers.
        self._pending: set[Any] = set()
        self._reads: set[asyncio.Task[None]] = set()
        self.failed: list[str] = []
        self._last = time.monotonic()
        self._sent_from = self._failed_from = 0

    def __enter__(self) -> _SaveWatch:
        self._page.on("request", self._on_request)
        self._page.on("response", self._on_response)
        self._page.on("requestfailed", self._on_failed)
        return self

    def __exit__(self, *exc: object) -> None:
        self._page.remove_listener("request", self._on_request)
        self._page.remove_listener("response", self._on_response)
        self._page.remove_listener("requestfailed", self._on_failed)
        for read in self._reads:
            read.cancel()

    @staticmethod
    def _is_save(url: str) -> bool:
        """Kcr7Ub anywhere in the decoded `rpcids` list: batchexecute can batch RPCs."""
        rpcids = parse_qs(urlsplit(url).query).get("rpcids", [])
        return any(AGENT_SETTINGS_SAVE_RPC in ids.split(",") for ids in rpcids)

    def judge_from_here(self) -> None:
        """Judge `settled` on what follows only: a rollback keeps the apply's record."""
        self._sent_from, self._failed_from = self.sent, len(self.failed)

    def _resolve(self, request: Any, failure: str | None) -> None:
        if request not in self._pending:
            return
        self._pending.discard(request)
        self._last = time.monotonic()
        if failure:
            self.failed.append(failure)

    def _on_request(self, request: Any) -> None:
        if self._is_save(request.url):
            self.sent += 1
            self._pending.add(request)
            self._last = time.monotonic()

    def _on_response(self, response: Any) -> None:
        if not self._is_save(response.url):
            return
        if response.status >= 400:
            self._resolve(response.request, f"HTTP {response.status}")
            return
        # batchexecute reports an RPC's own failure inside a 200, so the save stays pending
        # until its body has been read.
        read = asyncio.ensure_future(self._read(response))
        self._reads.add(read)
        read.add_done_callback(self._reads.discard)

    async def _read(self, response: Any) -> None:
        try:
            body = await response.text()
        except Exception as exc:  # noqa: BLE001 - an unread reply is not a landed save
            log.warning("migrated.agent_only.save_reply_unread", error=str(exc)[:200])
            self._resolve(response.request, f"unreadable reply: {str(exc)[:120]}")
            return
        refused = next((e for e in rpc_errors(body) if e.rpcid == AGENT_SETTINGS_SAVE_RPC), None)
        failure = None
        if refused is not None:
            failure = " ".join([f"{refused.rpcid} status {refused.code}", *refused.reasons])
        self._resolve(response.request, failure)

    def _on_failed(self, request: Any) -> None:
        if self._is_save(request.url):
            self._resolve(request, str(request.failure or "failed"))

    async def settled(self, what: str, *, clicked: bool) -> None:
        """Wait for the queue to drain. ``clicked``: this step made clicks, so saves are due."""
        deadline = time.monotonic() + SAVE_BUDGET_S
        while True:
            quiet = time.monotonic() - self._last >= SAVE_QUIET_S
            if not self._pending and quiet:
                failed = self.failed[self._failed_from :]
                # A failure is reported only once the queue has drained: each save carries the
                # whole section, so the ones behind a failed save still land the values.
                if failed:
                    raise UiSelectorDriftError(
                        detail=(
                            f"agent-only composer: {len(failed)} of {self.sent - self._sent_from} "
                            f"Agent-settings saves ({AGENT_SETTINGS_SAVE_RPC}) failed while "
                            f"{what}: {failed[0]}"
                        )
                    )
                if clicked and self.sent == self._sent_from:
                    raise UiSelectorDriftError(
                        detail=(
                            f"agent-only composer: Agent settings were clicked while {what}, "
                            f"but no Agent-settings save ({AGENT_SETTINGS_SAVE_RPC}) was sent"
                        )
                    )
                return
            if time.monotonic() >= deadline:
                raise UiSelectorDriftError(
                    detail=(
                        f"agent-only composer: {len(self._pending)} Agent-settings saves "
                        f"({AGENT_SETTINGS_SAVE_RPC}) unanswered {SAVE_BUDGET_S:.0f}s after {what}"
                    )
                )
            await asyncio.sleep(0.1)


class _AgentSettingsMixin:
    """Agent settings in and out, on the 2026-09-15 pane and on the 2026-11-20 build's (#1387).

    Split out of `AgentOnlyComposer` (one responsibility per class, #1249); the 2026-11-20
    pane's methods (`_is_unified` onwards) were added here since.
    """

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

    async def _checked_index(self, page: Page, group: int, *, groups: str = TOGGLE_GROUP) -> int:
        """The checked radio's position in the group, or -1 (none checked, or no such group)."""
        located = page.locator(groups)
        # Counted first: `evaluate` on a missing group would wait out Playwright's 30 s.
        if await located.count() <= group:
            return -1
        index = await located.nth(group).evaluate(
            "g => [...g.querySelectorAll(\"button[role='radio']\")]"
            ".findIndex(b => b.getAttribute('aria-checked') === 'true')"
        )
        return int(index)

    async def _check_radio(
        self, page: Page, group: int, radio: str | int, named: str, *, groups: str = TOGGLE_GROUP
    ) -> bool:
        """Check one radio, by ligature (aspect) or by position (count, confirm, restore).

        True when it had to be clicked."""
        radios = page.locator(groups).nth(group).locator(TOGGLE_RADIO)
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
            return False
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
        return True

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
        thinking_level: ThinkingLevel | None = None,
    ) -> DefaultsSnapshot:
        """Set this run's defaults, Save, and return what to restore."""
        with nothing_submitted():  # #1320: settings are bound before any submit
            if await self._is_unified(page):
                return await self._apply_unified(
                    page,
                    section=section,
                    aspect_ligature=aspect_ligature,
                    count=count,
                    model=model,
                    confirm=confirm,
                    thinking_level=thinking_level,
                )
            if section == "image" and thinking_level is not None:
                raise ConfigurationError(
                    detail="agent-only composer: legacy settings pane has no thinking-level control"
                )
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
                    confirm_value=await self._confirm_value(page),
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

    async def _confirm_value(self, page: Page) -> str:
        for value in _CONFIRM_VALUE.values():
            radio = page.locator(f"{CONFIRM_INPUT}[value='{value}']")
            if await radio.count() == 1 and await radio.is_checked():
                return value
        raise UiSelectorDriftError(detail="agent-only composer: confirm choice is unreadable")

    async def restore_defaults(self, page: Page, snapshot: DefaultsSnapshot) -> None:
        """Restore the account's original settings after a run."""
        if await self._is_unified(page):
            await self._restore_unified(page, snapshot)
            return
        await self._open_pane(page)
        base = _GROUP_BASE[snapshot.section]
        try:
            if snapshot.aspect_index >= 0:
                await self._check_radio(page, base, snapshot.aspect_index, "aspect")
            if snapshot.count_index >= 0:
                await self._check_radio(page, base + 1, snapshot.count_index, "count")
            await self._choose_model(page, snapshot.section, snapshot.model_text)
            radio = page.locator(f"{CONFIRM_INPUT}[value='{snapshot.confirm_value}']")
            if await radio.count() == 1 and not await radio.is_checked():
                await radio.check(timeout=4000)
            await self._save(page)
        except Exception:
            # The failed-apply hazard from the other end: left open, the next run would
            # take this run's values for the originals and "restore" them for good.
            await self._discard_pane(page)
            raise
        log.info("migrated.agent_only.defaults_restored", section=snapshot.section)

    # --- Agent settings on Flow's 2026-11-20 build (#1387) -----------------------------------

    async def _is_unified(self, page: Page) -> bool:
        """Is this page's Agent settings the 2026-11-20 build's? The 2026-09-15 pane or its
        `tune` opener wins when present, so a page on the older build keeps its own path."""
        if await page.locator(f"{PANE}, {SETTINGS_BUTTON}").count():
            return False
        return bool(
            await page.locator(f"{UNIFIED_PANE}, {DEFAULTS_PANE}, {AGENT_SETTINGS_GEAR}").count()
        )

    async def _back_from(self, page: Page, pane: str) -> None:
        """One level back through the panel header's `arrow_back`; every click already saved."""
        await _click(page.locator(PANE_BACK).last, "the Agent settings back arrow")
        try:
            await page.locator(pane).first.wait_for(state="hidden", timeout=int(PANE_S * 1000))
        except PlaywrightError as exc:
            raise UiSelectorDriftError(
                detail=f"agent-only composer: {pane} did not close on its back arrow"
            ) from exc

    async def _open_unified(self, page: Page) -> None:
        """Open Agent settings at its first level, from whichever gear is showing.

        The panel header's gear while the agent panel is open, the compact prompt box's
        while it is closed (Flow remembers which, and a closed panel is not in the DOM).
        Exactly one must be visible: #1273's rule, several matches is a miss, never a guess.
        """
        if await page.locator(DEFAULTS_PANE).first.is_visible():
            await self._back_from(page, DEFAULTS_PANE)
        if await page.locator(UNIFIED_PANE).first.is_visible():
            return
        gears = page.locator(AGENT_SETTINGS_GEAR)
        visible = [i for i in range(await gears.count()) if await gears.nth(i).is_visible()]
        if len(visible) != 1:
            raise UiSelectorDriftError(
                detail=(
                    f"agent-only composer: {len(visible)} visible Agent settings gears "
                    f"({AGENT_SETTINGS_GEAR}), so none was clicked"
                )
            )
        await _click(gears.nth(visible[0]), "the Agent settings gear", timeout_ms=5000)
        try:
            await page.locator(UNIFIED_PANE).first.wait_for(
                state="visible", timeout=int(PANE_S * 1000)
            )
        except PlaywrightError as exc:
            raise UiSelectorDriftError(
                detail=f"agent-only composer: Agent settings ({UNIFIED_PANE}) did not open"
            ) from exc

    async def _open_defaults(self, page: Page, section: Section) -> None:
        await self._open_unified(page)
        await _click(page.locator(DEFAULTS_ROW[section]).first, f"the {section} defaults row")
        try:
            await page.locator(DEFAULTS_PANE).first.wait_for(
                state="visible", timeout=int(PANE_S * 1000)
            )
        except PlaywrightError as exc:
            raise UiSelectorDriftError(
                detail=f"agent-only composer: the {section} defaults ({DEFAULTS_PANE}) did not open"
            ) from exc

    async def _leave_unified(self, page: Page) -> None:
        """Back out to the chat, where the run types its directive."""
        if await page.locator(DEFAULTS_PANE).first.is_visible():
            await self._back_from(page, DEFAULTS_PANE)
        if await page.locator(UNIFIED_PANE).first.is_visible():
            await self._back_from(page, UNIFIED_PANE)
        try:
            await page.locator(EDITOR).last.wait_for(state="visible", timeout=int(PANE_S * 1000))
        except PlaywrightError as exc:
            raise UiSelectorDriftError(
                detail="agent-only composer: the prompt box did not come back after Agent settings"
            ) from exc

    async def _unified_confirm_value(self, page: Page) -> str:
        """The confirm choice's value, or "" when it cannot be read."""
        index = await self._checked_index(page, 0, groups=UNIFIED_TOGGLE_GROUP)
        for value, position in _UNIFIED_CONFIRM_INDEX.items():
            if index == position:
                return value
        return ""

    async def _set_unified_confirm(self, page: Page, value: str) -> bool:
        return await self._check_radio(
            page, 0, _UNIFIED_CONFIRM_INDEX[value], "confirm", groups=UNIFIED_TOGGLE_GROUP
        )

    async def _unified_model(self, page: Page) -> str:
        selected = page.locator(MODEL_OPTION_SELECTED)
        if await selected.count() != 1:
            return ""
        # Counted first: `inner_text` on a missing label would wait out Playwright's 30 s.
        label = selected.locator(MODEL_OPTION_LABEL)
        if await label.count() != 1:
            return ""
        return (await label.inner_text()).strip()

    async def _choose_unified_model(
        self, page: Page, section: Section, wants: ModelMenuMatcher | str
    ) -> bool:
        """Select the model; True when it had to be clicked."""

        def hit(text: str) -> bool:
            if isinstance(wants, str):
                return text == wants
            return bool(text) and wants.matches(text)

        if hit(await self._unified_model(page)):
            return False
        options = page.locator(MODEL_OPTION)
        offered = [t.strip() for t in await options.locator(MODEL_OPTION_LABEL).all_inner_texts()]
        if len(offered) != await options.count():
            raise UiSelectorDriftError(
                detail=(
                    f"agent-only composer: a {section} model option has no label "
                    f"({MODEL_OPTION_LABEL})"
                )
            )
        hits = [i for i, text in enumerate(offered) if hit(text)]
        if len(hits) != 1:
            raise ConfigurationError(
                detail=(
                    f"{section} model matched {len(hits)} Agent-settings entries; "
                    f"offered: {', '.join(offered)}"
                ),
                remediation_hint="Pass a --model that names one offered entry, or omit it.",
            )
        await _click(options.nth(hits[0]), f"the {section} model {offered[hits[0]]!r}")
        # The same no-op click hazard as the older pane's menu: read the selection back.
        deadline = time.monotonic() + RADIO_SETTLE_S
        while not hit(await self._unified_model(page)):
            if time.monotonic() >= deadline:
                raise UiSelectorDriftError(
                    detail=(
                        f"agent-only composer: chose {offered[hits[0]]!r} but the {section} "
                        "model did not change"
                    )
                )
            await asyncio.sleep(0.1)
        log.info("migrated.agent_only.model_selected", section=section, model=offered[hits[0]])
        return True

    async def _aspect_label(self, page: Page, ligature: str) -> str:
        """The ``ligature`` radio's own text ("16:9") while it is checked, else ""."""
        checked = (
            page.locator(DEFAULTS_TOGGLE_GROUP)
            .nth(0)
            .locator(f"{TOGGLE_RADIO}[aria-checked='true']")
            .filter(has=page.locator(f"mat-icon:text-is('{ligature}')"))
        )
        if await checked.count() != 1:
            return ""
        # Without the ligature's node: its text runs straight into the label ("crop_16_916:9").
        text = await checked.evaluate(
            "b => { const c = b.cloneNode(true);"
            " c.querySelectorAll('mat-icon').forEach(i => i.remove()); return c.textContent; }"
        )
        return " ".join(str(text).split())

    async def _verify_row(
        self, page: Page, section: Section, *, model: str, aspect: str, count: int
    ) -> str:
        """Read the section's summary row back before anything is submitted.

        Each click saved on its own, so this is the one place where the three values are
        seen together, as Flow will use them. Model and aspect are Flow's own strings; the
        count is the first number after the aspect (number tokens are not translated)."""
        caption = page.locator(DEFAULTS_ROW[section]).first.locator(DEFAULTS_ROW_CAPTION)
        if not await caption.count():
            raise UiSelectorDriftError(
                detail=f"agent-only composer: the {section} defaults row has no summary"
            )
        text = " ".join((await caption.first.inner_text()).split())
        wrong: list[str] = []
        shown = bool(aspect) and aspect in text
        # The model is the whole caption before the aspect, not a substring of it: "Veo 3.1 -
        # Lite" must not pass for "Veo 3.1 - Lite [Lower Priority]".
        if not model or not shown or _bare(text.split(aspect, 1)[0]) != _bare(model):
            wrong.append(f"model {model!r}")
        if not shown:
            wrong.append(f"aspect {aspect!r}")
        numbers = _NUMBER_RE.findall(text.split(aspect, 1)[1]) if shown else []
        if not numbers or int(numbers[0]) != count:
            wrong.append(f"count {count}")
        if wrong:
            raise UiSelectorDriftError(
                detail=(
                    f"agent-only composer: the {section} defaults row reads {text!r}, "
                    f"which does not show {', '.join(wrong)}"
                )
            )
        return text

    async def _set_aspect_and_count(self, page: Page, aspect_ligature: str, count: int) -> bool:
        clicked = await self._check_radio(
            page, 0, aspect_ligature, "aspect", groups=DEFAULTS_TOGGLE_GROUP
        )
        return (
            await self._check_radio(page, 1, count - 1, "count", groups=DEFAULTS_TOGGLE_GROUP)
            or clicked
        )

    async def _set_unified_thinking_level(self, page: Page, thinking_level: ThinkingLevel) -> bool:
        if await page.locator(DEFAULTS_TOGGLE_GROUP).count() < 3:
            raise ConfigurationError(
                detail="agent-only composer: image defaults render no thinking-level control"
            )
        n_radios = await page.locator(DEFAULTS_TOGGLE_GROUP).nth(2).locator(TOGGLE_RADIO).count()
        try:
            target_idx = thinking_level.radio_index(n_radios)
        except ValueError as exc:
            raise ConfigurationError(
                detail=f"agent-only composer: {exc}",
                remediation_hint="Choose a thinking level offered by the Flow defaults pane.",
            ) from exc
        return await self._check_radio(
            page, 2, target_idx, "thinking_level", groups=DEFAULTS_TOGGLE_GROUP
        )

    async def _apply_unified(
        self,
        page: Page,
        *,
        section: Section,
        aspect_ligature: str,
        count: int,
        model: ModelMenuMatcher | None,
        confirm: Confirm,
        thinking_level: ThinkingLevel | None = None,
    ) -> DefaultsSnapshot:
        await self._open_unified(page)
        confirm_value = await self._unified_confirm_value(page)
        await self._open_defaults(page, section)
        want_think = section == "image" and thinking_level is not None
        has_think_before = want_think and await page.locator(DEFAULTS_TOGGLE_GROUP).count() >= 3
        snapshot = DefaultsSnapshot(
            section=section,
            aspect_index=await self._checked_index(page, 0, groups=DEFAULTS_TOGGLE_GROUP),
            count_index=await self._checked_index(page, 1, groups=DEFAULTS_TOGGLE_GROUP),
            model_text=await self._unified_model(page),
            confirm_value=confirm_value,
            thinking_index=(
                await self._checked_index(page, 2, groups=DEFAULTS_TOGGLE_GROUP)
                if has_think_before
                else -1
            ),
        )
        checks = (
            ("confirm", bool(confirm_value)),
            ("model", bool(snapshot.model_text)),
            ("aspect", snapshot.aspect_index >= 0),
            ("count", snapshot.count_index >= 0),
            *(("thinking_level", snapshot.thinking_index >= 0) for _ in (1,) if has_think_before),
        )
        for name, readable in checks:
            if not readable:
                await self._leave_unified(page)
                raise UiSelectorDriftError(
                    detail=f"agent-only composer: the {section} {name} choice is unreadable"
                )
        with _SaveWatch(page) as saves:
            try:
                clicked = await self._set_aspect_and_count(page, aspect_ligature, count)
                if model is not None and await self._choose_unified_model(page, section, model):
                    await self._set_aspect_and_count(page, aspect_ligature, count)
                    clicked = True
                if want_think:
                    assert thinking_level is not None
                    clicked |= await self._set_unified_thinking_level(page, thinking_level)
                aspect = await self._aspect_label(page, aspect_ligature)
                chosen = await self._unified_model(page)
                await self._open_unified(page)
                if confirm != "account":
                    clicked |= await self._set_unified_confirm(page, _CONFIRM_VALUE[confirm])
                row = await self._verify_row(
                    page, section, model=chosen, aspect=aspect, count=count
                )
                await self._leave_unified(page)
                await saves.settled(f"applying the {section} defaults", clicked=clicked)
            except BaseException:  # a cancel too: every click here is already saved
                await self._rollback_unified(page, snapshot, saves)
                raise
        log.info(
            "migrated.agent_only.defaults_applied",
            section=section,
            aspect=aspect_ligature,
            count=count,
            confirm=confirm,
            thinking_level=thinking_level.value if thinking_level else None,
            row=row,
        )
        return snapshot

    async def _rollback_unified(
        self, page: Page, snapshot: DefaultsSnapshot, saves: _SaveWatch
    ) -> None:
        # The apply's failed saves are in the error it raises; the rollback judges its own.
        saves.judge_from_here()
        try:
            await self._write_back(page, snapshot, saves)
        except Exception as exc:  # noqa: BLE001 - the apply's own failure is the one raised
            log.warning(
                "migrated.agent_only.defaults_rollback_failed",
                section=snapshot.section,
                aspect_index=snapshot.aspect_index,
                count_index=snapshot.count_index,
                model=snapshot.model_text,
                confirm=snapshot.confirm_value,
                error=str(exc)[:200],
            )

    async def _restore_unified(self, page: Page, snapshot: DefaultsSnapshot) -> None:
        with _SaveWatch(page) as saves:
            await self._write_back(page, snapshot, saves)
        log.info("migrated.agent_only.defaults_restored", section=snapshot.section)

    async def _write_back(self, page: Page, snapshot: DefaultsSnapshot, saves: _SaveWatch) -> None:
        """Click the originals back and wait until `saves` has seen every queued save land."""
        what = f"restoring the {snapshot.section} defaults"
        try:
            await self._open_unified(page)
            clicked = False
            if snapshot.confirm_value in _UNIFIED_CONFIRM_INDEX:
                clicked = await self._set_unified_confirm(page, snapshot.confirm_value)
            await self._open_defaults(page, snapshot.section)
            # The model first: switching it can put the aspect and count back to its own.
            clicked |= await self._choose_unified_model(page, snapshot.section, snapshot.model_text)
            if snapshot.aspect_index >= 0:
                clicked |= await self._check_radio(
                    page, 0, snapshot.aspect_index, "aspect", groups=DEFAULTS_TOGGLE_GROUP
                )
            if snapshot.count_index >= 0:
                clicked |= await self._check_radio(
                    page, 1, snapshot.count_index, "count", groups=DEFAULTS_TOGGLE_GROUP
                )
            if snapshot.thinking_index >= 0:
                clicked |= await self._check_radio(
                    page, 2, snapshot.thinking_index, "thinking_level", groups=DEFAULTS_TOGGLE_GROUP
                )
            await self._leave_unified(page)
        except BaseException:
            # The clicks made so far are queued saves: let them land and get back to the
            # chat, or a browser closed after this failure drops them.
            await self._drain(page, saves, what)
            raise
        await saves.settled(what, clicked=clicked)

    async def _drain(self, page: Page, saves: _SaveWatch, what: str) -> None:
        """Best effort after a failed write-back; the write-back's own error is the one raised."""
        try:
            await saves.settled(what, clicked=False)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "migrated.agent_only.defaults_drain_failed", what=what, error=str(exc)[:200]
            )
        try:
            await self._leave_unified(page)
        except Exception as exc:  # noqa: BLE001
            log.warning("migrated.agent_only.pane_leave_failed", error=str(exc)[:200])


class AgentOnlyComposer(_AgentSettingsMixin):
    """Agent settings in and out, directive in, finished tiles out."""

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
        gate = page.locator(LIVE_GATE).nth(baseline_gates)
        approve_row = gate.locator(APPROVE_ROW).first
        handle = None
        try:
            # The gate whose title was read, not ``.last``: with two live at once, the
            # newer one's count was never checked (#988 review).
            handle = await approve_row.element_handle(timeout=5000)
            await approve_row.click(timeout=5000)
        except PlaywrightError as exc:
            # After the submit: whether the gate took the click is unknown, so never retry.
            raise FlowAgentUiError(
                detail="agent-only composer: could not approve the credit confirmation",
                retryable=False,
                remediation_hint=_AFTER_SUBMIT_HINT,
            ) from exc
        try:
            # Wait on the approved gate's own element: an index locator re-resolves to the
            # NEXT gate when Flow opens one quickly, and then times out (CI, #1355).
            await page.wait_for_function(
                "el => el.classList.contains('read-only') || !el.isConnected",
                arg=handle,
                timeout=GATE_SETTLE_S * 1000,
            )
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
                    + (
                        f"; last poll: {run.lookup}; pending seen: {run.saw_pending}; "
                        f"listing reads: {run.listing_reads}"
                        if run.kind == "video"
                        else ""
                    )
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
        if title != run.last_gate_title:
            run.last_gate_title = title
            run.stable_gate_polls = 1
            return False
        run.stable_gate_polls += 1
        if run.stable_gate_polls < 2:
            return False
        quotes_more = gate_quotes_more(title, run.count)
        if run.gate_seen_at is None:
            run.gate_seen_at = time.monotonic()
        if not await self._gate_cap_ready(page, run, title):
            return False
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
        if time.monotonic() - run.gate_seen_at < GATE_SETTLE_S:
            return False
        if run.kind == "image":
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

    async def _gate_cap_ready(self, page: Page, run: _Run, title: str) -> bool:
        if run.max_credits is None:
            return True
        max_credits = run.max_credits
        quoted = gate_quoted_credits(title)
        if quoted is None:
            if run.gate_seen_at is None:
                run.gate_seen_at = time.monotonic()
            if time.monotonic() - run.gate_seen_at < GATE_SETTLE_S:
                return False
            await self._reject_cap_gate(page, run, quoted, max_credits)
        elif quoted > max_credits:
            await self._reject_cap_gate(page, run, quoted, max_credits)
        run.quoted_credits = quoted
        return True

    async def _reject_cap_gate(
        self, page: Page, run: _Run, quoted: int | None, max_credits: int
    ) -> None:
        rejected = await self._reject_gate(page, run.baseline_gates)
        error = CreditCapExceededError(
            quoted=quoted,
            max_credits=max_credits,
            route="agent-only confirmation gate",
        )
        if not rejected:
            error.charged, error.next_action = "maybe", "reconcile"
            error.remediation_hint = "The gate is still open in Flow; reconcile before retrying."
        raise error

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
                charged="no",  # #1320: raised before the submit
            )
        head = _tile_key(state0["finished"][0]) if state0["finished"] else None
        return set(_video_uuids(state0)) | _asb_tokens(state0), head

    async def _open_chat(self, page: Page) -> None:
        """Open a closed agent panel before the submit: its chat holds the gate and the reply.

        On the 2026-11-20 build a closed panel is not in the DOM. Two runs submitted from the
        compact box alone saw no gate and no clip within their budget (measured 2026-11-21).
        #1388 closes the panel before the agent-only verdict, so a genuinely agent-only page
        reaches the driver closed. On that build a closed panel with no open control is drift;
        an older build's page without the control is left as it is."""
        panel = page.locator(AGENT_PANEL).first
        if await panel.is_visible():
            return
        opener = page.locator(AGENT_PANEL_OPEN)
        with nothing_submitted():  # #1320: before the submit
            if not await opener.count():
                if await self._is_unified(page):
                    raise UiSelectorDriftError(
                        detail=(
                            "agent-only composer: the agent panel is closed and its open "
                            f"control ({AGENT_PANEL_OPEN}) is missing; the panel's chat holds "
                            "the credit gate, so nothing was submitted"
                        )
                    )
                return
            await _click(opener.first, "the agent panel's open control", timeout_ms=5000)
            try:
                await panel.wait_for(state="visible", timeout=PANE_S * 1000)
            except PlaywrightError as exc:
                raise UiSelectorDriftError(
                    detail="agent-only composer: the agent panel did not open"
                ) from exc
        log.info("migrated.agent_only.panel_opened")

    async def _gate_baseline(self, page: Page) -> int:
        """Live gates before the submit, once the chat's gates and bubbles stop changing.

        Earlier sessions' unanswered gates survive reloads, and a chat history that is still
        rendering would mount one after the count: it would then be approved as this run's
        own, billing an earlier request. Stable counts over `CHAT_STABLE_POLLS` polls are the
        best available sign that the history is in; a chat that never settles is drift."""
        deadline = time.monotonic() + CHAT_SETTLE_S
        seen: list[tuple[int, int]] = []
        with nothing_submitted():  # #1320: before the submit
            while True:
                seen.append(
                    (
                        await page.locator(LIVE_GATE).count(),
                        await page.locator(LAST_REPLY).count(),
                    )
                )
                recent = seen[-CHAT_STABLE_POLLS:]
                if len(recent) == CHAT_STABLE_POLLS and len(set(recent)) == 1:
                    return recent[0][0]
                if time.monotonic() >= deadline:
                    raise UiSelectorDriftError(
                        detail=(
                            f"agent-only composer: the agent chat did not settle within "
                            f"{CHAT_SETTLE_S:.0f}s (gates, replies: {seen[-1]}), so its credit "
                            "gates could not be counted"
                        )
                    )
                await asyncio.sleep(CHAT_POLL_S)

    async def _submit(self, page: Page, directive: str) -> None:
        with nothing_submitted():  # #1320: the click below is the first send
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
        max_credits: int | None = None,
        on_first_media: Callable[[str], object] | None = None,
        read_listing: ListingReader | None = None,
    ) -> list[TileMedia]:
        """Submit ``directive`` and return the ``count`` new results it produced.

        ``read_listing`` returns the project's ``Zzl0ze`` payload (or ``None``); a video
        whose page names no uuid is looked up there (#1354)."""
        self.quoted_credits: int | None = None
        await self._open_chat(page)  # the gates below are counted in the open panel
        baseline_gates = await self._gate_baseline(page)
        if kind == "video":
            baseline, baseline_head = await self._video_baseline(page)
        else:
            baseline, baseline_head = await self._any_uuids(page), None
        submitted_at = time.time()
        await self._submit(page, directive)
        log.info("migrated.agent_only.submitted", kind=kind, count=count)
        run = _Run(
            kind,
            budget_s,
            time.monotonic() + budget_s,
            baseline_gates,
            count,
            submitted_at,
            max_credits,
        )
        run.seen_before = frozenset(baseline)
        if kind == "video":
            media = await self._await_video(
                page, run, count, baseline, baseline_head, on_first_media, read_listing
            )
            self.quoted_credits = run.quoted_credits
            return media
        return await self._await_images(page, run, count, baseline)

    async def _await_video(
        self,
        page: Page,
        run: _Run,
        count: int,
        baseline: set[str],
        baseline_head: str | None,
        on_first_media: Callable[[str], object] | None,
        read_listing: ListingReader | None,
    ) -> list[TileMedia]:
        started = False
        peak_pending = 0
        while True:
            if await self._gate_turn(page, run):
                continue
            state = await page.evaluate(_VIDEO_JS)
            named = [u for u in _video_uuids(state) if u not in baseline]
            peak_pending = max(peak_pending, state["pending"])
            run.saw_pending = run.saw_pending or bool(state["pending"])
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
            started = await _report_video_started(
                run, started, named[0] if named else None, state["pending"], on_first_media
            )
            ready = await self._ready_video(page, state, named, baseline_head, run, read_listing)
            # #1354: the listing may name the clip before its player mounts, or the run may
            # time out waiting for it; the early report must not wait for the download.
            listed = ready.uuid if ready is not None else (run.listed and run.listed[1])
            started = await _report_video_started(run, started, listed, 0, on_first_media)
            if ready is not None:
                return [ready]
            await asyncio.sleep(POLL_S)

    async def _ready_video(
        self,
        page: Page,
        state: dict[str, Any],
        named: list[str],
        baseline_head: str | None,
        run: _Run,
        read_listing: ListingReader | None,
    ) -> TileMedia | None:
        """This run's finished clip, or None while it is not ready yet."""
        head = state["finished"][0] if state["finished"] else None
        # Nothing is still pending, the newest finished tile is not the one that was newest
        # before submit, and a uuid names THIS clip: from the page, or else from the listing
        # record checked in `_listed_uuid` (a re-render alone swaps a tile's cdn media for
        # /asb/, which changes the head without making a clip).
        if not head:
            run.lookup = "no finished video tile mounted"
            return None
        if state["pending"]:
            run.lookup = "a clip is pending"
            return None
        if _tile_key(head) == baseline_head:
            run.lookup = "the newest finished tile is the one from before the submit"
            return None
        if named:
            uuid = _cdn_uuid(head["video"], "video") or named[-1]
        else:
            uuid = await self._listed_uuid(head, run, read_listing)
            # The read takes seconds: the tile hovered below must still be the one it named.
            if uuid is not None and not await _head_carries(page, asb_token(head["img"])):
                run.lookup = "the newest tile changed during the listing read"
                return None
        if uuid is None:
            return None
        src = head["video"] or await self._hover_video_src(page)
        if not src:
            run.lookup = f"clip {uuid} named, its player not mounted"
            return None
        log.info("migrated.agent_only.video_ready", media_id=uuid)
        return TileMedia(uuid, src)

    @staticmethod
    async def _listed_uuid(
        head: dict[str, str], run: _Run, read_listing: ListingReader | None
    ) -> str | None:
        """The uuid of the listed record whose poster shares the tile's `/asb/` token (#1354).

        Only a finished record that was not on the page before the submit, and was created
        after it, counts: an idle agent can re-render the OLD tile to `/asb/`, and its record
        must not pass as this run's clip. The clock check is the second guard, not the first."""
        token = asb_token(head["img"])
        if run.listed is not None and run.listed[0] == token:
            return run.listed[1]  # named already; only the hover player was not mounted yet
        if read_listing is None or token is None:
            run.lookup = (
                "no listing reader"
                if read_listing is None
                else "the newest tile has no /asb/ token"
            )
            return None
        if token in run.seen_before:
            run.lookup = "the newest tile was on the page before the submit"
            return None
        if not run.saw_pending:  # a head change before our clip queues is not our clip
            run.lookup = "this run's clip was never seen pending"
            return None
        if time.monotonic() < run.listing_after:
            return None
        budget = max(1.0, min(LISTING_READ_BUDGET_S, run.deadline - time.monotonic()))
        listing = await read_listing(budget)
        run.listing_reads += 1
        run.listing_after = time.monotonic() + LISTING_RETRY_S
        found = (
            None if listing is None else generation_record_by_poster(LISTING_RPC, listing, token)
        )
        if found is None:
            run.lookup = "the listing " + (
                "did not answer" if listing is None else "has no record for the newest tile"
            )
            log.info("migrated.agent_only.video_not_listed_yet")
            return None
        record, created = found
        if (
            not record.is_done
            or record.media_id.lower() in run.seen_before
            or created is None
            # Second guard; the clock margin matches the classic composer's prompt match.
            or created < run.submitted_at - PROMPT_MATCH_MARGIN_S
        ):
            run.lookup = f"the listed record {record.media_id} is unfinished or not this run's"
            log.info(
                "migrated.agent_only.listed_video_not_ours",
                media_id=record.media_id,
                status=record.status,
                created=created,
            )
            return None
        log.info("migrated.agent_only.video_named_by_listing", media_id=record.media_id)
        run.listed = (token, record.media_id.lower())
        return run.listed[1]

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
    #: Wall-clock time just before the submit click: a listed clip created earlier is not ours.
    submitted_at: float
    max_credits: int | None = None
    quoted_credits: int | None = None
    approvals: int = 0
    #: When this run's gate was first seen with a title that could not be read yet.
    gate_seen_at: float | None = None
    last_gate_title: str = ""
    stable_gate_polls: int = 0
    #: Clip uuids and `/asb/` tokens on the page before the submit: never this run's clip.
    seen_before: frozenset[str] = frozenset()
    #: Whether this run's clip has shown as pending; the listing is not read before that.
    saw_pending: bool = False
    #: Monotonic time before which the project listing is not read again (#1354).
    listing_after: float = 0.0
    #: (tile token, media id) the listing already named: kept while the hover player mounts.
    listed: tuple[str, str] | None = None
    #: Why the last poll did not return a video, and how often the listing was read: the
    #: timeout names both, so an exit 9 says where the run stopped (#1354).
    lookup: str = "nothing polled yet"
    listing_reads: int = 0


async def _head_carries(page: Page, token: str | None) -> bool:
    """Whether the newest finished video tile still shows the `/asb/` ``token`` (#1354)."""
    finished = (await page.evaluate(_VIDEO_JS))["finished"]
    return bool(finished) and asb_token(finished[0]["img"]) == token


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
        thinking_level=request.thinking_level,
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
    confirm = _confirm_setting()
    if request.max_credits is not None and confirm == "never":
        raise CreditCapExceededError(
            quoted=None,
            max_credits=request.max_credits,
            route="agent-only confirmation is disabled",
        )
    composer = AgentOnlyComposer()
    snapshot = await composer.apply_defaults(
        page,
        section="video",
        aspect_ligature=ASPECT_LIGATURE[request.aspect],
        count=1,
        model=VIDEO_MODEL_MENU_MATCHERS[request.model] if request.model else None,
        confirm="always" if request.max_credits is not None else confirm,
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
            max_credits=request.max_credits,
            on_first_media=started,
            read_listing=lambda budget_s: read_project_listing(page, project_id, budget_s=budget_s),
        )
    finally:
        await _restore_quietly(composer, page, snapshot)
    local_path = await download_video(page, media.src, media.uuid, save) if download else None
    return VideoResult(
        status=VideoStatus(media_id=media.uuid, status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
        local_path=local_path,
        project_id=project_id,
        quoted_credits=getattr(composer, "quoted_credits", None),
    )
