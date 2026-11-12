"""Reference attach: uploads, existing assets, image UUIDs, characters, audio.

A part of `VideoGenerationMixin`, moved byte-identical from
`ui_automation_video.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api.transports._common import extract_project_id
from gflow_cli.api.transports._video_common import (
    _CONTEXT_INCLUDE_TIER_NAMES,
    _INCLUDE_BUTTON_TIER_NAMES,
    ADD_MEDIA_BUTTON,
    ADD_TO_PROMPT_DIALOG,
    DIALOG_ANY,
    PICKER_CONTEXT_INCLUDE,
    PICKER_INCLUDE_BUTTON,
    PICKER_PERSONAGENS_TAB,
    PICKER_SEARCH_INPUT,
    PICKER_VOZES_TAB,
    REMOTE_PICKER_CLOSE_TIMEOUT_S,
    UPLOAD_IMAGE_ROUTE,
    UPLOAD_MEDIA_BUTTON,
    _capture_debug_screenshot,
    _capture_picker_dom_dump,
    _upload_rejection_message,
    screenshot_clause,
)
from gflow_cli.api.transports._video_picker_project import PickerProjectMixin
from gflow_cli.errors import MediaUploadRejectedError, ReferenceNotFoundError, TransportTimeoutError
from gflow_cli.file_integrity import matches_recorded_file

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

    from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

log = structlog.get_logger(__name__)


class ReferenceAttachMixin(PickerProjectMixin):
    """Reference attach: uploads, existing assets, image UUIDs, characters, audio."""

    @staticmethod
    async def _pick_option_and_include(
        page: Page,
        name: str,
        *,
        surface: str,
        detail: str,
        out_dir: Path | None,
        dialog_timeout_s: float,
    ) -> None:
        """Shared picker flow: type ``name`` into the open resource picker,
        select the matching result tile, fire the locale-safe include action,
        and verify the picker dialog closed. Used by both the I2V frame and R2V
        reference remote-attach paths (the picker is identical once open)."""
        search_input = page.locator(PICKER_SEARCH_INPUT)
        # Human-like typing jitter to dodge WAF bot heuristics — not a security
        # context, so a plain PRNG is fine.
        await search_input.press_sequentially(name, delay=random.randint(10, 50))  # NOSONAR  # noqa: S311 - human-pacing jitter, not cryptography
        await page.wait_for_timeout(600)

        # Text match — apostrophes in the display name would break a
        # `:has-text('{name}')` CSS selector.
        tile = VideoGenerationMixin._remote_option_tile(page, name).first
        await tile.wait_for(state="visible", timeout=8000)
        await tile.click()
        await page.wait_for_timeout(300)

        # #529 live recon (pt profile): clicking the result tile now attaches
        # directly and closes the picker — the include button exists only in
        # the hover-preview pane. Treat a closed dialog as success and fall
        # through to the legacy include-button flow only while it stays open.
        dialog = page.locator(DIALOG_ANY).last
        try:
            await dialog.wait_for(state="hidden", timeout=3000)
        except Exception:  # noqa: BLE001 - any wait failure = dialog still open
            pass
        else:
            return

        # Include via the tiered, locale-safe resolver (a hardcoded English
        # "Add to Prompt" here previously hung non-English accounts — #170/#56).
        include = await VideoGenerationMixin._resolve_include_action(
            page,
            PICKER_INCLUDE_BUTTON,
            _INCLUDE_BUTTON_TIER_NAMES,
            surface=surface,
            detail=detail,
            out_dir=out_dir,
            screenshot_name=f"{surface}_missing.png",
        )
        await include.click(timeout=3000)
        await page.wait_for_timeout(600)

        # Confirm the picker closed — otherwise the include never registered and
        # logging success would be a silent false positive.
        dialog = page.locator(DIALOG_ANY).last
        try:
            await dialog.wait_for(state="hidden", timeout=dialog_timeout_s * 1000)
        except Exception as e:
            raise TransportTimeoutError(
                f"{detail} picker dialog did not close after {dialog_timeout_s}s "
                "(the include action may not have registered)",
            ) from e

    @staticmethod
    def _remote_option_tile(page: Page, name: str) -> Locator:
        """Locate a picker result tile by its display name.

        Matches the option's text via ``has_text`` (a Playwright arg, never a
        CSS string): ``name`` is a stored ``display_name`` or the original
        generation prompt, both of which commonly contain an apostrophe or
        quote that would break a single-quoted ``:has-text()`` CSS selector
        (PR #237 review).
        """
        # Anchored match: a substring match would let 'cabin' select
        # 'cabin at night' and .first attach the wrong image (PR #245 review).
        # #529 live recon: the picker dialog exposes NO accessible tree (the
        # option's computed name is empty), so role+name matching can never
        # find the tile — match the option's text instead. The text carries
        # the picker's localized media-type badge after the display name
        # ('…map\nImagem' on a pt profile); tolerate only a trailing
        # capitalized badge word, which 'at night' is not.
        pattern = re.compile(rf"^{re.escape(name)}(\s?[^\Wa-z0-9_]\S*)?$")
        return page.locator("[role='option']", has_text=pattern)

    @staticmethod
    async def _upload_via_open_dialog(
        page: Page,
        image: Path,
        *,
        log_label: str,
        out_dir: Path | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        """With a media dialog ALREADY open, upload `image` and commit it into
        the active slot/carousel: 'Upload media' (file chooser) -> wait the
        uploadImage XHR 200 (bytes stored) -> 'Add to Prompt' -> wait the dialog
        to CLOSE (commit registered) before returning, so the caller never
        submits before the image binds. Shared by I2V slots and R2V references."""
        uploaded: list[int] = []  # uploadImage response statuses, in arrival order

        def on_response(response: Any) -> None:
            if UPLOAD_IMAGE_ROUTE in response.url:
                uploaded.append(response.status)
                log.info(
                    "ui_automation_video.image_uploaded",
                    target=log_label,
                    status=response.status,
                )

        page.on("response", on_response)
        try:
            # The locale-agnostic icon selector is the only anchor (#1273). On a
            # miss, fail loud with a screenshot; short timeouts so it cannot
            # become the silent ~34s hang that #56 was about.
            try:
                async with page.expect_file_chooser(timeout=8000) as fc_info:
                    await page.locator(UPLOAD_MEDIA_BUTTON).first.click(timeout=4000)
                chooser = await fc_info.value
            except Exception as e:
                shot = await _capture_debug_screenshot(
                    page,
                    out_dir,
                    f"debug_upload_no_chooser_{log_label}.png",
                )
                msg = (
                    f"The 'upload' icon button did not open a file chooser for "
                    f"{log_label!r} — Google likely changed the media dialog (issue #56). "
                    f"Report it with the screenshot at "
                    f"https://github.com/ffroliva/gflow-cli/issues."
                    f"{screenshot_clause(shot)}"
                )
                raise RuntimeError(msg) from e
            await chooser.set_files(str(image))
            deadline = time.monotonic() + timeout_s
            while not uploaded and time.monotonic() < deadline:
                await asyncio.sleep(0.5)
            if not uploaded:
                log.warning("ui_automation_video.upload_incomplete", target=log_label)
            else:
                # Fail loud on a rejected upload instead of committing an empty
                # slot that silently falls back to T2V (#125). Typed (#287) so
                # callers get exit 27 + a re-encode hint, not "Unexpected error."
                rejection = _upload_rejection_message(uploaded[-1], log_label)
                if rejection is not None:
                    raise MediaUploadRejectedError(detail=rejection, route=UPLOAD_IMAGE_ROUTE)
        finally:
            page.remove_listener("response", on_response)

        # 'Add to Prompt' = the only iconless button in the open dialog (its text
        # is localized; see the UPLOAD_MEDIA_BUTTON note). Select it structurally.
        add_btn = (
            page.locator(ADD_TO_PROMPT_DIALOG)
            .last.locator("button")
            .filter(has_not=page.locator("i.google-symbols"))
            .last
        )
        if await add_btn.count():
            await add_btn.click()
        try:
            await page.locator(DIALOG_ANY).last.wait_for(state="hidden", timeout=15_000)
        except Exception:
            log.warning("ui_automation_video.dialog_close_timeout", target=log_label)
        await page.wait_for_timeout(1500)

    @staticmethod
    async def _attach_references(
        page: Page,
        images: list[Path],
        *,
        out_dir: Path | None,
        timeout_s: float = 120.0,
        prefer_existing: bool = False,
    ) -> None:
        """R2V: attach up to MAX_REFERENCE_IMAGES reference images. References
        have no Start/End slots — each is added via the 'Add Media' button, which
        opens the same media dialog. Must run with the settings panel closed.

        When ``prefer_existing`` is set (image i2i), each ref is first looked up
        in the open picker by its filename before uploading: Flow names an
        uploaded file by its exact filename, so a repeated local ref is DEDUPED —
        selected in place — instead of re-uploaded, avoiding the duplicate
        library entries of #314. Upload stays the fallback when no library match
        exists (fresh file, or a picker cohort without a search box). Off by
        default so the R2V video path keeps its upload-every-time behaviour."""
        missing = [str(p) for p in images if not p.exists()]
        if missing:
            msg = f"reference image(s) not found: {missing}"
            raise FileNotFoundError(msg)
        attached = 0
        for i, img in enumerate(images):
            add_media = page.locator(ADD_MEDIA_BUTTON).first
            try:
                await add_media.wait_for(state="visible", timeout=8000)
            except Exception as e:
                if i == 0:
                    shot = await _capture_debug_screenshot(page, out_dir, "debug_no_add_media.png")
                    clause = screenshot_clause(shot)
                    msg = f"'Add Media' button not found for first reference.{clause}"
                    raise RuntimeError(
                        msg,
                    ) from e
                # Flow removes the Add-Media button once the per-model reference
                # cap is hit (omni_flash=7, veo_3_1_*=3). The DTO enforces this
                # when the model is known; for an unknown (None) model we only
                # learn the cap here. Proceed with what's attached + warn loudly.
                log.warning(
                    "ui_automation_video.reference_cap_reached",
                    attached=attached,
                    requested=len(images),
                    note="Flow hid 'Add Media' — per-model reference cap reached",
                )
                break
            await add_media.click()
            await page.wait_for_timeout(1000)
            if prefer_existing and await VideoGenerationMixin._try_select_existing_by_filename(
                page, img.name, out_dir=out_dir
            ):
                attached += 1
                log.info("ui_automation_video.reference_attached", index=i, deduped=True)
                continue
            await VideoGenerationMixin._upload_via_open_dialog(
                page,
                img,
                log_label=f"ref{i}",
                out_dir=out_dir,
                timeout_s=timeout_s,
            )
            attached += 1
            log.info("ui_automation_video.reference_attached", index=i)

    @staticmethod
    async def _try_select_existing_by_filename(
        page: Page, filename: str, *, out_dir: Path | None
    ) -> bool:
        """In the OPEN reference picker, try to select an existing library asset
        whose display name equals ``filename`` — dedup instead of re-upload (#314).

        Flow names an uploaded file by its exact filename (live-verified: an
        upload of ``zzdedupprobe.png`` surfaces as a picker option named
        ``zzdedupprobe.png``), and the picker search filters on that name. So a
        repeated local ref can be attached by selecting the existing tile rather
        than uploading a duplicate — without persisting or trusting any media
        UUID across runs.

        Contract: returns ``True`` once a matching asset is attached; ``False``
        when the search + a virtualised-grid scroll surface no match, having
        cleared the filter so the caller's upload path starts from a clean grid.
        A match that is *found but then fails to attach* (the include never
        registers) raises ``TransportTimeoutError`` — a real UI failure is
        surfaced rather than silently uploading a duplicate, matching the
        media-UUID path. Keyed on the exact filename: a name Flow sanitises on
        upload (stored display name then differs) simply misses and falls back to
        upload; verified for ASCII-safe names.
        """
        # The picker library has its OWN active project (#287) — align it to the
        # editor's project so the search sees this project's media, where the
        # duplicate uploads accumulate.
        await VideoGenerationMixin._sync_picker_project(page, out_dir=out_dir)

        search = page.locator(PICKER_SEARCH_INPUT).first
        if not await search.count():
            return False  # no search box (older / #174 full-page cohort) → upload
        await search.fill("")
        # Human-like typing jitter to dodge WAF heuristics — not security.
        await search.press_sequentially(filename, delay=random.randint(10, 50))  # NOSONAR  # noqa: S311 - human-pacing jitter, not cryptography
        await page.wait_for_timeout(600)

        # Match the tile by its image ALT (= the exact filename, locale-invariant).
        # The option's accessible NAME also carries a localised media-type suffix
        # ("Image"/"Imagem"), so an exact name match would miss; the img alt is
        # exactly the filename. get_by_alt_text avoids CSS-quoting a filename.
        tile = (
            page.get_by_role("option").filter(has=page.get_by_alt_text(filename, exact=True)).first
        )
        # The library grid is virtualised (react-virtuoso) — a real match can sit
        # off-screen after a broad search, absent from the DOM until scrolled into
        # range. Scroll (progress-bounded, #287) before concluding there is no
        # existing asset, so a still-existing dup isn't re-uploaded.
        if not await tile.count() and not (
            await VideoGenerationMixin._scroll_picker_grid_until_rendered(page, tile)
        ):
            await search.fill("")  # clear the filter for the upload fallback
            return False

        await VideoGenerationMixin._attach_selected_tile(
            page,
            tile,
            out_dir=out_dir,
            detail=f"filename ref {filename!r}",
            surface="filename_ref_include",
            screenshot_name="filename_ref_include_missing.png",
            dialog_timeout_s=REMOTE_PICKER_CLOSE_TIMEOUT_S,
        )
        log.info("ui_automation_video.reference_deduped_by_filename", filename=filename)
        return True

    @staticmethod
    async def _attach_remote_references(
        page: Page,
        ref_names: list[str],
        *,
        out_dir: Path | None,
    ) -> None:
        """Attach remote images by searching their display_name in the All tab.

        The picker indexes Flow's own short auto-caption, NOT the generation
        prompt, so a name that never matches used to surface as a bare
        Playwright ``TimeoutError`` after 8 s — no indication of what went
        wrong. The raw picker/catalog comparison is recorded in
        ``docs/superpowers/spikes/2026-08-15-picker-tile-alt-text.md``. A miss is
        now a typed, self-documenting error that names the term searched and
        lists what the picker actually offered.
        """
        # playwright is a TYPE_CHECKING-only import at module level; the
        # timeout class is needed at runtime to type the picker miss.
        from playwright.async_api import (  # noqa: PLC0415
            TimeoutError as PlaywrightTimeoutError,
        )

        for name in ref_names:
            add = page.locator(ADD_MEDIA_BUTTON).first
            await add.wait_for(state="visible", timeout=8000)
            await add.click()
            await page.wait_for_timeout(800)

            try:
                await VideoGenerationMixin._pick_option_and_include(
                    page,
                    name,
                    surface="remote_reference_include",
                    detail=f"remote reference {name!r}",
                    out_dir=out_dir,
                    dialog_timeout_s=REMOTE_PICKER_CLOSE_TIMEOUT_S,
                )
            except PlaywrightTimeoutError as exc:
                offered = await VideoGenerationMixin._picker_option_names(page)
                shot = await _capture_debug_screenshot(
                    page, out_dir, "debug_remote_reference_not_found.png"
                )
                raise ReferenceNotFoundError(
                    f"no media named {name!r} in this project's picker. Flow indexes a "
                    f"short auto-caption, not the generation prompt, so passing a prompt "
                    f"as a reference name will not match. "
                    + (
                        f"The picker offered: {offered}."
                        if offered
                        else "The picker listed no selectable media."
                    )
                    + " Pass the asset's media UUID instead, or use --ref with a local file."
                    + screenshot_clause(shot)
                ) from exc
            log.info("ui_automation_video.remote_reference_attached", display_name=name)

    @staticmethod
    async def _picker_option_names(page: Page, limit: int = 12) -> list[str]:
        """Names the open picker is actually offering — for a not-found error.

        Best-effort: a diagnostic must never mask the failure it describes.
        """
        try:
            return await page.evaluate(
                """(n) => [...document.querySelectorAll("[role='option']")]
                       .map(o => (o.getAttribute('aria-label') || o.textContent || '')
                                   .replace(/\\s+/g, ' ').trim())
                       .filter(Boolean).slice(0, n)""",
                limit,
            )
        except Exception:  # noqa: BLE001 - diagnostic only
            return []

    @staticmethod
    def _existing_asset_tile(page: Page, media_id: str) -> Locator:
        """Locate a picker tile for an already-existing asset by its media UUID.

        The tile's thumbnail URL is ``media.getMediaUrlRedirect?name=<uuid>``, so
        matching ``img[src*=<uuid>]`` selects the exact asset with no dependence
        on a display name (robust to name collisions) and no search term.
        """
        return page.locator(f"[role='option']:has(img[src*='{media_id}'])").first

    @staticmethod
    async def _select_existing_asset(
        page: Page,
        media_id: str,
        display_name: str,
        *,
        out_dir: Path | None,
        dialog_timeout_s: float = REMOTE_PICKER_CLOSE_TIMEOUT_S,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> bool:
        """In the OPEN reference picker, select the already-existing Flow asset
        identified by ``media_id`` (preferred over uploading a duplicate).

        Flow's browser picker is name-addressed: search by the catalog-recorded
        ``display_name``, then assert the exact UUID in the surfaced tile's
        thumbnail URL. This handles duplicate names without selecting the wrong
        asset. It deliberately does not scroll the unfiltered virtualised grid
        or type UUID fragments into the name search. A missing/stale name or
        unavailable search input falls through to the
        caller's verified local-file fallback or typed error.

        #546 rename self-healing: on a search MISS, ``name_resolver`` (a sync
        callable, UUID -> current Flow display name or ``None``) is consulted
        once; a non-empty DIFFERENT name triggers exactly one retry search
        (``_search_picker_for_tile`` clears the box before typing). Cached
        name = optimization; listing = truth; UUID = identity. A raising
        resolver is swallowed with a warning and the fallback chain proceeds.

        The resolver is invoked via ``asyncio.to_thread`` so it may block on
        I/O: the CLI bridge blocks its worker thread on a listing fetch it
        schedules back onto THIS loop (``run_coroutine_threadsafe``), which
        only completes because the loop is parked here awaiting the worker.
        """
        if not display_name:
            return False
        tile = VideoGenerationMixin._existing_asset_tile(page, media_id)
        found = await VideoGenerationMixin._search_picker_for_tile(page, tile, display_name)
        if found is False and name_resolver is not None:
            fresh: str | None = None
            try:
                # to_thread: the resolver is a SYNC callable that may block on
                # I/O (the CLI bridge parks its worker thread on a listing
                # fetch scheduled back onto this loop via
                # run_coroutine_threadsafe — see refresh_resolver.wire_refresh_resolver).
                # Calling it inline would deadlock that bridge.
                fresh = await asyncio.to_thread(name_resolver, media_id)
            except Exception:  # noqa: BLE001 - resolver must never kill the generation
                # Log only the media_id — the exception message may carry
                # listing payload fragments (redaction).
                log.warning("ui_automation_video.name_resolver_failed", media_id=media_id)
            if fresh and fresh != display_name:
                # Bounded second pass with the resolver-fresh name — NOT a loop.
                found = await VideoGenerationMixin._search_picker_for_tile(page, tile, fresh)
        if not found:
            # #287 diagnosis telemetry: capture WHAT the picker was showing
            # when the lookup gave up — which attribute carries the media
            # identity, and which project the library view was on, are the
            # key unknowns on a live miss.
            # cast + isinstance: a unit-test fake's page.url may not be a str.
            page_url = cast("object", page.url)
            project_id = extract_project_id(page_url) if isinstance(page_url, str) else None
            # Capture only the filtered result set. When search is unavailable,
            # avoid collecting unrelated assets from the unfiltered picker.
            shot = (
                await _capture_debug_screenshot(
                    page, out_dir, f"debug_picker_miss_{media_id[:8]}.png"
                )
                if found is False
                else None
            )
            dump = (
                await _capture_picker_dom_dump(page, out_dir, media_id, project_id)
                if found is False
                else None
            )
            log.warning(
                "ui_automation_video.existing_asset_not_found",
                media_id=media_id,
                project_id=project_id,
                screenshot=str(shot) if shot is not None else None,
                dom_dump=str(dump) if dump is not None else None,
            )
            if found is False:
                clear_search = page.locator(PICKER_SEARCH_INPUT).first
                await clear_search.fill("")
                await page.wait_for_timeout(400)
            return False

        await VideoGenerationMixin._attach_selected_tile(
            page,
            tile,
            out_dir=out_dir,
            detail=f"image ref {media_id}",
            surface="image_ref_include",
            screenshot_name="image_ref_include_missing.png",
            dialog_timeout_s=dialog_timeout_s,
        )
        return True

    @staticmethod
    async def _attach_selected_tile(
        page: Page,
        tile: Locator,
        *,
        out_dir: Path | None,
        detail: str,
        surface: str,
        screenshot_name: str,
        dialog_timeout_s: float,
    ) -> None:
        """Attach an already-located picker ``tile`` and confirm the dialog closed.

        The image reference picker attaches on tile-click and auto-closes the
        dialog (one step); the video r2v picker instead needs an explicit
        "Add to Prompt" include after selecting. Handle both: click, and if the
        dialog did not auto-close, resolve the locale-safe include button and
        click it, then verify the dialog closed. Shared by the media-UUID
        (:meth:`_select_existing_asset`) and filename
        (:meth:`_try_select_existing_by_filename`) selection paths. Raises
        ``TransportTimeoutError`` if the dialog never closes after the include —
        a tile matched but the attach did not register."""
        await tile.click()
        await page.wait_for_timeout(400)
        dialog = page.locator(DIALOG_ANY).last
        try:
            await dialog.wait_for(state="hidden", timeout=2500)
            return
        except Exception:  # noqa: BLE001 - still open -> needs explicit include
            pass

        include = await VideoGenerationMixin._resolve_include_action(
            page,
            PICKER_INCLUDE_BUTTON,
            _INCLUDE_BUTTON_TIER_NAMES,
            surface=surface,
            detail=detail,
            out_dir=out_dir,
            screenshot_name=screenshot_name,
        )
        await include.click(timeout=3000)
        await page.wait_for_timeout(600)
        try:
            await dialog.wait_for(state="hidden", timeout=dialog_timeout_s * 1000)
        except Exception as e:
            raise TransportTimeoutError(
                f"{detail} picker dialog did not close after {dialog_timeout_s}s "
                "(the include action may not have registered)",
            ) from e

    @staticmethod
    async def _attach_image_uuid_refs(
        page: Page,
        refs: list[tuple[str, str, str, str]],
        *,
        out_dir: Path | None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        """Attach pre-generated image UUID references for I2I.

        Each ref is ``(media_id, display_name, local_path, local_sha256)``. Prefers selecting
        the already-existing Flow asset in the picker (no duplicate upload —
        the founder principle); uploads ``local_path`` only as a fallback when the
        asset can't be located in place. Raises when neither is possible.
        """
        for media_id, display_name, local_path, local_sha256 in refs:
            add = page.locator(ADD_MEDIA_BUTTON).first
            await add.wait_for(state="visible", timeout=8000)
            await add.click()
            await page.wait_for_timeout(800)

            # #287 (live-confirmed): the picker's library view is per-project
            # — align it to the target project BEFORE this ref's lookup
            # (re-checked per dialog open; the switch itself no-ops when
            # already aligned or when the selector is absent).
            await VideoGenerationMixin._sync_picker_project(page, out_dir=out_dir)

            selected = await VideoGenerationMixin._select_existing_asset(
                page, media_id, display_name, out_dir=out_dir, name_resolver=name_resolver
            )
            if selected:
                log.info(
                    "ui_automation_video.image_ref_selected_existing",
                    media_id=media_id,
                    resolved_by="display_name",
                )
                continue

            if local_path:
                path = Path(local_path)
                if not matches_recorded_file(path, sha256=local_sha256):
                    raise TransportTimeoutError(
                        f"image ref {media_id!r} local fallback changed since it was "
                        "recorded; refusing to upload different bytes."
                    )
                log.info(
                    "ui_automation_video.image_ref_upload_fallback",
                    media_id=media_id,
                    resolved_by="upload",
                )
                await VideoGenerationMixin._upload_via_open_dialog(
                    page, path, log_label="image_ref", out_dir=out_dir
                )
                continue

            raise TransportTimeoutError(
                f"image ref {media_id!r} could not be selected in the picker and "
                "has no local file to upload — re-generate it or pass a local path.",
            )

    @staticmethod
    async def _resolve_include_action(
        page: Page,
        tiers: tuple[str, ...],
        tier_names: tuple[str, ...],
        *,
        surface: str,
        detail: str,
        out_dir: Path | None,
        screenshot_name: str,
    ) -> Locator:
        """Probe the include-action selector tiers IN ORDER; return the first
        tier that matches exactly ONE element, visible.

        Issue #170: a single localized has-text selector broke every
        non-Portuguese account. Tiers are probed sequentially (not flattened
        into one comma list — comma lists resolve in DOM order, not tier
        priority) and the matched tier is logged. A tier matching several
        elements is a miss, never a click on `.first` (#1273): the structural
        include tier means "the lone iconless button", and two of them means
        Flow changed the dialog.

        On exhaustion: capture a screenshot, press Escape twice (context menu
        + picker dialog — a Page must never return to the pool with an open
        overlay), and raise a typed, locale-neutral
        :class:`TransportTimeoutError` so the CLI surfaces the remediation
        hint with exit 9 instead of the privacy-hashed 'Unexpected error.'.
        """
        last_exc: Exception | None = None
        ambiguous = 0
        for tier, selector in zip(tier_names, tiers, strict=True):
            # Hidden nodes are not candidates: Flow keeps closed dialogs mounted.
            matches = page.locator(selector).filter(visible=True)
            loc = matches.first
            try:
                await loc.wait_for(state="visible", timeout=4000)
            except Exception as e:  # noqa: BLE001 — try the next tier
                last_exc = e
                continue
            count = await matches.count()
            if count != 1:
                log.warning(
                    "ui_automation_video.include_selector_ambiguous",
                    surface=surface,
                    tier=tier,
                    count=count,
                )
                ambiguous = max(ambiguous, count)
                continue
            log.info(
                "ui_automation_video.include_selector_tier",
                surface=surface,
                tier=tier,
            )
            return loc
        shot = await _capture_debug_screenshot(page, out_dir, screenshot_name)
        await page.keyboard.press("Escape")
        await page.keyboard.press("Escape")
        what = f"was ambiguous: {ambiguous} matches" if ambiguous else "did not appear"
        raise TransportTimeoutError(
            f"{detail} include action {what} (no selector tier "
            f"matched exactly one element on surface {surface!r}).{screenshot_clause(shot)}",
            remediation_hint=(
                "Flow's resource picker may have changed. "
                "Report the visible menu/button captions (plus the screenshot) "
                "at https://github.com/ffroliva/gflow-cli/issues."
            ),
        ) from last_exc

    @staticmethod
    async def _attach_character_entities(
        page: Page,
        entities: list[tuple[str, str]],
        *,
        out_dir: Path | None,
    ) -> None:
        """R2V: attach each character as a `referenceEntity` via the resource
        picker's Personagens tab.

        Mechanism (verified credit-free via route-abort payload capture,
        2026-06-06): open 'Add Media' -> Personagens tab -> RIGHT-CLICK the entity
        tile -> the context-menu include action (`add`-ligature menu item; the
        caption is localized, e.g. 'Incluir no comando' on pt-BR). This stages
        `referenceEntities:[{entityId}]` on the submit payload. A LEFT-click on a
        Tudo-tab tile + the inline include button instead stages a plain
        `referenceImage` (the character thumbnail) — which the submit backstop
        (`_assert_entities_attached`) correctly rejects. A plain left-click on the
        Personagens tile navigates into the character editor (it is an
        `<a href=.../character/...>`), hence the right-click.

        `entities` is a list of `(entity_id, display_name)` pairs. Tiles are
        addressed by entity id (`data-tile-id="fe_id_<entityId>"`), so selection
        is unambiguous even when several characters share a display name.
        """
        for entity_id, name in entities:
            add = page.locator(ADD_MEDIA_BUTTON).first
            await add.wait_for(state="visible", timeout=8000)
            await add.click()
            await page.wait_for_timeout(800)
            ptab = page.locator(PICKER_PERSONAGENS_TAB).first
            await ptab.wait_for(state="visible", timeout=8000)
            await ptab.click(force=True)
            await page.wait_for_timeout(700)
            tile = await VideoGenerationMixin._find_picker_entity_tile(page, entity_id)
            await tile.wait_for(state="visible", timeout=8000)
            await tile.scroll_into_view_if_needed(timeout=8000)
            await tile.click(button="right")
            await page.wait_for_timeout(400)
            include = await VideoGenerationMixin._resolve_include_action(
                page,
                PICKER_CONTEXT_INCLUDE,
                _CONTEXT_INCLUDE_TIER_NAMES,
                surface="context_menu",
                detail=f"character {name!r} ({entity_id})",
                out_dir=out_dir,
                screenshot_name="debug_entity_ctx_menu.png",
            )
            await include.click()
            await page.wait_for_timeout(600)
            log.info(
                "ui_automation_video.character_entity_attached",
                name=name,
                entity_id=entity_id,
            )

    @staticmethod
    async def _attach_reference_audio(
        page: Page,
        voice_id: str,
        *,
        out_dir: Path | None,
    ) -> None:
        """R2V: attach a voice resource via the Vozes picker -> include button."""
        add = page.locator(ADD_MEDIA_BUTTON).first
        await add.wait_for(state="visible", timeout=8000)
        await add.click()
        await page.wait_for_timeout(800)
        await page.locator(PICKER_VOZES_TAB).first.click()
        await page.wait_for_timeout(400)
        await page.locator(PICKER_SEARCH_INPUT).first.fill(voice_id)
        await page.wait_for_timeout(600)
        # Role+name match (apostrophe-safe, mirroring _remote_option_tile) — a
        # voice_id with a quote would break a single-quoted :has-text() selector.
        tile = (
            page.get_by_role("option", name=voice_id)
            .or_(page.get_by_role("button", name=voice_id))
            .first
        )
        await tile.click()
        await page.wait_for_timeout(300)
        include = await VideoGenerationMixin._resolve_include_action(
            page,
            PICKER_INCLUDE_BUTTON,
            _INCLUDE_BUTTON_TIER_NAMES,
            surface="vozes_button",
            detail=f"voice {voice_id!r}",
            out_dir=out_dir,
            screenshot_name="debug_voice_include.png",
        )
        await include.click()
        await page.wait_for_timeout(600)
        log.info("ui_automation_video.reference_audio_attached", voice=voice_id)
