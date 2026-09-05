"""Drive Flow's migrated ``flow.google.com`` editor (Angular Material) — t2v, and i2v
from a local start frame.

Google is moving accounts from ``labs.google/fx/tools/flow`` onto
``flow.google.com`` (issue #639). The migrated app is the same product on a
different widget toolkit: ligatures live in ``<mat-icon>`` instead of ``<i>``,
the settings popover is a ``cdk-overlay`` pane of ``[role=radiogroup]`` /
``[role=radio]`` buttons instead of ``role=menu`` tabs, the model picker is a
``[role=menu]`` of ``[role=menuitem]``s, and the composer is a ``contenteditable``
(the ``textarea`` next to it is not clickable). On the wire it is ``batchexecute``,
not aisandbox REST: submit is rpcid ``YhhmEf`` (t2v) or ``eb1hJf`` (i2v), the app
then polls ``jwpduf`` every 5 s by itself and fetches the result with ``as29s`` — so
this driver **observes** the page's own traffic and adds none. A start frame goes in
through the editor's own upload (``maseQ``) and its library picker. Recon with
measurements: ``docs/superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md``
and ``docs/superpowers/spikes/2026-09-05-migrated-frames-attach.md``.

Every anchor here is structural or a Material Symbols ligature; the only text
matched is a numeric token (``8s``, ``x2``) or a product name (``Veo 3.1 - Lite``).
``aria-label`` values are translated on this host and are never used.

Selector trap recorded by the spike: Playwright's CSS ``:text-matches('\\s…')``
goes through CSS string escaping, which turns ``\\s`` into ``s`` — labels are
matched with a Python-side ``filter(has_text=re.compile(...))`` instead.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import structlog

from gflow_cli.api.transports._common import extract_project_id
from gflow_cli.api.transports.batchexecute import (
    GenerationRecord,
    generation_record,
    parse_frames,
)
from gflow_cli.api.video import (
    I2V_DEFAULT_MODEL,
    Aspect,
    Mode,
    VideoModel,
    VideoResult,
    VideoStarted,
    VideoStatus,
)
from gflow_cli.errors import (
    ConfigurationError,
    FlowHostMigratedError,
    MediaUploadRejectedError,
    ReferenceNotFoundError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.video import GenerateVideoRequest, VideoStartedCallback

log = structlog.get_logger(__name__)

MIGRATED_PROJECT_URL = "https://flow.google.com/project/{project_id}"
READY_ANCHOR = ".settings-trigger-button"
OVERLAY = ".cdk-overlay-pane"
#: Playwright's own visibility engine — the detached overlays Angular leaves behind are
#: in the DOM but not visible, and only the visible ones can cover the composer.
VISIBLE_OVERLAY = f"{OVERLAY}:visible"
#: Escapes `_close_pane` will spend: exactly the stack depth measured after a model
#: switch (the menu, then the settings pane). No headroom — a third stacked overlay is
#: not something to absorb quietly, and `strict=True` turns it into a named failure.
PANE_CLOSE_ESCAPES = 2
RADIOGROUP = "[role='radiogroup']"
RADIO = "[role='radio']"
MENU_ITEM = "[role='menuitem']"
COMPOSER = "[contenteditable='true']"
#: The submode radio that renders the Start/End frame chips (i2v).
FRAMES_LIGATURE = "crop_free"
DIALOG = "[role='dialog']"
DIALOG_CLOSE = f"{DIALOG} button:has(mat-icon:text-is('close'))"

#: ``YhhmEf`` is the text-to-video submit; ``eb1hJf`` the image-to-video one (a bound
#: Start chip switches the app between them — 2026-09-05 frames spike).
SUBMIT_RPCS = ("YhhmEf", "eb1hJf")
STATUS_RPCS = ("jwpduf", "as29s")
UPLOAD_RPC = "maseQ"
#: The model key Flow puts in the submit body, e.g. ``veo_3_1_i2v_lite``.
MODEL_KEY = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*_(?:t2v|i2v)_[a-z0-9_]+")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

# --- i2v: the toolbar upload path and the Frames picker ---------------------------
#: The toolbar `+` — the only add affordance OUTSIDE the prompt box (the box has its
#: own `add` icons). XPath because CSS cannot express "no such ancestor".
TOOLBAR_ADD = "xpath=//button[.//mat-icon[normalize-space()='add']][not(ancestor::flow-prompt-box)]"
UPLOAD_MENU_ITEM = f"{OVERLAY} {MENU_ITEM}:has(mat-icon:text-is('upload'))"
EMPTY_CHIP = "flow-prompt-box button.empty-chip"
BOUND_CHIP = "flow-prompt-box button.chip-container:has(img)"
PICKER = "flow-add-menu-popover-content"
PICKER_SEARCH = "input[type='text']"
PICKER_OPTION = "button.asset-item[role='option']"
FRAME_PICKER_OPEN_S = 8.0
#: The picker search is server-side (``UpteDb``) and a fresh upload is not always
#: indexed by the first query: on 2026-09-05 a project holding 30+ assets missed it
#: twice within 8 s and listed it on the third search. Each attempt reopens the popover.
FRAME_SEARCH_ATTEMPTS = 3
FRAME_SEARCH_RETRY_PAUSE_S = 2.0
#: ``maseQ`` answered in 1–3 s for a 120 KB PNG; a 20 MB file on a slow link needs more.
FRAME_UPLOAD_S = 60.0
FRAME_COMMIT_HIDDEN_S = 15.0
FRAME_THUMB_VISIBLE_S = 5.0
#: The submit reply arrived 4.0–4.6 s after the click in both measured runs.
SUBMIT_REPLY_BUDGET_S = 60.0
# Angular enables the arrow_forward button ~100 ms after `insert_text` lands in the
# composer (measured 2026-09-05, #670); checking it synchronously reads the stale state.
SUBMIT_ENABLE_BUDGET_S = 5.0
SUBMIT_ENABLE_POLL_S = 0.1
#: A ``jwpduf`` poll reports status 3 first; the record that carries the signed
#: URLs (``as29s``) followed 2–5 s later in every measured run. Wait that long
#: for it before settling for the URL-less record.
RESULT_URL_GRACE_S = 20.0

#: Product names read back verbatim from the live migrated menu (v0.62.1's refusal
#: diagnostic, corroborated by the 2026-09-05 spike). These are the tiers a *not yet
#: moved* account may be routed to the new host for — see :func:`migrated_can_serve`.
VIDEO_MODEL_MENU_LABELS: dict[VideoModel, str] = {
    VideoModel.OMNI_FLASH: "Omni 1.1 Flash",
    VideoModel.VEO_3_1_LITE: "Veo 3.1 - Lite",
    VideoModel.VEO_3_1_FAST: "Veo 3.1 - Fast",
    VideoModel.VEO_3_1_QUALITY: "Veo 3.1 - Quality",
}

#: The suffix Flow appends to a tier it is serving at lower priority. The labs driver
#: has matched ``veo_3_1_lite_lower_priority`` by this tag alone since #539
#: (``[role='menuitem']:has-text('[Lower Priority]')``), because through v0.67.0 no
#: capture had ever rendered the entry — the 2026-08-14 two-account capability matrix,
#: #650's duration capture and v0.61.0's refusal A/B all recorded a picker MISS.
#:
#: It has since been captured: on 2026-09-05 a migrated account rendered
#: ``Veo 3.1 - Lite [Lower Priority]``, and its picker was *defaulted* to that tier —
#: which is presumably why the labs captures missed it, having been taken on accounts
#: Flow was not throttling. Matching stays on the tag rather than moving to that label:
#: it is one account's rendering, the labs driver keys off the same tag, and a tag that
#: Flow appends to whichever tier it throttles survives it moving to another one.
#: Capture: ``docs/superpowers/spikes/2026-09-05-migrated-model-menu-lower-priority.md``.
LOWER_PRIORITY_TAG = "[Lower Priority]"


@dataclass(frozen=True)
class ModelMenuMatcher:
    """How one model's entry is recognised in the migrated model menu.

    ``contains`` must appear in the entry's text and ``excludes`` must not. Every
    ordinary tier excludes :data:`LOWER_PRIORITY_TAG`, because Flow's lower-priority
    entry is its sibling's label plus that suffix: matched as a bare substring,
    ``Veo 3.1 - Lite`` also matches ``Veo 3.1 - Lite [Lower Priority]``. That is the
    ambiguity #539 fixed on labs.google (whose selectors carry
    ``:not(:has-text('[Lower Priority]'))``) and which this port had dropped.
    """

    contains: str
    excludes: str | None = LOWER_PRIORITY_TAG

    def matches(self, text: str) -> bool:
        folded = text.casefold()
        if self.contains.casefold() not in folded:
            return False
        return self.excludes is None or self.excludes.casefold() not in folded


#: Every model the migrated menu can be *driven* to, including the lower-priority Lite
#: tier the routing gate above deliberately does not list.
VIDEO_MODEL_MENU_MATCHERS: dict[VideoModel, ModelMenuMatcher] = {
    **{model: ModelMenuMatcher(label) for model, label in VIDEO_MODEL_MENU_LABELS.items()},
    # No label to exclude a sibling by, and none needed: the tag IS the entry.
    VideoModel.VEO_3_1_LITE_LOWER_PRIORITY: ModelMenuMatcher(LOWER_PRIORITY_TAG, excludes=None),
}
ASPECT_LIGATURE: dict[Aspect, str] = {
    Aspect.LANDSCAPE: "crop_16_9",
    Aspect.PORTRAIT: "crop_9_16",
}


def _unported_form(request: GenerateVideoRequest) -> str | None:
    """The noun for what this request asks of the new host that slice 1 does not
    drive, or ``None`` when the composer takes it. i2v is ported for a **local**
    start frame only: the Frames picker on this host lists assets by display name
    with no UUID in its DOM (2026-09-05 spike), so a frame given by media UUID or
    ``@Name`` has nothing to anchor on yet, and the End chip is unmeasured."""
    if request.mode is Mode.T2V:
        return None
    if request.mode is not Mode.I2V:
        return "reference-to-video"
    if request.end_image or request.end_image_ref_id or request.end_image_ref_name:
        return "an end frame"
    if request.start_image_ref_id:
        return "a frame given by Flow media UUID"
    if request.start_image_ref_name:
        return "a frame given by @Name"
    if not isinstance(request.start_image, Path):
        return "image-to-video without a local start frame"
    return None


def migrated_can_serve(request: GenerateVideoRequest, project_id: str | None) -> bool:
    """Can the migrated composer take this request as it stands? Text-to-video, or
    image-to-video from a **local** start frame, in an existing project, with a model
    the new host offers (or none). Everything else — an end frame, a frame by UUID or
    ``@Name``, r2v media, character references, a fresh project, a labs-only model —
    is not ported yet, so an unmoved account keeps the labs driver for it.

    Gated on :data:`VIDEO_MODEL_MENU_LABELS`, not on the wider
    :data:`VIDEO_MODEL_MENU_MATCHERS`: this decides whether to *move* a request off
    labs.google, and pulling one there for a tier no capture has ever seen rendered
    would trade a working driver for an unverified one. An account Flow has already
    moved is routed by its URL and never reaches this question — for it,
    ``--model veo-lite-lp`` is driven by its matcher instead of refused outright."""
    if _unported_form(request) is not None or not project_id:
        return False
    if request.reference_entities:
        return False
    return request.model is None or request.model in VIDEO_MODEL_MENU_LABELS


def _exact(label: str) -> re.Pattern[str]:
    return re.compile(r"^\s*" + re.escape(label) + r"\s*$")


def _ligature(page: Any, name: str) -> Any:
    """A ``mat-icon`` whose ligature text is exactly ``name`` — for ``filter(has=…)``."""
    return page.locator("mat-icon").filter(has_text=_exact(name))


def _rpcid(url: str) -> str | None:
    m = re.search(r"[?&]rpcids=([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def _first_uuid(text: str) -> str | None:
    """The first UUID in a ``batchexecute`` reply — for ``maseQ`` that is the new
    media id (``[media_id, project_id, …]``, measured 2026-09-05)."""
    for rid, payload in parse_frames(text):
        if rid != UPLOAD_RPC:
            continue
        m = UUID_RE.search(str(payload))
        if m:
            return m.group(0)
    return None


def _post_data(request: Any) -> str:
    """The POST body as text, or ``""`` when Playwright cannot decode it —
    a listener must not raise."""
    try:
        return str(request.post_data or "")
    except Exception:  # noqa: BLE001 - Playwright's post_data raises on undecodable bytes
        return ""


def _i2v_body_problem(body: str, rpcid: str, media_id: str) -> str | None:
    """Why this submit body is NOT the image-to-video generation the user asked for,
    or ``None``. A t2v key means the chip was empty at submit time; a body without
    the uploaded id means the picker bound some other asset."""
    if not body:
        return (
            f"migrated host: the {rpcid} submit body could not be read, so the "
            "image-to-video request could not be confirmed before Flow acted on it"
        )
    key = MODEL_KEY.search(body)
    key_text = key.group(0) if key else "no model key"
    if "_t2v_" in body or "_i2v_" not in body:
        return (
            f"migrated host: the submit went out on {rpcid} with a text-to-video model key "
            f"({key_text}) for an image-to-video (i2v) request — the start frame was not "
            "bound when the app submitted (the labs #125 shape on this host)"
        )
    if media_id not in body:
        other = [u for u in UUID_RE.findall(body) if u.lower() != media_id.lower()]
        return (
            f"migrated host: the {rpcid} submit body does not carry the uploaded start "
            f"frame {media_id} (ids in the body: {', '.join(other[:4]) or 'none'}) — the "
            "picker bound a different asset"
        )
    return None


class MigratedComposer:
    """Settings → prompt → submit → observe, against the migrated editor."""

    # --- readiness ------------------------------------------------------------

    async def ensure_editor(self, page: Page, project_id: str, *, timeout_s: float = 30.0) -> None:
        """Land on ``flow.google.com/project/<id>`` (direct — no labs.google visit
        needed on either kind of account) and wait for the settings trigger."""
        target = MIGRATED_PROJECT_URL.format(project_id=project_id)
        current = str(getattr(page, "url", "") or "")
        if not current.startswith(target):
            log.info("migrated.navigate", url=target)
            await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        await self._dismiss_dialog(page)
        try:
            await page.locator(READY_ANCHOR).first.wait_for(
                state="visible", timeout=int(timeout_s * 1000)
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the settings trigger ({READY_ANCHOR}) did not become "
                    f"visible within {timeout_s:.0f}s on {page.url} (host=migrated): {e}"
                ),
            ) from e
        log.info("migrated.editor_ready", url=page.url)

    @staticmethod
    async def _dismiss_dialog(page: Page) -> None:
        """One attempt at the modal the host shows over a fresh editor (the "Get
        started" changelog on a first visit, #593's twin): its ``close`` icon button,
        else Escape. Best-effort — a dialog that stays is reported by whichever
        click it blocks next, with the overlay named there."""
        dialog = page.locator(DIALOG).first
        try:
            if not await dialog.is_visible():
                return
            close = page.locator(DIALOG_CLOSE).first
            via = "close" if await close.count() else "escape"
            if via == "close":
                await close.click(timeout=3000)
            else:
                await page.keyboard.press("Escape")
            log.info("migrated.dialog_dismissed", via=via)
        except Exception as e:  # noqa: BLE001 - best-effort, never the failure itself
            log.warning("migrated.dialog_not_dismissed", error=str(e)[:200])

    # --- settings ---------------------------------------------------------------

    async def apply_video_settings(self, page: Page, request: GenerateVideoRequest) -> None:
        """Mode, model, aspect, duration, count — through the radios, with read-back.

        Model goes first: like on labs.google the duration row is model-state.
        """
        pane = await self._open_pane(page)
        try:
            await self._select(page, pane, axis="mode", lig="videocam")
            if request.mode is Mode.I2V:
                # Frames renders the Start/End chips the attach stage binds to. Flow
                # remembers the last submode per account, so it is set, not assumed.
                await self._select(page, pane, axis="submode", lig=FRAMES_LIGATURE)
            model = request.model
            if model is None and request.mode is Mode.I2V:
                # #125: for i2v, "no --model" is not "no opinion". The labs path binds
                # I2V_DEFAULT_MODEL before submitting; without the same bind here the
                # editor submits on whichever tier it last remembered — which on a
                # queued MCP request (its payload carries model=None) can be a
                # 100-credit tier for a run the caller expected to cost 10.
                model = I2V_DEFAULT_MODEL
                log.info("migrated.i2v_model_defaulted", model=model.value, issue_ref="#125")
            if model is not None:
                await self._select_model(page, pane, model)
            await self._select(page, pane, axis="aspect", lig=ASPECT_LIGATURE[request.aspect])
            if request.duration is not None:
                await self._select(page, pane, axis="duration", text=f"{request.duration}s")
            await self._select(page, pane, axis="count", text=f"x{request.count}")
            log.info(
                "migrated.settings_applied",
                aspect=request.aspect.value,
                duration=request.duration,
                count=request.count,
                # The EFFECTIVE model — `request.model` is None on an i2v run that
                # took the #125 default, and logging that read as "no model bound".
                model=model.value if model else None,
            )
        except BaseException:
            # A close failure must never replace the error that is already travelling.
            # `_close_pane` in a bare `finally` did exactly that: a `--model` Flow does
            # not offer raises ConfigurationError (exit 11) naming the offered models,
            # and a stuck pane then overwrote it with UiSelectorDriftError (exit 23),
            # losing the list the user needed. On this path the pane is best-effort.
            await self._close_pane(page, strict=False)
            raise
        await self._close_pane(page, strict=True)

    async def _open_pane(self, page: Page) -> Any:
        trigger = page.locator(READY_ANCHOR).first
        if not await trigger.count():
            raise UiSelectorDriftError(
                detail=f"migrated host: settings trigger ({READY_ANCHOR}) missing (host=migrated)"
            )
        await trigger.click(timeout=5000)
        # THE overlay that holds the option groups — not `.last`: once the model
        # menu (a second overlay) has opened and closed, a detached menu pane can
        # still be the last one in the DOM, and every axis after `--model` then
        # reads "0 option groups" (measured 2026-09-05, $0 run).
        pane = page.locator(OVERLAY).filter(has=page.locator(RADIOGROUP)).last
        try:
            await pane.locator(RADIOGROUP).first.wait_for(state="visible", timeout=8000)
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    "migrated host: the settings pane opened but rendered no option "
                    "groups ([role='radiogroup']) (host=migrated)"
                ),
            ) from e
        return pane

    def _blocking_overlays(self, page: Page) -> Any:
        """Visible overlays that are ours to dismiss — the settings pane and the model
        menu, identified by what they contain.

        Scoped rather than every ``.cdk-overlay-pane``: those classes are generic CDK
        and Flow mounts snackbars, tooltips and dialogs in the same container, none of
        which cover the composer or answer to Escape. An unrelated toast visible at the
        wrong moment would otherwise burn both escapes and abort a run that was fine.
        """
        return page.locator(VISIBLE_OVERLAY).filter(has=page.locator(f"{RADIOGROUP}, {MENU_ITEM}"))

    async def _close_pane(self, page: Page, *, strict: bool = True) -> None:
        """Dismiss every visible settings/menu overlay, and verify that none is left.

        ``strict=False`` downgrades a stuck pane to a warning. It is passed on the path
        where an exception is already in flight, so a close failure cannot mask the
        error the caller actually needs — see :meth:`apply_video_settings`.

        One Escape is not enough after ``--model``. Selecting from the menu leaves
        Angular with **two** stacked overlays — the settings pane and the menu opened
        over it — and each Escape dismisses exactly one, so a single press closed only
        the menu and left the settings pane covering the composer. ``send_prompt``'s
        click then failed Playwright's actionability check and surfaced ~5 s later as a
        bare ``TimeoutError`` naming ``[contenteditable='true']``, with nothing pointing
        at the pane. Field-reported as "switch the model and the run dies; re-run with
        the same model and it works" — a re-run binds the model at the button read-back,
        never opens the menu, and so never stacks the second overlay.

        Measured 2026-09-05 at $0 (spike
        ``2026-09-05-migrated-model-menu-lower-priority.md``): after a switch the
        composer's bounding box is **identical** for 12 s — it was never unstable — while
        ``.cdk-overlay-pane:visible`` stays at 1; one more Escape takes it to 0 and the
        prompt types.

        The old read-back *did* notice — ``migrated.pane_still_open`` fires on the
        pre-fix source, confirmed by replaying it against the live host. It was a
        warning: the run continued, the composer click timed out 5 s later, and the
        error it raised named ``[contenteditable='true']`` with no reference to the
        warning that had predicted it. The observation was there and only the
        consequence was missing, so it is now the failure itself.

        A pane that will not close is raised here, pre-submit and at $0, rather than left
        for the composer click to report as an unattributable timeout.
        """
        for _ in range(PANE_CLOSE_ESCAPES):
            # Re-queried every pass rather than held: the count has to be re-read after
            # each Escape, and a locator built once is only re-evaluated because
            # Playwright's are lazy — not a property worth depending on here.
            if not await self._blocking_overlays(page).count():
                return
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        remaining = await self._blocking_overlays(page).count()
        if not remaining:
            return
        log.warning("migrated.pane_still_open", visible_overlays=remaining, strict=strict)
        if not strict:
            return
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: {remaining} overlay(s) still visible after "
                f"{PANE_CLOSE_ESCAPES} Escape presses — the settings pane would cover the "
                f"composer and the prompt could not be typed (host=migrated)"
            ),
        )

    async def _select(
        self,
        page: Page,
        pane: Any,
        *,
        axis: str,
        lig: str | None = None,
        text: str | None = None,
    ) -> None:
        """Click one radio and read ``aria-checked`` back; re-query once on a stale node."""
        radios = pane.locator(RADIO)
        wanted = text if text is not None else str(lig)
        matches = (
            radios.filter(has=_ligature(page, lig))
            if lig
            else radios.filter(has_text=_exact(wanted))
        )
        target = matches.first
        if not await target.count():
            groups = await pane.locator(RADIOGROUP).count()
            # Only the duration row is a per-account/model capability (#650) — a missing
            # mode/aspect/count radio, or an empty pane, is the DOM having changed.
            if axis != "duration" or groups == 0:
                raise UiSelectorDriftError(
                    detail=(
                        f"migrated host: no '{axis}' radio offering {wanted!r} in the settings "
                        f"pane ({groups} option groups rendered) (host=migrated)"
                    ),
                )
            raise ConfigurationError(
                detail=(
                    f"the migrated Flow host renders no duration control offering {wanted!r} "
                    f"for this account and model ({groups} option groups shown)"
                ),
                remediation_hint=(
                    "Drop --duration to accept Flow's default length, or pick a model whose "
                    "settings pane shows a duration row (on the maintainer cohort only "
                    "Omni 1.1 Flash does)."
                ),
            )
        if await target.get_attribute("aria-checked") == "true":
            return
        await target.click(timeout=4000)
        await asyncio.sleep(0.2)
        if await matches.first.get_attribute("aria-checked") == "true":
            return
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: the '{axis}' radio {wanted!r} did not become aria-checked "
                f"after the click (host=migrated)"
            ),
        )

    async def _select_model(self, page: Page, pane: Any, model: VideoModel) -> None:
        matcher = VIDEO_MODEL_MENU_MATCHERS.get(model)
        if matcher is None:
            raise ConfigurationError(
                detail=(
                    f"model '{model.value}' is not available on the migrated Flow host; "
                    f"offered: {', '.join(VIDEO_MODEL_MENU_LABELS.values())}"
                ),
                remediation_hint="Pass --model with one of the offered names, or omit it.",
            )
        button = pane.locator("button").filter(has=_ligature(page, "arrow_drop_down")).first
        if not await button.count():
            raise UiSelectorDriftError(
                detail=(
                    "migrated host: model picker button (arrow_drop_down) not found in the "
                    "settings pane (host=migrated)"
                ),
            )
        current = (await button.text_content() or "").strip()
        if matcher.matches(current):
            # Logged, because otherwise this path is invisible: a run that short-circuits
            # here emits no model event at all, and a field timeline cannot tell "bound
            # the tier you asked for" from "never touched the picker". The 2026-09-05
            # live run needed a separate $0 probe to answer exactly that.
            log.info("migrated.model_already_selected", model=current, requested=model.value)
            return
        await button.click(timeout=4000)
        items = page.locator(MENU_ITEM)
        try:
            await items.first.wait_for(state="visible", timeout=5000)
        except Exception as e:
            raise UiSelectorDriftError(
                detail="migrated host: model menu ([role='menuitem']) did not open (host=migrated)",
            ) from e
        # Matched in Python rather than through a `has_text` filter: the menu is read
        # back for the refusal diagnostic anyway, an *exclusion* is not expressible as
        # `has_text`, and more than one hit has to REFUSE instead of resolving `.first`
        # (#539 — the labs A/B that proved a `.first` on an ambiguous selector picks a
        # tier the user never asked for, and Flow bills for it).
        offered = [t.strip() for t in await items.all_text_contents()]
        hits = [i for i, text in enumerate(offered) if matcher.matches(text)]
        if not hits:
            await page.keyboard.press("Escape")
            raise ConfigurationError(
                detail=(
                    f"model '{model.value}' is not offered on this account's migrated Flow "
                    f"host; offered: {', '.join(offered)}"
                ),
                remediation_hint="Pass --model with one of the offered names, or omit it.",
            )
        if len(hits) > 1:
            await page.keyboard.press("Escape")
            raise ConfigurationError(
                detail=(
                    f"model '{model.value}' matched {len(hits)} entries in the migrated model "
                    f"menu ({', '.join(offered[i] for i in hits)}) — refusing rather than "
                    f"guessing which tier Flow would bill"
                ),
                remediation_hint=(
                    "Pass a --model that names one entry, or omit it to accept Flow's default."
                ),
            )
        await items.nth(hits[0]).click(timeout=4000)
        log.info("migrated.model_selected", model=offered[hits[0]], requested=model.value)

    # --- start frame (i2v) ------------------------------------------------------

    async def attach_start_frame(self, page: Page, project_id: str, image_path: Path) -> str:
        """Upload ``image_path`` through the editor's own Upload entry, bind it on the
        Start chip by file name, and return the media id the app's ``maseQ`` reply
        named for it — the id the submit body is then asserted to carry.

        The upload is permanent in the Flow project (it lands in the library like any
        other asset). The picker is library-only and searched by display name; an
        upload is listed under its file name, and two uploads of one file list twice —
        the picker's default sort puts the newest first, and the submit-body check is
        what catches a wrong pick.
        """
        # The same guard the REST upload runs, from the same function: exists,
        # under the size cap, a real image by its header. Shared rather than
        # copied — it is a security check, and two copies drift.
        from gflow_cli.api.client import validate_image_file  # noqa: PLC0415 - cycle

        await validate_image_file(image_path)
        # No outer budget: every leg below has its own bounded wait, and an outer
        # one that fires first would replace the stage-named failure (which frame,
        # which rpc, what the picker listed) with a generic "attach timed out".
        media_id = await self._upload_via_toolbar(page, project_id, image_path)
        await self._pick_frame_by_name(page, image_path.name, media_id)
        return media_id

    async def _upload_via_toolbar(self, page: Page, project_id: str, image_path: Path) -> str:
        """Toolbar ``+`` → the ``upload`` menu item → the file chooser → the app's own
        ``maseQ`` upload, observed for the media id it returns. Nothing is replayed."""
        loop = asyncio.get_running_loop()
        reply: asyncio.Future[tuple[int, str]] = loop.create_future()
        route = f"batchexecute:{UPLOAD_RPC}"

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            if "batchexecute" not in url or _rpcid(url) != UPLOAD_RPC or reply.done():
                return
            status = int(getattr(response, "status", 0) or 0)
            text = ""
            if status == 200:
                try:
                    text = await response.text()
                except Exception:  # noqa: BLE001 - an aborted body is a rejected upload
                    text = ""
            if not reply.done():
                reply.set_result((status, text))

        page.on("response", on_response)
        try:
            add = page.locator(TOOLBAR_ADD).first
            if not await add.count():
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the toolbar add button (mat-icon 'add' outside "
                        "flow-prompt-box) is missing (host=migrated)"
                    ),
                )
            await add.click(timeout=5000)
            item = page.locator(UPLOAD_MENU_ITEM).first
            try:
                await item.wait_for(state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000))
            except Exception as e:
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the toolbar menu rendered no upload entry "
                        f"({MENU_ITEM} with the 'upload' ligature) within "
                        f"{FRAME_PICKER_OPEN_S:.0f}s (host=migrated)"
                    ),
                ) from e
            try:
                async with page.expect_file_chooser(
                    timeout=int(FRAME_PICKER_OPEN_S * 1000)
                ) as fc_info:
                    await item.click(timeout=4000)
                chooser = await fc_info.value
            except Exception as e:
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the upload entry opened no file chooser within "
                        f"{FRAME_PICKER_OPEN_S:.0f}s (host=migrated)"
                    ),
                ) from e
            await chooser.set_files(str(image_path))
            try:
                status, text = await asyncio.wait_for(reply, timeout=FRAME_UPLOAD_S)
            except TimeoutError:
                raise MediaUploadRejectedError(
                    detail=(
                        f"migrated host: no {UPLOAD_RPC} reply within {FRAME_UPLOAD_S:.0f}s "
                        "of choosing the file — the upload never reached Flow or was dropped"
                    ),
                    route=route,
                ) from None
            if status != 200:
                raise MediaUploadRejectedError(
                    detail=f"migrated host: the upload rpc {UPLOAD_RPC} answered HTTP {status}",
                    status=status,
                    route=route,
                )
            media_id = _first_uuid(text)
            if media_id is None:
                raise MediaUploadRejectedError(
                    detail=f"migrated host: {UPLOAD_RPC} answered 200 without a media id",
                    status=status,
                    route=route,
                )
            if media_id.lower() == project_id.lower():
                # The measured reply is ``[media_id, project_id, …]``. If the first
                # UUID is the project's, the shape moved under us — and the submit-body
                # assertion could not catch it, since the project id is in every body.
                raise MediaUploadRejectedError(
                    detail=(
                        f"migrated host: the first id in the {UPLOAD_RPC} reply is the "
                        f"project id ({project_id}), not a new media id — the reply "
                        "shape changed and the upload cannot be bound safely"
                    ),
                    status=status,
                    route=route,
                )
            log.info("migrated.frame_uploaded", media_id=media_id, status=status)
            return media_id
        finally:
            page.remove_listener("response", on_response)

    @staticmethod
    async def _open_frame_picker(page: Page) -> Any:
        """Click the empty Start chip and wait for the library picker's search box."""
        chip = page.locator(EMPTY_CHIP).first
        if not await chip.count():
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: no empty Start chip ({EMPTY_CHIP}) to bind the frame "
                    "on — is the Frames submode selected? (host=migrated)"
                ),
            )
        await chip.click(timeout=4000)
        picker = page.locator(OVERLAY).filter(has=page.locator(PICKER)).last
        try:
            await picker.locator(PICKER_SEARCH).first.wait_for(
                state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000)
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the frame picker ({PICKER}) did not open within "
                    f"{FRAME_PICKER_OPEN_S:.0f}s of clicking the Start chip (host=migrated)"
                ),
            ) from e
        return picker

    async def _pick_frame_by_name(self, page: Page, name: str, media_id: str) -> None:
        """Start chip → the library picker → search by display name → first option →
        the chip must now hold a thumbnail. An unbound chip is refused here: an empty
        Frames submit goes out as text-to-video (the labs #125 shape on this host)."""
        for attempt in range(1, FRAME_SEARCH_ATTEMPTS + 1):
            picker = await self._open_frame_picker(page)
            search = picker.locator(PICKER_SEARCH).first
            await search.click(timeout=4000)
            await page.keyboard.insert_text(name)
            options = picker.locator(PICKER_OPTION).filter(has_text=_exact(name))
            try:
                await options.first.wait_for(
                    state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000)
                )
            except Exception as e:
                # What the picker DID list is the diagnostic: the display name an upload
                # gets is an observation per account, not a contract.
                listed = [
                    t.strip() for t in await picker.locator(PICKER_OPTION).all_text_contents()
                ]
                await page.keyboard.press("Escape")
                if attempt < FRAME_SEARCH_ATTEMPTS:
                    log.info("migrated.frame_search_miss", attempt=attempt, listed=len(listed))
                    await asyncio.sleep(FRAME_SEARCH_RETRY_PAUSE_S)
                    continue
                raise ReferenceNotFoundError(
                    detail=(
                        f"migrated host: the frame picker lists no asset named {name!r} after "
                        f"{FRAME_SEARCH_ATTEMPTS} searches of {FRAME_PICKER_OPEN_S:.0f}s (media "
                        f"{media_id}) — uploads are expected under their file name; the "
                        "picker listed for that search: "
                        f"{', '.join(repr(t) for t in listed[:8]) or 'nothing'}"
                    ),
                ) from e
            else:
                # Outside the except above on purpose: a click that fails here is not
                # "the picker never listed it", and must not be reported as one.
                await options.first.click(timeout=4000)
                break
        try:
            # Re-queried, and `.last` like the open: the picker overlay is detached
            # after the pick, and a detached-but-hidden earlier pane would let a
            # `.first` hidden-wait pass while the live picker is still up.
            await (
                page.locator(OVERLAY)
                .filter(has=page.locator(PICKER))
                .last.wait_for(state="hidden", timeout=int(FRAME_COMMIT_HIDDEN_S * 1000))
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the frame picker stayed open {FRAME_COMMIT_HIDDEN_S:.0f}s "
                    f"after picking {name!r} (host=migrated)"
                ),
            ) from e
        try:
            await page.locator(BOUND_CHIP).first.wait_for(
                state="visible", timeout=int(FRAME_THUMB_VISIBLE_S * 1000)
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the Start chip did not bind {name!r} — no "
                    f"{BOUND_CHIP} within {FRAME_THUMB_VISIBLE_S:.0f}s of the pick; "
                    "refusing to submit what would go out as text-to-video (host=migrated)"
                ),
            ) from e
        log.info("migrated.frame_bound", media_id=media_id)

    # --- prompt + submit --------------------------------------------------------

    async def send_prompt(self, page: Page, prompt: str) -> None:
        composer = page.locator(COMPOSER).first
        if not await composer.count():
            raise UiSelectorDriftError(
                detail=f"migrated host: composer ({COMPOSER}) not found (host=migrated)",
            )
        await composer.click(timeout=5000)
        # insert_text dispatches input events without key presses: a newline in the
        # prompt lands as text instead of an Enter that might submit early.
        await page.keyboard.insert_text(prompt)
        log.info("migrated.prompt_typed", chars=len(prompt))

    async def submit_and_observe(
        self,
        page: Page,
        *,
        poll_timeout_s: float,
        on_started: VideoStartedCallback | None,
        project_id: str | None,
        expect_media_id: str | None = None,
    ) -> GenerationRecord:
        """Click submit, then read the page's own ``YhhmEf``/``eb1hJf`` / ``jwpduf`` /
        ``as29s`` replies until the record is terminal. Fires ``on_started`` as soon
        as the submit reply names the media id — before the poll, as the labs path does.

        ``expect_media_id`` (i2v) inspects the submit *request* the app sends: its body
        must carry that id and an ``_i2v_`` model key, else the run is a
        :class:`WireFormatError` — the generation the user asked for is not the one
        Flow is billing."""
        loop = asyncio.get_running_loop()
        submitted: asyncio.Future[GenerationRecord] = loop.create_future()
        route_error: asyncio.Future[WireFormatError] = loop.create_future()

        def on_request(request: Any) -> None:
            url = str(getattr(request, "url", ""))
            rpcid = _rpcid(url) if "batchexecute" in url else None
            if rpcid not in SUBMIT_RPCS or route_error.done() or expect_media_id is None:
                return
            problem = _i2v_body_problem(_post_data(request), rpcid, expect_media_id)
            if problem is not None:
                route_error.set_result(
                    WireFormatError(detail=problem, route=f"batchexecute:{rpcid}")
                )

        # ``terminal``: failed, or done WITH the signed URL. ``done_no_url``: the
        # first status-3 record that has no URL yet (a poll beats the result RPC).
        terminal: asyncio.Future[GenerationRecord] = loop.create_future()
        done_no_url: asyncio.Future[GenerationRecord] = loop.create_future()
        workflow: dict[str, str] = {}

        def _settle(rec: GenerationRecord) -> None:
            if rec.is_failed or (rec.is_done and rec.video_url):
                if not terminal.done():
                    terminal.set_result(rec)
            elif rec.is_done and not done_no_url.done():
                done_no_url.set_result(rec)

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            rpcid = _rpcid(url) if "batchexecute" in url else None
            if rpcid not in SUBMIT_RPCS and rpcid not in STATUS_RPCS:
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - an aborted/streamed body is not our frame
                return
            for rid, payload in parse_frames(text):
                if rid in SUBMIT_RPCS and not submitted.done():
                    try:
                        rec = generation_record(rid, payload)
                    except WireFormatError as exc:
                        submitted.set_exception(exc)
                        return
                    workflow["id"] = rec.workflow_id
                    log.info(
                        "migrated.submit_observed",
                        rpc=rid,
                        workflow_id=rec.workflow_id,
                        media_id=rec.media_id,
                        status=rec.status,
                    )
                    submitted.set_result(rec)
                    _settle(rec)
                elif rid in STATUS_RPCS and workflow:
                    try:
                        rec = generation_record(rid, payload)
                    except WireFormatError:
                        continue
                    if rec.workflow_id != workflow["id"]:
                        continue
                    log.info("migrated.status", rpc=rid, status=rec.status, bytes=rec.size_bytes)
                    _settle(rec)

        page.on("response", on_response)
        if expect_media_id is not None:
            page.on("request", on_request)
        try:
            submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
            if not await submit.count():
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the submit button (arrow_forward) is missing "
                        "after the prompt was typed (host=migrated)"
                    ),
                )
            enable_deadline = time.monotonic() + SUBMIT_ENABLE_BUDGET_S
            while not await submit.is_enabled():
                if time.monotonic() >= enable_deadline:
                    raise UiSelectorDriftError(
                        detail=(
                            "migrated host: the submit button (arrow_forward) stayed disabled "
                            f"for {SUBMIT_ENABLE_BUDGET_S:.0f}s after the prompt was typed "
                            "(host=migrated)"
                        ),
                    )
                await asyncio.sleep(SUBMIT_ENABLE_POLL_S)
            deadline = time.monotonic() + poll_timeout_s
            await submit.click(timeout=5000)
            log.info("migrated.submit_clicked")
            budget = min(SUBMIT_REPLY_BUDGET_S, poll_timeout_s)
            await asyncio.wait(
                {submitted, route_error}, timeout=budget, return_when=asyncio.FIRST_COMPLETED
            )
            if route_error.done():
                # The request is inspected before its reply lands, so a wrong body is
                # named as such and not as whatever the reply then says.
                raise route_error.result()
            if not submitted.done():
                raise TransportTimeoutError(
                    detail=(
                        f"migrated host: no {'/'.join(SUBMIT_RPCS)} reply within "
                        f"{budget:.0f}s of clicking submit"
                    ),
                )
            first = submitted.result()
            started = VideoStarted(
                media_id=first.media_id,
                project_id=project_id or first.project_id,
                flow_operation_id=first.workflow_id,
            )
            if on_started is not None:
                maybe = on_started(started)
                if asyncio.iscoroutine(maybe):
                    await maybe
            final = await self._await_terminal(
                terminal, done_no_url, deadline=deadline, workflow_id=first.workflow_id
            )
            log.info(
                "migrated.result",
                status=final.status,
                done=final.is_done,
                url_host=urlsplit(final.video_url).hostname if final.video_url else None,
                bytes=final.size_bytes,
            )
            return final
        finally:
            # A submit reply that failed to parse sets an exception on `submitted`;
            # when the body assertion raised first, nobody read it, and asyncio would
            # log "Future exception was never retrieved" at GC. Consume it.
            if submitted.done() and not submitted.cancelled():
                submitted.exception()
            page.remove_listener("response", on_response)
            if expect_media_id is not None:
                page.remove_listener("request", on_request)

    @staticmethod
    async def _await_terminal(
        terminal: asyncio.Future[GenerationRecord],
        done_no_url: asyncio.Future[GenerationRecord],
        *,
        deadline: float,
        workflow_id: str,
    ) -> GenerationRecord:
        """Wait for a terminal record; a done-without-URL record buys a short grace
        for the one that carries the URL, then stands on its own."""
        remaining = max(deadline - time.monotonic(), 0.01)
        done, _ = await asyncio.wait(
            {terminal, done_no_url}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        if terminal.done():
            return terminal.result()
        if not done:
            raise TransportTimeoutError(
                detail=(
                    f"migrated host: generation {workflow_id} was not terminal within the "
                    f"poll timeout"
                ),
            )
        grace = min(RESULT_URL_GRACE_S, max(deadline - time.monotonic(), 0.01))
        try:
            return await asyncio.wait_for(terminal, timeout=grace)
        except TimeoutError:
            log.warning("migrated.result_url_not_observed", grace_s=grace)
            return done_no_url.result()

    # --- download ---------------------------------------------------------------

    @staticmethod
    async def _fetch_mp4(page: Page, record: GenerationRecord) -> bytes:
        """GET the signed URL and prove it is an MP4 (``ftyp`` at offset 4); if it is
        not — the record carries a poster JPEG next to the clip, and a 2026-09-05
        run downloaded that one — try the other URL before giving up."""
        from gflow_cli.api.transports.ui_automation import (  # noqa: PLC0415 - cycle
            _is_allowed_download_host,  # pyright: ignore[reportPrivateUsage]
        )

        seen: list[str] = []
        for url in (record.video_url, record.poster_url):
            if not url:
                continue
            if not _is_allowed_download_host(url):
                raise WireFormatError(
                    detail=(
                        "migrated host: refusing to download from "
                        f"{urlsplit(url).hostname!r} (not an allowed Google host)"
                    ),
                    route="batchexecute:as29s",
                )
            # No redirects: an open redirect on the CDN must not rebound the
            # request elsewhere (same posture as the labs image download).
            resp = await page.request.get(url, timeout=180_000, max_redirects=0)
            if resp.status >= 300:
                raise WireFormatError(
                    detail=f"migrated host: signed media URL returned HTTP {resp.status}",
                    status=resp.status,
                    route="flow-content.google",
                )
            body = await resp.body()
            if body[4:8] == b"ftyp":
                if record.size_bytes and len(body) != record.size_bytes:
                    log.warning(
                        "migrated.download_size_mismatch",
                        expected=record.size_bytes,
                        actual=len(body),
                    )
                return body
            seen.append(
                f"{urlsplit(url).path.rsplit('/', 1)[-1]}: {body[:4].hex()} ({len(body)} B)"
            )
        raise WireFormatError(
            detail=(
                "migrated host: no signed URL on the record returned an MP4 "
                f"(ftyp magic); saw {'; '.join(seen) or 'no URLs'}"
            ),
            route="batchexecute:as29s",
        )

    async def download(
        self,
        page: Page,
        record: GenerationRecord,
        out_dir: Path | None,
    ) -> Path | None:
        """The clip from its signed CDN URL. The labs ``media.getMediaUrlRedirect``
        route answers 404 for a migrated media id (measured 2026-09-05), so there is
        no second source: a record with no URL is a wire-format failure."""
        if not record.video_url and not record.poster_url:
            raise WireFormatError(
                detail=(
                    "migrated host: the generation finished but no signed media URL was "
                    f"observed within the {RESULT_URL_GRACE_S:.0f}s grace"
                ),
                route="batchexecute:as29s",
            )
        body = await self._fetch_mp4(page, record)
        target_dir = out_dir or Path.cwd()
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{record.media_id}.mp4"
        path.write_bytes(body)
        log.info("migrated.download", path=str(path), bytes=len(body))
        return path


async def run_video(
    page: Page,
    request: GenerateVideoRequest,
    *,
    project_id: str | None,
    out_dir: Path | None,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
) -> VideoResult:
    """The migrated-host twin of the labs ``_generate_video_locked`` tail: same
    inputs, same ``VideoResult``, so recorder, CLI, MCP and worker are untouched.

    t2v, and i2v from a local start frame (uploaded through the editor and bound on
    the Start chip by file name). An end frame, a frame by UUID / ``@Name`` and r2v
    are not ported yet; a fresh project can only be created through the labs
    gallery, so the caller must name one (``--project``).
    """
    unported = _unported_form(request)
    if unported is not None:
        raise FlowHostMigratedError(
            detail=(
                f"this account's Flow lives on flow.google.com, where gflow drives "
                f"text-to-video and image-to-video from a local start frame; {unported} "
                f"is not ported yet (#639) — pass --initial-frame <local file> without "
                f"an end frame"
            ),
        )
    pid = project_id or extract_project_id(page.url)
    if not pid:
        raise ConfigurationError(
            detail=(
                "generating on the migrated flow.google.com host needs an existing project: "
                "pass --project <id> (see `gflow project list` / `gflow project create`) — "
                "creating one from the editor is not ported to this host yet"
            ),
        )
    log.info("migrated.dispatch", project_id=pid, mode=request.mode.value)
    composer = MigratedComposer()
    await composer.ensure_editor(page, pid)
    await composer.apply_video_settings(page, request)
    media_id: str | None = None
    frame = request.start_image
    if request.mode is Mode.I2V and frame is not None:
        media_id = await composer.attach_start_frame(page, pid, frame)
    await composer.send_prompt(page, request.prompt)
    record = await composer.submit_and_observe(
        page,
        poll_timeout_s=poll_timeout_s,
        on_started=on_started,
        project_id=pid,
        expect_media_id=media_id,
    )
    status = VideoStatus(
        media_id=record.media_id,
        status=(
            "MEDIA_GENERATION_STATUS_SUCCESSFUL"
            if record.is_done
            else "MEDIA_GENERATION_STATUS_FAILED"
        ),
        error_message=None if record.is_done else f"migrated host reported status {record.status}",
    )
    local_path: Path | None = None
    if download and record.is_done:
        local_path = await composer.download(page, record, out_dir)
    return VideoResult(
        status=status,
        local_path=Path(local_path) if local_path is not None else None,
        project_id=pid,
        flow_operation_id=record.workflow_id,
    )
