"""Frame upload and picker interactions for i2v/r2v.

A part of `MigratedComposer`, moved byte-identical from
`migrated_composer.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.transports._common import safe_page_url
from gflow_cli.api.transports._mc_common import (
    BOUND_CHIP,
    DIALOG,
    EMPTY_CHIP,
    FRAME_COMMIT_GRACE_S,
    FRAME_COMMIT_HIDDEN_S,
    FRAME_PICKER_OPEN_S,
    FRAME_SEARCH_ATTEMPTS,
    FRAME_SEARCH_RETRY_PAUSE_S,
    FRAME_THUMB_VISIBLE_S,
    FRAME_UPLOAD_S,
    MENU_ITEM,
    PICKER,
    PICKER_CONFIRM,
    PICKER_OPTION,
    PICKER_SEARCH,
    TOOLBAR_ADD,
    UPLOAD_MENU_ITEM,
    UPLOAD_RPC,
    UUID_RE,
    _first_uuid,
    _picker_pane,
    _project_snapshot,
    _unique_display_name,
    _unreadable_rpc_error,
    url_rpcid,
)
from gflow_cli.api.transports._mc_settings_pane import SettingsPaneMixin
from gflow_cli.api.transports.batchexecute import rpc_errors
from gflow_cli.errors import (
    MediaUploadRejectedError,
    ReferenceNotFoundError,
    UiSelectorDriftError,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)


class UploadFrameMixin(SettingsPaneMixin):
    """Frame upload and picker interactions for i2v/r2v."""

    # --- start frame (i2v) ------------------------------------------------------

    async def attach_start_frame(self, page: Page, project_id: str, image_path: Path) -> str:
        """Upload ``image_path`` through the editor's own Upload entry, bind it on the
        Start chip by file name, and return the media id the app's ``maseQ`` reply
        named for it — the id the submit body is then asserted to carry.

        The upload is permanent in the Flow project (it lands in the library like any
        other asset). The picker is library-only and searched by display name, so what
        is uploaded is a run-unique COPY of ``image_path`` — see below.
        """
        from gflow_cli.api.client import validate_image_file  # noqa: PLC0415 - cycle

        await validate_image_file(image_path)
        # No outer budget: each leg is bounded, and an outer one firing first would
        # replace the stage-named failure with a generic "attach timed out".
        media_id, display_name = await self._upload_via_toolbar(page, project_id, image_path)
        await self._pick_frame_by_name(page, display_name, media_id)
        return media_id

    async def attach_end_frame(self, page: Page, project_id: str, image_path: Path) -> str:
        """Upload ``image_path`` through the editor's own Upload entry, bind it on the
        End chip by file name, and return the media id — mirroring
        :meth:`attach_start_frame`. The Start chip must already be bound: the pick
        clicks the first REMAINING empty chip and then requires two bound chips, so
        a silently unbound Start surfaces here instead of mis-binding End.
        """
        from gflow_cli.api.client import validate_image_file  # noqa: PLC0415 - cycle

        await validate_image_file(image_path)
        media_id, display_name = await self._upload_via_toolbar(page, project_id, image_path)
        await self._pick_frame_by_name(
            page, display_name, media_id, chip_label="End", expect_bound_chips=2
        )
        return media_id

    async def upload(self, page: Page, project_id: str, image_path: Path) -> tuple[str, str]:
        """Upload ``image_path`` into the project; ``(media_id, run-unique caption)``.

        The public face of :meth:`_upload_via_toolbar`, for a reference that will be
        mentioned in place on later rows instead of uploaded again (#913).
        """
        return await self._upload_via_toolbar(page, project_id, image_path)

    async def _upload_via_toolbar(
        self, page: Page, project_id: str, image_path: Path
    ) -> tuple[str, str]:
        """Toolbar ``+`` → the ``upload`` menu item → the file chooser → the app's own
        ``maseQ`` upload, observed for the media id it returns. Nothing is replayed.

        Returns ``(media_id, display_name)``. The bytes are handed to the chooser with a
        **run-unique display name** (:func:`_unique_display_name`) rather than the file's
        own, because both callers find the upload again by that name — so the caller must
        search for the name returned here, never for ``image_path.name``.
        """
        display_name = _unique_display_name(image_path)
        loop = asyncio.get_running_loop()
        reply: asyncio.Future[tuple[int, str | None]] = loop.create_future()
        route = f"batchexecute:{UPLOAD_RPC}"

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            if "batchexecute" not in url or url_rpcid(url) != UPLOAD_RPC or reply.done():
                return
            status = int(getattr(response, "status", 0) or 0)
            text: str | None = ""
            if status == 200:
                try:
                    text = await response.text()
                except Exception:  # noqa: BLE001 - the write may have persisted
                    text = None
            if not reply.done():
                reply.set_result((status, text))

        # The response listener alone cannot tell "the page never asked" from "Flow was
        # slow" — and those are different bugs with opposite fixes (#719: shape A is a
        # blocked send, shape B is a lost reply). One boolean splits them at the point of
        # failure instead of leaving both as one 60 s timeout.
        sent = False

        def on_request(request: Any) -> None:
            nonlocal sent
            url = str(getattr(request, "url", ""))
            if "batchexecute" in url and url_rpcid(url) == UPLOAD_RPC:
                sent = True

        page.on("response", on_response)
        page.on("request", on_request)
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
            # Flow holds an account's FIRST upload behind a one-time "rights to use this
            # image" confirmation. It renders only once the chooser has handed the file
            # over — so `_dismiss_dialog`, back in `ensure_editor`, never sees it — and
            # Flow sends nothing until a human accepts, which is why the wait below used
            # to expire on a request the page had already declined to make (#719).
            #
            # Counted, never matched. The dialog's two buttons are
            # `button.flow-button-medium` with no ligature and no data attribute,
            # separable only by DOM order, and its copy is translated — no anchor there
            # satisfies this module's locale rule. "A dialog appeared between the file
            # being chosen and the wait expiring" is upload-related by construction: it
            # needs no selector and cannot rot when Angular renames a class. The baseline
            # is taken BEFORE `set_files` so an already-open modal is never blamed.
            #
            # gflow does not click it: accepting affirms that the ACCOUNT OWNER holds the
            # rights to the content, which is not a claim a script may make for someone.
            # Measured 2026-09-08 across 6 runs on ci-probe —
            # docs/superpowers/spikes/2026-09-08-migrated-upload-fails-two-ways.md
            dialogs_before = await page.locator(DIALOG).count()
            # A FilePayload, not a path: the display name Flow lists the asset under is
            # ours to choose, so uniqueness needs no copy on disk (and leaves no temp file
            # to leak). `read_bytes` is off-thread for the same reason the header read in
            # `client.py` is — the file is up to MAX_IMAGE_BYTES.
            await chooser.set_files(
                {
                    "name": display_name,
                    "mimeType": mimetypes.guess_type(image_path.name)[0]
                    or "application/octet-stream",
                    "buffer": await asyncio.to_thread(image_path.read_bytes),
                }
            )
            try:
                status, text = await asyncio.wait_for(reply, timeout=FRAME_UPLOAD_S)
            except TimeoutError:
                try:
                    opened = await page.locator(DIALOG).count() > dialogs_before
                except Exception:  # noqa: BLE001 - a probe is never the failure it reports
                    # The likeliest reason no reply came is that the page died or navigated,
                    # in which case this count raises too. Letting that escape would replace
                    # a mapped exit 27 and its remediation with an unmapped traceback — the
                    # #752 lesson, one surface over.
                    opened = False
                if opened and not sent:
                    raise MediaUploadRejectedError(
                        detail=(
                            f"migrated host: a dialog opened after the file was chosen and no "
                            f"{UPLOAD_RPC} request left the page — most likely Flow's one-time "
                            f"upload-terms confirmation (host=migrated)"
                        ),
                        route=route,
                        remediation_hint=(
                            "Open the project on flow.google.com, upload any image by hand, "
                            "and accept the one-time 'rights to use this image' dialog. gflow "
                            "does not accept it for you: it affirms that YOU hold the rights "
                            "to what you upload. It appears once per account — after that, "
                            "uploads work unattended. If you see a different dialog instead "
                            "(an error, a quota notice, a re-login), that is the one blocking "
                            "the upload. Nothing was spent; re-run when done."
                        ),
                    ) from None
                # Say which half of #719 this is. `sent` is observed, not inferred: the
                # request listener above saw the upload leave the page, or it did not.
                went_out = (
                    "the request left the page and Flow did not answer in time"
                    if sent
                    else "no upload request ever left the page"
                )
                raise MediaUploadRejectedError(
                    detail=(
                        f"migrated host: no {UPLOAD_RPC} reply within {FRAME_UPLOAD_S:.0f}s "
                        f"of choosing the file — {went_out}"
                    ),
                    route=route,
                    remediation_hint=(
                        "If the request left the page, Flow accepted the upload and did not "
                        "answer in time — an intermittent fault on this host (#719), not "
                        "your file: re-run, and the same file usually succeeds. If nothing "
                        "left the page, something client-side stopped it — check for a modal "
                        "on flow.google.com. Either way nothing was spent. Re-encoding the "
                        "image does NOT help; three different files were ruled out in #719."
                    ),
                ) from None
            if status != 200:
                raise MediaUploadRejectedError(
                    detail=f"migrated host: the upload rpc {UPLOAD_RPC} answered HTTP {status}",
                    status=status,
                    route=route,
                )
            if text is None:
                service_error = _unreadable_rpc_error(
                    UPLOAD_RPC, location=f"{safe_page_url(page.url)} (upload={display_name})"
                )
                try:
                    _, tiles = await _project_snapshot(page, project_id, upload_name=display_name)
                    matches = [t["id"] for t in tiles if t["name"] == display_name]
                    if (
                        len(matches) == 1
                        and UUID_RE.fullmatch(matches[0])
                        and matches[0].lower() != project_id.lower()
                    ):
                        log.info("migrated.upload_recovered", media_id=matches[0])
                        return matches[0], display_name
                except Exception as exc:  # noqa: BLE001 - retain the original uncertain reply
                    log.info("migrated.upload_readback_unverified", error_type=type(exc).__name__)
                raise service_error
            media_id = _first_uuid(text)
            if media_id is None and any(
                e.rpcid == UPLOAD_RPC and e.code == 3 for e in rpc_errors(text)
            ):
                # gRPC 3 INVALID_ARGUMENT: Flow refused this file (a 1x1 PNG, measured
                # 3/3 on 2026-10-20), not a protocol break (#755).
                raise MediaUploadRejectedError(
                    detail=(
                        f"migrated host: Flow declined the image {display_name} "
                        f"({UPLOAD_RPC} answered INVALID_ARGUMENT)"
                    ),
                    status=status,
                    route=route,
                    remediation_hint=(
                        "Flow refuses very small or degenerate images. Re-try with a "
                        "normal-sized image; nothing was spent."
                    ),
                )
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
            return media_id, display_name
        finally:
            page.remove_listener("response", on_response)
            page.remove_listener("request", on_request)

    @staticmethod
    async def _open_frame_picker(page: Page, chip_label: str = "Start") -> Any:
        """Click the first remaining empty chip (Start, then End once Start is bound)
        and wait for the library picker's search box."""
        chip = page.locator(EMPTY_CHIP).first
        if not await chip.count():
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: no empty {chip_label} chip ({EMPTY_CHIP}) to bind the frame "
                    "on — is the Frames submode selected? (host=migrated)"
                ),
            )
        await chip.click(timeout=4000)
        picker = _picker_pane(page)
        try:
            await picker.locator(PICKER_SEARCH).first.wait_for(
                state="visible", timeout=int(FRAME_PICKER_OPEN_S * 1000)
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the frame picker ({PICKER}) did not open within "
                    f"{FRAME_PICKER_OPEN_S:.0f}s of clicking the {chip_label} chip (host=migrated)"
                ),
            ) from e
        return picker

    async def _pick_frame_by_name(
        self,
        page: Page,
        name: str,
        media_id: str,
        *,
        chip_label: str = "Start",
        expect_bound_chips: int = 1,
    ) -> None:
        """Empty chip → the library picker → search by display name → first option →
        then ``expect_bound_chips`` bound thumbnails must be visible (1 after Start,
        2 after End). An unbound chip is refused here: an empty Frames submit goes
        out as text-to-video (the labs #125 shape on this host)."""
        for attempt in range(1, FRAME_SEARCH_ATTEMPTS + 1):
            picker = await self._open_frame_picker(page, chip_label)
            search = picker.locator(PICKER_SEARCH).first
            await search.click(timeout=4000)
            await page.keyboard.insert_text(name)
            # Containment, never `_exact` (#860): a picker tile is an icon node followed
            # by the file name, and a locator's text is the concatenation of both --
            # `imageshero-ab12cd34.png` for an asset named `hero-ab12cd34.png`. An anchored
            # match can therefore never hold on this cohort. The display name is
            # run-unique by construction (#792), so a substring is exact enough to be
            # unable to bind a stale copy of the same file.
            options = picker.locator(PICKER_OPTION).filter(has_text=name)
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
                        f"{media_id}) — the tag after the stem is expected: gflow uploads "
                        "under a run-unique display name so the search cannot bind an "
                        "older copy of the same file (#792). The picker listed: "
                        f"{', '.join(repr(t) for t in listed[:8]) or 'nothing'}"
                    ),
                ) from e
            else:
                # Outside the except above on purpose: a click that fails here is not
                # "the picker never listed it", and must not be reported as one.
                await options.first.click(timeout=4000)
                break
        try:
            await _picker_pane(page).wait_for(
                state="hidden", timeout=int(FRAME_COMMIT_GRACE_S * 1000)
            )
        except PlaywrightTimeoutError:
            # Cohort split (#792): on some accounts the option click no longer commits and
            # the picker waits for its own confirm. Only a TIMEOUT lands here — a closed
            # page or a detached frame is a different failure and travels unchanged, or the
            # probe below would re-raise it from inside this handler as an unmapped
            # traceback (the #752 lesson, already learned one method over at the upload).
            #
            # Visibility, not count(): a detached pane's button still counts, and clicking
            # one waits out its own timeout on something that cannot be clicked.
            confirm = _picker_pane(page).locator(PICKER_CONFIRM).first
            had_confirm = await confirm.is_visible()
            if had_confirm:
                try:
                    await self._click(page, confirm, named=PICKER_CONFIRM, timeout=4000)
                except UiSelectorDriftError:
                    # A confirm that would not take the click is not yet the failure: the
                    # picker this cohort's click DID commit may simply be on its way out.
                    # Fall through to the long wait and let the picker have the last word.
                    log.info("migrated.frame_confirm_click_missed", issue_ref="#792")
                else:
                    log.info("migrated.frame_confirm_clicked", media_id=media_id, issue_ref="#792")
            try:
                await _picker_pane(page).wait_for(
                    state="hidden", timeout=int(FRAME_COMMIT_HIDDEN_S * 1000)
                )
            except Exception as e:
                # Say WHICH of the two it was: a picker that ignored its own confirm is
                # a different drift from one that never offered it, and reporting both
                # as "stayed open" is how the next cohort change reads as this one.
                why = (
                    "even after its confirm was clicked"
                    if had_confirm
                    else f"and carries no confirm ({PICKER_CONFIRM})"
                )
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: the frame picker stayed open "
                        f"{FRAME_COMMIT_GRACE_S + FRAME_COMMIT_HIDDEN_S:.1f}s after picking "
                        f"{name!r} {why} (host=migrated)"
                    ),
                ) from e
        try:
            await (
                page.locator(BOUND_CHIP)
                .nth(expect_bound_chips - 1)
                .wait_for(state="visible", timeout=int(FRAME_THUMB_VISIBLE_S * 1000))
            )
        except Exception as e:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: the {chip_label} chip did not bind {name!r} — "
                    f"fewer than {expect_bound_chips} bound chip(s) within "
                    f"{FRAME_THUMB_VISIBLE_S:.0f}s of the pick; refusing to submit what "
                    "would go out as text-to-video (host=migrated)"
                ),
            ) from e
        log.info("migrated.frame_bound", media_id=media_id)
