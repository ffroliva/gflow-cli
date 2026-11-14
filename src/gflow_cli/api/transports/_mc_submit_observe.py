"""Prompt sending, generation submission, response polling and observation.

A part of `MigratedComposer`, moved byte-identical from
`migrated_composer.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

import structlog
from playwright.async_api import Error as PlaywrightError

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.transports._mc_common import (
    ABORTED_BEFORE_SEND,
    BATCHEXECUTE_GLOB,
    COMPOSER,
    IMAGE_REPLY_BUDGET_S,
    IMAGE_SUBMIT_RPC,
    LISTING_READ_BUDGET_S,
    LISTING_RPC,
    MIGRATED_PROJECT_URL,
    MODEL_KEY,
    RESULT_URL_GRACE_S,
    STATUS_RPCS,
    SUBMIT_BUTTON,
    SUBMIT_ENABLE_BUDGET_S,
    SUBMIT_ENABLE_POLL_S,
    SUBMIT_REPLY_BUDGET_S,
    SUBMIT_RPCS,
    VISIBLE_OVERLAY,
    _ClipByPrompt,
    _grid_images,
    _guard_image_submit,
    _image_body_problem,
    _image_body_references,
    _ligature,
    _outcome_unknown,
    _raise_if_out_of_credits,
    _recover_images,
    _submit_body_problem,
    _submit_refusal,
    body_rpcid,
    request_body,
    url_rpcid,
)
from gflow_cli.api.transports._mc_mention import MentionMixin
from gflow_cli.api.transports.batchexecute import (
    GenerationRecord,
    generation_record,
    generation_records_carrying,
    image_records,
    parse_frames,
)
from gflow_cli.api.video import VideoStarted
from gflow_cli.errors import (
    GFlowError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.async_api import Locator, Page

    from gflow_cli.api.image import GenerateImageRequest
    from gflow_cli.api.video import VideoStartedCallback

log = structlog.get_logger(__name__)


class SubmitObserveMixin(MentionMixin):
    """Prompt sending, generation submission, response polling and observation."""

    # --- prompt + submit --------------------------------------------------------

    async def send_prompt(self, page: Page, prompt: str, *, append: bool = False) -> None:
        """Type the prompt. ``append=True`` keeps what is already in the composer — the
        mention chips an r2v run just attached — and leaves the caret where the last one
        put it, rather than clicking and moving it."""
        composer = page.locator(COMPOSER).first
        if not await composer.count():
            raise UiSelectorDriftError(
                detail=f"migrated host: composer ({COMPOSER}) not found (host=migrated)",
            )
        if not append:
            # _close_pane's docstring names THIS click as the one that surfaced a
            # stuck settings pane as a bare 5 s TimeoutError naming only the composer.
            await self._click(page, composer, named=COMPOSER, timeout=5000)
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
        expect_end_media_id: str | None = None,
        expect_reference_ids: tuple[str, ...] = (),
        submit_rpcs: tuple[str, ...] = SUBMIT_RPCS,
        submit_selector: str | None = None,
        request_check: Callable[[str, str], GFlowError | None] | None = None,
        adopt_ids: tuple[str, ...] = (),
        adopt_prompt: str | None = None,
    ) -> GenerationRecord:
        """Click submit, then read the page's own submit / ``jwpduf`` / ``as29s``
        replies until the record is terminal. Fires ``on_started`` as soon as the
        submit reply names the media id — before the poll, as the labs path does.

        ``submit_rpcs``, ``submit_selector`` and ``request_check`` let another surface
        reuse this observer (extend: ``fZytfe``, the editor's own submit button, and a
        verdict on every outgoing ``batchexecute`` request that fails the run at once).

        ``expect_media_id`` (i2v) and ``expect_reference_ids`` (r2v) inspect the
        submit *request* the app sends: its body must carry those ids and the
        matching model key, else the run is a :class:`WireFormatError` — the
        generation the user asked for is not the one Flow is billing. A bound end
        frame (``expect_end_media_id``) switches the submit to the interpolation
        rpc (``nprQif``), whose name travels in the ``f.req`` body rather than the
        URL; that body must carry the interpolation key and both frame ids.

        ``adopt_ids`` are media ids this run uploaded. When Chrome loses the submit reply
        body (#1112), the status poll whose record carries one of them is adopted as the
        submit; each upload is a run-unique copy (18 of 18 distinct reference ids in the
        2026-10-27 spike), so an older clip does not carry it.

        ``adopt_prompt`` does the same job when nothing was uploaded (t2v, one clip, #1141):
        see :class:`_ClipByPrompt`. A caller that passes neither keeps the immediate exit 9."""
        loop = asyncio.get_running_loop()
        submitted: asyncio.Future[GenerationRecord] = loop.create_future()
        route_error: asyncio.Future[GFlowError] = loop.create_future()
        # Body problems the observer saw; raised only if the route guard did not stop them.
        observed: list[GFlowError] = []
        # (request, rpcid) of each submit sent, to place a reply that names no rpcid.
        submit_requests: list[tuple[Any, str]] = []
        submit_key: dict[str, str] = {}
        clicked_at = 0.0
        by_prompt: _ClipByPrompt | None = None
        # Set when a candidate finishes: the listing does not name a clip still generating
        # (measured 2026-11-05, answered in 3 s without it, twice).
        candidate_seen: asyncio.Future[None] = loop.create_future()

        def on_request(request: Any) -> None:
            url = str(getattr(request, "url", ""))
            if "batchexecute" not in url or route_error.done():
                return
            # The URL `rpcids` param is the normal carrier and is tried first. The
            # body fallback exists because the interpolation submit has been reported
            # without that param; on the account measured 2026-09-17 it DID carry it
            # (`nprQif` in the URL, key `omni_flash_i2v_8s_first_last`), so treat the
            # body as a second carrier rather than the rule — cohorts differ here.
            body = request_body(request)
            rpcid = url_rpcid(url) or body_rpcid(body)
            # Record request rpcids too: at timeout the set shows both what went
            # out and what answered (issue #639).
            if rpcid is not None:
                seen_submit_rpcs.add(rpcid)
            if rpcid is None:
                return
            if request_check is not None:
                verdict = request_check(rpcid, body)
                if verdict is not None:
                    route_error.set_result(verdict)
                    return
            if rpcid in submit_rpcs:
                submit_requests.append((request, rpcid))
                # The prompt comes first in the body, and a prompt can read like a key.
                if key := MODEL_KEY.search(body.replace(adopt_prompt or "", "")):
                    submit_key["key"] = key.group(0)
                try:
                    problem = _submit_body_problem(
                        rpcid,
                        body,
                        expect_media_id=expect_media_id,
                        expect_end_media_id=expect_end_media_id,
                        expect_reference_ids=expect_reference_ids,
                    )
                except Exception:  # noqa: BLE001 - a listener must not raise; the route
                    return  # guard runs the same check and aborts the submit on failure
                # Recorded, not raised: this event fires before the route guard, which
                # aborts the same request unsent and names it so. A problem only the
                # observer saw means the route missed it and Flow got the request.
                if problem is not None:
                    observed.append(WireFormatError(detail=problem, route=f"batchexecute:{rpcid}"))
                return
            # An unwatched rpcid is RECORDED (`seen_submit_rpcs`, above) but never
            # adopted as our submit. Adopting by body content was tried and removed:
            # the predicate available here (`_i2v_body_problem`) requires `_i2v_` in
            # the key, which `veo_3_1_interpolation_lite` does not contain — so it
            # could never fire for the veo start+end case it was written for — while
            # for the omni `*_i2v_*_first_last` key it matched broadly enough that any
            # future rpc echoing composer state would be adopted, and a parse failure
            # on an adopted id calls `submitted.set_exception` (fatal) rather than
            # `continue`. That trades a named timeout for a hard failure on an
            # already-billed run. Both submit rpcids we have measured — `eb1hJf`
            # (start only) and `nprQif` (start+end, 2026-09-17) — are in SUBMIT_RPCS,
            # so nothing needs adopting; a third one should be measured and added by
            # name, not guessed at from a body.

        # ``terminal``: failed, or done WITH the signed URL. ``done_no_url``: the
        # first status-3 record that has no URL yet (a poll beats the result RPC).
        terminal: asyncio.Future[GenerationRecord] = loop.create_future()
        done_no_url: asyncio.Future[GenerationRecord] = loop.create_future()
        workflow: dict[str, str] = {}
        # The submit rpc whose reply body was lost, while a status poll may still name it.
        lost: dict[str, str] = {}

        def _settle(rec: GenerationRecord) -> None:
            if rec.is_failed or (rec.is_done and rec.video_url):
                if not terminal.done():
                    terminal.set_result(rec)
            elif rec.is_done and not done_no_url.done():
                done_no_url.set_result(rec)

        def _adopt(rid: str, payload: Any) -> None:
            carrying = generation_records_carrying(rid, payload, adopt_ids)
            matches = {r.workflow_id: r for r in carrying}
            if len(matches) > 1:
                ids = ", ".join(sorted(r.media_id for r in matches.values()))
                log.warning("migrated.video_recovery_ambiguous", rpc=rid, media_ids=ids)
                submitted.set_exception(
                    _outcome_unknown(
                        f"migrated host: the {lost['rpc']} submit reply body was lost and "
                        f"{len(matches)} clips carry this run's upload ({ids})"
                    )
                )
            elif matches:
                (rec,) = matches.values()
                _accept(rid, rec)

        def _accept(rid: str, rec: GenerationRecord) -> None:
            workflow["id"] = rec.workflow_id
            log.info(
                "migrated.video_adopted_from_listing"
                if rid == LISTING_RPC
                else "migrated.video_adopted_from_status",
                rpc=rid,
                workflow_id=rec.workflow_id,
                media_id=rec.media_id,
                status=rec.status,
            )
            submitted.set_result(rec)
            _settle(rec)

        def _note_poll(rid: str, payload: Any) -> None:
            assert by_prompt is not None  # noqa: S101 - set in the same branch as `lost`
            refusal = by_prompt.note_poll(rid, payload)
            if refusal is not None:
                submitted.set_exception(refusal)
            elif not candidate_seen.done() and any(
                not rec.is_running for rec in by_prompt.candidates.values()
            ):
                candidate_seen.set_result(None)

        def _decide_by_prompt(listing: Any | None) -> None:
            assert by_prompt is not None  # noqa: S101 - set in the same branch as `lost`
            try:
                _accept(LISTING_RPC, by_prompt.decide(listing))
            except TransportTimeoutError as refusal:
                submitted.set_exception(refusal)

        # Every batchexecute rpcid seen while waiting for the submit reply. A
        # start+end submit may answer on an rpcid this observer does not watch;
        # recording the set turns an unknown-reply timeout from a mystery into a
        # named fact (issue #639).
        seen_submit_rpcs: set[str] = set()

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            rpcid = url_rpcid(url) if "batchexecute" in url else None
            if rpcid is not None:
                seen_submit_rpcs.add(rpcid)
            if "batchexecute" not in url:
                return
            # `rpcid is None` falls through on purpose: the interpolation reply is a
            # bare `batchexecute` with no `rpcids` param, and `parse_frames` recovers
            # its id from the frame itself (measured 2026-09-17).
            if rpcid is not None and rpcid not in submit_rpcs and rpcid not in STATUS_RPCS:
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - an aborted/streamed body is not our frame
                # #1009/#1010: Chrome can drop a reply body. On the submit reply that
                # loses the workflow id, so no status reply can settle the run either.
                # nprQif answers on a bare URL: tie it to the submit by its request.
                sent = getattr(response, "request", None)
                sub = (
                    rpcid
                    if rpcid in submit_rpcs
                    else next((r for q, r in submit_requests if q is sent), None)
                )
                if sub is None or submitted.done():
                    return
                if adopt_prompt is not None and project_id and "key" in submit_key:
                    nonlocal by_prompt
                    by_prompt = _ClipByPrompt(
                        project_id, adopt_prompt, submit_key["key"], clicked_at
                    )
                if adopt_ids or by_prompt is not None:
                    lost["rpc"] = sub
                    log.warning(
                        "migrated.video_submit_reply_lost",
                        rpc=sub,
                        note="submit reply lost; following the clip in the project, no re-submit",
                    )
                else:
                    submitted.set_exception(
                        _outcome_unknown(
                            f"migrated host: the {sub} submit reply arrived but its "
                            "body was unreadable"
                        )
                    )
                return
            refusal = _submit_refusal(text, submit_rpcs)
            # An envelope arriving after an accepted submit is ignored: that run's outcome
            # is already decided, and it now belongs to the terminal wait.
            if refusal is not None and not submitted.done():
                submitted.set_exception(refusal)
                return
            for rid, payload in parse_frames(text):
                # Record the id the FRAME names, not just the URL's. The interpolation
                # reply arrives on a bare `batchexecute` with no `rpcids` param, so a
                # URL-only diagnostic reports "rpcs seen: none" for the very reply shape
                # whose absence it is trying to explain (#639).
                seen_submit_rpcs.add(rid)
                if rid in submit_rpcs and not submitted.done():
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
                elif rid in STATUS_RPCS and lost and not workflow and not submitted.done():
                    if by_prompt is not None:
                        _note_poll(rid, payload)
                    else:
                        _adopt(rid, payload)
                elif rid in STATUS_RPCS and workflow:
                    try:
                        rec = generation_record(rid, payload, workflow_id=workflow["id"])
                    except WireFormatError:
                        continue
                    log.info("migrated.status", rpc=rid, status=rec.status, bytes=rec.size_bytes)
                    _settle(rec)

        page.on("response", on_response)

        async def guard(route: Any, request: Any) -> None:
            """Abort a wrong submit BEFORE it leaves the browser (#1178): the observer
            above only sees a request already on its way to Flow, which bills it."""
            rpcid: str | None = None
            try:
                body = request_body(request)
                rpcid = url_rpcid(str(getattr(request, "url", ""))) or body_rpcid(body)
                problem = (
                    _submit_body_problem(
                        rpcid,
                        body,
                        expect_media_id=expect_media_id,
                        expect_end_media_id=expect_end_media_id,
                        expect_reference_ids=expect_reference_ids,
                    )
                    if rpcid is not None and rpcid in submit_rpcs
                    else None
                )
            except Exception as exc:  # noqa: BLE001 - an unsettled route hangs the page
                problem = (
                    f"migrated host: the {rpcid} submit could not be checked ({type(exc).__name__})"
                    if rpcid is not None and rpcid in submit_rpcs
                    else None
                )
            if problem is None:
                # fallback, not continue_: an outer guard (extend) must still see it.
                await route.fallback()
                return
            await route.abort()
            log.warning("migrated.submit_refused_before_send", rpc=rpcid)
            if not route_error.done():
                route_error.set_result(
                    WireFormatError(
                        detail=problem,
                        route=f"batchexecute:{rpcid}",
                        remediation_hint=ABORTED_BEFORE_SEND,
                    )
                )

        # Both are ALWAYS armed, t2v included: a stale End chip makes the app submit
        # `nprQif` on a run that asked for no end frame. The route guard is where refusing
        # is free (the request never leaves); the observer is the backstop for a request
        # the route never saw, whose verdict lands once the run settles or times out.
        # Gating on `expect_media_id` once left `_r2v_body_problem` unreached live.
        page.on("request", on_request)
        try:
            await page.route(BATCHEXECUTE_GLOB, guard)
            submit = (
                page.locator(submit_selector).first
                if submit_selector
                else page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
            )
            submit_named = submit_selector or "arrow_forward"
            if not await submit.count():
                # A credit shortfall and a moved frontend look identical here: both are
                # "arrow_forward is gone". Ask which one BEFORE naming a culprit --
                # reporting a short balance as selector drift tells the user to file a
                # frontend bug that no code change can fix.
                await _raise_if_out_of_credits(page)
                raise UiSelectorDriftError(
                    detail=(
                        f"migrated host: the submit button ({submit_named}) is missing "
                        "after the prompt was typed (host=migrated)"
                    ),
                )
            enable_deadline = time.monotonic() + SUBMIT_ENABLE_BUDGET_S
            while not await submit.is_enabled():
                if time.monotonic() >= enable_deadline:
                    # Same question as above, on the other way of giving up on submit.
                    await _raise_if_out_of_credits(page)
                    raise UiSelectorDriftError(
                        detail=(
                            f"migrated host: the submit button ({submit_named}) stayed disabled "
                            f"for {SUBMIT_ENABLE_BUDGET_S:.0f}s after the prompt was typed "
                            "(host=migrated)"
                        ),
                    )
                await asyncio.sleep(SUBMIT_ENABLE_POLL_S)
            deadline = time.monotonic() + poll_timeout_s
            clicked_at = time.time()
            # The credit-spending click: a bare timeout here leaves "did it submit?"
            # unanswerable, which is the worst place in this driver to lose attribution.
            await self._click(page, submit, named=SUBMIT_BUTTON, timeout=5000)
            log.info("migrated.submit_clicked")
            budget = min(SUBMIT_REPLY_BUDGET_S, poll_timeout_s)
            await asyncio.wait(
                {submitted, route_error, candidate_seen},
                timeout=budget,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if by_prompt is not None and by_prompt.candidates and not submitted.done():
                await asyncio.wait(
                    {submitted, route_error, candidate_seen},
                    timeout=max(0.0, deadline - time.monotonic()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            if lost and not submitted.done() and not route_error.done() and project_id:
                # No poll named it (their bodies can be lost too): the project listing,
                # read in a tab opened only now, after the upload's file chooser is done.
                listing = await self._read_listing(
                    page,
                    project_id,
                    budget_s=max(0.0, min(LISTING_READ_BUDGET_S, deadline - time.monotonic())),
                )
                if by_prompt is not None and not submitted.done():
                    _decide_by_prompt(listing)
                elif listing is not None and not submitted.done():
                    _adopt(LISTING_RPC, listing)
            if lost and not submitted.done() and not route_error.done():
                # The clip is being made; a status poll may still name it before the deadline.
                await asyncio.wait(
                    {submitted, route_error},
                    timeout=max(0.0, deadline - time.monotonic()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            if route_error.done():
                # The request is inspected before its reply lands, so a wrong body is
                # named as such and not as whatever the reply then says.
                raise route_error.result()
            if observed:
                raise observed[0]  # the route did not stop it: sent, and Flow bills it
            if not submitted.done() and lost:
                where = MIGRATED_PROJECT_URL.format(project_id=project_id) if project_id else "Flow"
                raise _outcome_unknown(
                    f"migrated host: the {lost['rpc']} submit reply arrived but its body was "
                    f"unreadable, and no status reply named a clip carrying this run's upload "
                    f"within {poll_timeout_s:.0f}s; it may still finish in {where}"
                )
            if not submitted.done():
                seen = ", ".join(sorted(seen_submit_rpcs)) or "none"
                raise _outcome_unknown(
                    f"migrated host: no {'/'.join(submit_rpcs)} reply within "
                    f"{budget:.0f}s of clicking submit (rpcs seen: {seen})"
                )
            first = submitted.result()
            started = VideoStarted(
                media_id=first.media_id,
                project_id=project_id or first.project_id,
                flow_operation_id=first.workflow_id,
                workflow_id=first.workflow_id,
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
            page.remove_listener("request", on_request)
            try:
                await page.unroute(BATCHEXECUTE_GLOB, guard)
            except Exception as exc:  # noqa: BLE001 - must not mask the real outcome
                log.debug("migrated.submit_unroute_failed", error=type(exc).__name__)

    async def _read_listing(self, page: Page, project_id: str, *, budget_s: float) -> Any | None:
        """The project's ``Zzl0ze`` payload, read in a second tab; ``None`` if none came.

        A second tab, because the generating page must not navigate mid-generation, and
        its own first-load listing was not observed to answer (spike 2026-10-27)."""
        got: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        async def on_response(response: Any) -> None:
            if got.done() or LISTING_RPC not in str(getattr(response, "url", "")):
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - a lost body leaves the fallback empty
                return
            for rid, payload in parse_frames(text):
                if rid == LISTING_RPC and not got.done():
                    got.set_result(payload)

        end = time.monotonic() + budget_s
        tab: Page | None = None
        try:
            tab = await page.context.new_page()
            tab.on("response", on_response)
            await tab.goto(
                MIGRATED_PROJECT_URL.format(project_id=project_id),
                wait_until="domcontentloaded",
                timeout=budget_s * 1000,
            )
            return await asyncio.wait_for(got, max(0.0, end - time.monotonic()))
        # Playwright's TimeoutError is not the builtin one. Any failure here leaves the
        # caller's exit 9, which names the project; this runs after Flow has billed.
        except (TimeoutError, PlaywrightError) as exc:
            log.warning(
                "migrated.video_listing_unanswered", budget_s=budget_s, error=type(exc).__name__
            )
            return None
        finally:
            if tab is not None:
                with contextlib.suppress(PlaywrightError):
                    await tab.close()

    async def _verify_submit_target(self, page: Page, submit: Locator) -> None:
        """Prove the submit button is visible and receives pointer events."""
        try:
            if not await submit.is_visible():
                raise RuntimeError("image submit button is not visible")
            # The JS hit-test reads viewport coordinates; a below-the-fold submit
            # would fail it even though Playwright's click auto-scrolls. Bring the
            # button into the viewport the gate is about to measure.
            await submit.scroll_into_view_if_needed()
            box = await submit.bounding_box()
            if not isinstance(box, dict) or not all(
                isinstance(box.get(key), (int, float)) for key in ("x", "y", "width", "height")
            ):
                raise RuntimeError("image submit button has no usable bounding box")
            state: Any = await page.evaluate(
                """
                ({x, y}) => {
                    // #778's closed vocabulary: tag + framework class tokens. Never the
                    // id or the raw class list, which carry account identifiers (#890).
                    const describe = (element) => {
                        if (!element) return null;
                        const cls = [...element.classList]
                            .filter((c) => /^(cdk|mat|mdc|flow|glue)-/.test(c))
                            .slice(0, 3);
                        return element.tagName.toLowerCase() +
                            (cls.length ? '.' + cls.join('.') : '');
                    };
                    const submit = [...document.querySelectorAll('button')].find((button) =>
                        [...button.querySelectorAll('mat-icon')].some((icon) =>
                            (icon.textContent || '').trim() === 'arrow_forward'));
                    const top = document.elementFromPoint(x, y);
                    const topButton = top?.closest?.('button');
                    const target = Boolean(
                        submit &&
                        (top === submit || submit.contains(top) || topButton === submit)
                    );
                    return {
                        target,
                        top: describe(top),
                    };
                }
                """,
                {
                    "x": float(box["x"]) + float(box["width"]) / 2,
                    "y": float(box["y"]) + float(box["height"]) / 2,
                },
            )
        except UiSelectorDriftError:
            raise
        except Exception as error:
            detail = await self._ui_failure_detail(
                page,
                phase="pre_submit_button_gate",
                selector="button -> arrow_forward",
                detail=(
                    "migrated host: image submit button failed the visibility or pointer "
                    "target check (host=migrated)"
                ),
                error=error,
            )
            raise UiSelectorDriftError(detail=detail) from error
        state_dict: dict[str, Any] = cast(dict[str, Any], state) if isinstance(state, dict) else {}
        if not state_dict.get("target"):
            blocker = state_dict.get("top")
            detail = await self._ui_failure_detail(
                page,
                phase="pre_submit_button_gate",
                selector="button -> arrow_forward",
                detail=(
                    "migrated host: an unexpected element receives pointer events at the "
                    f"image submit target (blocker={blocker}) (host=migrated)"
                ),
                error="submit target is covered",
            )
            raise UiSelectorDriftError(detail=detail)

    async def _pre_submit_gate(self, page: Page) -> Any:
        """Prove the composer is clear and submit is present and hit-testable.

        Runs before any network observer arms. Enablement is waited on

        downstream by the submit path itself (with the out-of-credits
        interrogation); this gate refuses missing/covered targets first.
        """
        await self._dismiss_cookie_bar(page)
        blockers = self._blocking_overlays(page)
        remaining = await blockers.count()
        if remaining:
            detail = await self._ui_failure_detail(
                page,
                phase="pre_submit_overlay_gate",
                selector=VISIBLE_OVERLAY,
                detail=(
                    f"migrated host: {remaining} blocking overlay(s) remain before image "
                    "Generate (host=migrated)"
                ),
                error=f"{remaining} blocking overlay(s)",
            )
            raise UiSelectorDriftError(detail=detail)
        submit = page.locator("button").filter(has=_ligature(page, "arrow_forward")).first
        if not await submit.count():
            await _raise_if_out_of_credits(page)
            detail = await self._ui_failure_detail(
                page,
                phase="pre_submit_button_gate",
                selector="button -> arrow_forward",
                detail="migrated host: image submit button is missing (host=migrated)",
                error="submit button missing",
            )
            raise UiSelectorDriftError(detail=detail)
        await self._verify_submit_target(page, submit)
        return submit

    async def submit_images_and_observe(
        self,
        page: Page,
        request: GenerateImageRequest,
        *,
        reference_ids: tuple[str, ...] = (),
        entity_ids: tuple[str, ...] = (),
    ) -> list[GeneratedImage]:
        """Submit Image mode and decode the completed ``ogiZ0b`` reply.

        ``reference_ids`` (media) and ``entity_ids`` (characters) are the EXACT reference
        set the submit may carry: missing or extra ids abort it before Flow acts (#723).
        """
        # Complete all UI gates before registering network observers or clicking
        # Generate. A deterministic DOM failure therefore cannot spend a request or
        # enter the transport retry loop.
        submit = await self._pre_submit_gate(page)
        loop = asyncio.get_running_loop()
        result: asyncio.Future[list[GeneratedImage]] = loop.create_future()
        route_error: asyncio.Future[WireFormatError] = loop.create_future()
        body_lost: asyncio.Future[None] = loop.create_future()
        grid = await _grid_images(page)
        # References and characters already exist; a late-listed upload is not output.
        baseline = None if grid is None else {*grid, *reference_ids, *entity_ids}

        def on_request(raw_request: Any) -> None:
            url = str(getattr(raw_request, "url", ""))
            if url_rpcid(url) != IMAGE_SUBMIT_RPC or route_error.done():
                return
            problem = _image_body_problem(
                request_body(raw_request), reference_ids, request.model, entity_ids
            )
            if problem is not None:
                route_error.set_result(
                    WireFormatError(detail=problem, route=f"batchexecute:{IMAGE_SUBMIT_RPC}")
                )

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            if url_rpcid(url) != IMAGE_SUBMIT_RPC or result.done() or body_lost.done():
                return
            status = int(getattr(response, "status", 0) or 0)
            if status != 200:
                result.set_exception(
                    WireFormatError(
                        detail=f"migrated image submit answered HTTP {status}",
                        status=status,
                        route=f"batchexecute:{IMAGE_SUBMIT_RPC}",
                    )
                )
                return
            try:
                text = await response.text()
            except Exception:  # noqa: BLE001 - a raw Playwright error here was retried (#1010)
                # #1075: the image was made; read it off the grid instead of the reply.
                body_lost.set_result(None)
                return
            try:
                refusal = _submit_refusal(text, (IMAGE_SUBMIT_RPC,))
                if refusal is not None:
                    raise refusal
                parsed = parse_frames(text)
                records = [
                    record
                    for rpcid, payload in parsed
                    if rpcid == IMAGE_SUBMIT_RPC
                    for record in image_records(rpcid, payload)
                ]
                if not records:
                    log.warning(
                        "migrated.image_submit_unparsed_reply",
                        rpcid=IMAGE_SUBMIT_RPC,
                        text_len=len(text),
                        parsed_count=len(parsed),
                        parsed_rpcids=[p[0] for p in parsed],
                    )
                    raise WireFormatError(
                        detail="migrated image submit returned no ogiZ0b frame",
                        route=f"batchexecute:{IMAGE_SUBMIT_RPC}",
                    )
                images = [
                    GeneratedImage(
                        media_name=record.media_id,
                        workflow_id=record.workflow_id,
                        seed=record.seed,
                        prompt=record.prompt,
                        # #789: the ogiZ0b reply carries no model field. Echoing
                        # the request back would report an attribution this host
                        # never confirmed — and with a hidden model picker (#788)
                        # the selected model can differ from the requested one,
                        # making the echo actively wrong on the single field a
                        # user would check to find out.
                        model_name_type=None,
                        aspect_ratio=request.aspect.value,
                        fife_url=record.image_url,
                        dimensions=record.dimensions,
                        display_name=record.display_name,
                    )
                    for record in records
                ]
            except Exception as exc:  # noqa: BLE001 - delivered through the waiting future
                result.set_exception(exc)
                return
            result.set_result(images)

        def is_image_submit(url: str) -> bool:
            return url_rpcid(url) == IMAGE_SUBMIT_RPC

        async def guard(route: Any, raw_request: Any) -> None:
            problem = await _guard_image_submit(
                route, raw_request, reference_ids, request.model, entity_ids
            )
            found = _image_body_references(request_body(raw_request))
            log.info(
                "migrated.image_submit_guarded",
                outcome="aborted" if problem else "passed",
                references=len(reference_ids),
                characters=len(entity_ids),
                # What the body actually carried, per slot (#723: extras rode slot 2).
                body_media=None if found is None else len(found[0]),
                body_characters=None if found is None else len(found[1]),
            )
            if problem is not None and not route_error.done():
                route_error.set_result(
                    WireFormatError(detail=problem, route=f"batchexecute:{IMAGE_SUBMIT_RPC}")
                )

        page.on("request", on_request)
        page.on("response", on_response)
        try:
            if reference_ids or entity_ids:
                await page.route(is_image_submit, guard)
            enable_deadline = time.monotonic() + SUBMIT_ENABLE_BUDGET_S
            while not await submit.is_enabled():
                if time.monotonic() >= enable_deadline:
                    await _raise_if_out_of_credits(page)
                    raise UiSelectorDriftError(
                        detail="migrated host: image submit stayed disabled (host=migrated)"
                    )
                await asyncio.sleep(SUBMIT_ENABLE_POLL_S)
            await self._click(page, submit, named=SUBMIT_BUTTON, timeout=5000)
            done, _ = await asyncio.wait(
                {result, route_error, body_lost},
                timeout=IMAGE_REPLY_BUDGET_S,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if route_error.done():
                raise route_error.result()
            if body_lost.done():
                return await _recover_images(page, request, baseline)
            if result not in done:
                raise _outcome_unknown(
                    f"migrated host: no {IMAGE_SUBMIT_RPC} image result within "
                    f"{IMAGE_REPLY_BUDGET_S:.0f}s of clicking submit"
                )
            return result.result()
        finally:
            # Consume the future's exception even on the paths that never read it
            # (a route error raised first, a timeout): otherwise asyncio logs
            # "exception was never retrieved" at GC, in a process that has already
            # reported a different, correct error. Not a no-op — do not delete.
            if result.done() and not result.cancelled():
                result.exception()
            page.remove_listener("request", on_request)
            page.remove_listener("response", on_response)
            if reference_ids or entity_ids:
                try:
                    await page.unroute(is_image_submit, guard)
                except Exception as exc:  # noqa: BLE001 - must not mask the real outcome
                    log.debug("migrated.image_submit_unroute_failed", error=type(exc).__name__)

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
            raise _outcome_unknown(
                f"migrated host: generation {workflow_id} was not terminal within the poll timeout"
            )
        grace = min(RESULT_URL_GRACE_S, max(deadline - time.monotonic(), 0.01))
        try:
            return await asyncio.wait_for(terminal, timeout=grace)
        except TimeoutError:
            log.info("migrated.result_url_not_observed", grace_s=grace)
            return done_no_url.result()
