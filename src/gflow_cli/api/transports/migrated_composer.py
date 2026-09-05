"""Drive Flow's migrated ``flow.google.com`` editor (Angular Material) — t2v first.

Google is moving accounts from ``labs.google/fx/tools/flow`` onto
``flow.google.com`` (issue #639). The migrated app is the same product on a
different widget toolkit: ligatures live in ``<mat-icon>`` instead of ``<i>``,
the settings popover is a ``cdk-overlay`` pane of ``[role=radiogroup]`` /
``[role=radio]`` buttons instead of ``role=menu`` tabs, the model picker is a
``[role=menu]`` of ``[role=menuitem]``s, and the composer is a ``contenteditable``
(the ``textarea`` next to it is not clickable). On the wire it is ``batchexecute``,
not aisandbox REST: submit is rpcid ``YhhmEf``, the app then polls ``jwpduf`` every
5 s by itself and fetches the result with ``as29s`` — so this driver **observes**
the page's own traffic and adds none. Recon with measurements:
``docs/superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md``.

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
from gflow_cli.api.video import Aspect, Mode, VideoModel, VideoResult, VideoStarted, VideoStatus
from gflow_cli.errors import (
    ConfigurationError,
    FlowHostMigratedError,
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

SUBMIT_RPC = "YhhmEf"
STATUS_RPCS = ("jwpduf", "as29s")
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


def migrated_can_serve(request: GenerateVideoRequest, project_id: str | None) -> bool:
    """Can the migrated composer take this request as it stands? Text-to-video in an
    existing project, with a model the new host offers (or none). Everything else —
    i2v/r2v media, character references, a fresh project, a labs-only model — is
    not ported yet, so an unmoved account keeps the labs driver for it.

    Gated on :data:`VIDEO_MODEL_MENU_LABELS`, not on the wider
    :data:`VIDEO_MODEL_MENU_MATCHERS`: this decides whether to *move* a request off
    labs.google, and pulling one there for a tier no capture has ever seen rendered
    would trade a working driver for an unverified one. An account Flow has already
    moved is routed by its URL and never reaches this question — for it,
    ``--model veo-lite-lp`` is driven by its matcher instead of refused outright."""
    if request.mode is not Mode.T2V or not project_id:
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

    # --- settings ---------------------------------------------------------------

    async def apply_video_settings(self, page: Page, request: GenerateVideoRequest) -> None:
        """Mode, model, aspect, duration, count — through the radios, with read-back.

        Model goes first: like on labs.google the duration row is model-state.
        """
        pane = await self._open_pane(page)
        try:
            await self._select(page, pane, axis="mode", lig="videocam")
            if request.model is not None:
                await self._select_model(page, pane, request.model)
            await self._select(page, pane, axis="aspect", lig=ASPECT_LIGATURE[request.aspect])
            if request.duration is not None:
                await self._select(page, pane, axis="duration", text=f"{request.duration}s")
            await self._select(page, pane, axis="count", text=f"x{request.count}")
            log.info(
                "migrated.settings_applied",
                aspect=request.aspect.value,
                duration=request.duration,
                count=request.count,
                model=request.model.value if request.model else None,
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
    ) -> GenerationRecord:
        """Click submit, then read the page's own ``YhhmEf`` / ``jwpduf`` / ``as29s``
        replies until the record is terminal. Fires ``on_started`` as soon as the
        submit reply names the media id — before the poll, as the labs path does."""
        loop = asyncio.get_running_loop()
        submitted: asyncio.Future[GenerationRecord] = loop.create_future()
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
            if rpcid != SUBMIT_RPC and rpcid not in STATUS_RPCS:
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - an aborted/streamed body is not our frame
                return
            for rid, payload in parse_frames(text):
                if rid == SUBMIT_RPC and not submitted.done():
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
            try:
                first = await asyncio.wait_for(
                    submitted, timeout=min(SUBMIT_REPLY_BUDGET_S, poll_timeout_s)
                )
            except TimeoutError:
                raise TransportTimeoutError(
                    detail=(
                        f"migrated host: no {SUBMIT_RPC} reply within "
                        f"{min(SUBMIT_REPLY_BUDGET_S, poll_timeout_s):.0f}s of clicking submit"
                    ),
                ) from None
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
            page.remove_listener("response", on_response)

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

    t2v only for now — i2v/r2v attach media through labs-shaped slots that have
    not been recon'd on this host, and a fresh project can only be created through
    the labs gallery, so the caller must name one (``--project``).
    """
    if request.mode is not Mode.T2V:
        raise FlowHostMigratedError(
            detail=(
                f"this account's Flow lives on flow.google.com, where gflow drives "
                f"text-to-video only for now; {request.mode.value} is not ported yet (#639)"
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
    await composer.send_prompt(page, request.prompt)
    record = await composer.submit_and_observe(
        page, poll_timeout_s=poll_timeout_s, on_started=on_started, project_id=pid
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
