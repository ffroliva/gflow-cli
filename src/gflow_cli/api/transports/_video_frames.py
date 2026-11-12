"""Start and end frame attach.

A part of `VideoGenerationMixin`, moved byte-identical from
`ui_automation_video.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api.transports._video_common import (
    FRAME_SLOT_BY_LABEL,
    FRAME_SLOTS_STRUCT,
    _capture_debug_screenshot,
    screenshot_clause,
)
from gflow_cli.api.transports._video_references import ReferenceAttachMixin
from gflow_cli.errors import TransportTimeoutError
from gflow_cli.file_integrity import matches_recorded_file

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

    from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

log = structlog.get_logger(__name__)


class FrameAttachMixin(ReferenceAttachMixin):
    """Start and end frame attach."""

    @staticmethod
    async def _attach_frame(
        page: Page,
        slot_index: int,
        label: str,
        image: Path,
        *,
        out_dir: Path | None,
        timeout_s: float = 120.0,
    ) -> None:
        """Fill the I2V first/last frame slot (`slot_index` 0=first, 1=last) with
        `image` through Flow's media dialog (the ONLY way that binds the image to
        the slot — set_input_files on the generic input only adds it to the
        library, see the UPLOAD_IMAGE_ROUTE note). Sequence: click slot ->
        'Upload media' (file chooser) -> wait uploadImage -> commit. Must run with
        the settings panel CLOSED (the slots live in the main editor). `label` is
        for logging only. Path existence is validated here (the boundary)."""
        if not image.exists():
            msg = f"frame image not found: {image}"
            raise FileNotFoundError(msg)

        # wait_ms is short (1500) because _wait_video_editor_ready already
        # guaranteed the editor SPA is mounted; the frame panel resolves in
        # <10 ms on a pre-rendered page, so a future swap_horiz rename surfaces
        # as a fast, clear error instead of an 8-second dead wait per call.
        slot = await VideoGenerationMixin._resolve_frame_slot(
            page, slot_index, label, out_dir=out_dir, wait_ms=1500
        )
        await slot.click()
        await page.wait_for_timeout(1000)  # media dialog opens
        await VideoGenerationMixin._upload_via_open_dialog(
            page,
            image,
            log_label=label,
            out_dir=out_dir,
            timeout_s=timeout_s,
        )
        log.info("ui_automation_video.frame_attached", slot=label)

    @staticmethod
    async def _resolve_frame_slot(
        page: Page,
        slot_index: int,
        label: str,
        *,
        out_dir: Path | None,
        wait_ms: int,
    ) -> Locator:
        """Locate an I2V frame slot, structural-first with a text-label fallback.

        Shared by the local-upload and remote-ref frame-attach paths. The frame
        slots are the dialog-divs inside the swap_horiz container, indexed by
        position (0=start, 1=end); FRAME_SLOT_BY_LABEL (has-text 'Start'/'End',
        English-only) is the fallback when the structural count is insufficient.
        Once an image binds, its slot leaves the structural pattern, so the next
        unfilled slot is `.first` of the remaining matches regardless of index.
        Returns the (unclicked) slot locator; raises RuntimeError with a debug
        screenshot if none resolves.
        """
        structs = page.locator(FRAME_SLOTS_STRUCT)
        try:
            await structs.first.wait_for(state="visible", timeout=wait_ms)
        except Exception as e:
            shot = await _capture_debug_screenshot(
                page, out_dir, f"debug_no_{label.lower()}_slot.png"
            )
            msg = f"frame slot {label!r} not found on the Flow editor.{screenshot_clause(shot)}"
            raise RuntimeError(msg) from e

        struct_count = await structs.count()
        if struct_count > slot_index:
            return structs.nth(slot_index)
        if struct_count > 0:
            return structs.first
        slot = page.locator(FRAME_SLOT_BY_LABEL.format(label=label)).first
        try:
            await slot.wait_for(state="visible", timeout=3000)
        except Exception as e:
            msg = (
                f"frame slot index {slot_index} ({label!r}) not present "
                f"(found {struct_count} structural slot(s), "
                f"text-label fallback also missed)"
            )
            raise RuntimeError(msg) from e
        return slot

    @staticmethod
    async def _attach_remote_frame(
        page: Page,
        slot_index: int,
        label: str,
        name: str,
        *,
        out_dir: Path | None,
        timeout_s: float = 120.0,
    ) -> None:
        """Attach a remote image (by display name) into an I2V frame slot."""
        slot = await VideoGenerationMixin._resolve_frame_slot(
            page, slot_index, label, out_dir=out_dir, wait_ms=12000
        )
        await slot.click()
        await page.wait_for_timeout(1000)  # media dialog opens

        await VideoGenerationMixin._pick_option_and_include(
            page,
            name,
            surface="remote_frame_include",
            detail=f"{label} remote frame",
            out_dir=out_dir,
            dialog_timeout_s=timeout_s,
        )
        log.info("ui_automation_video.remote_frame_attached", slot=label, display_name=name)

    @staticmethod
    async def _attach_frame_by_media_id(
        page: Page,
        slot_index: int,
        label: str,
        media_id: str,
        display_name: str,
        *,
        out_dir: Path | None,
        project_name: str | None = None,
        local_path: Path | None = None,
        local_sha256: str = "",
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        """Fill the I2V first/last frame slot with an already-existing in-project
        asset without a duplicate upload. The catalog-resolved ``display_name``
        filters the browser picker; ``media_id`` verifies the exact result tile.
        An empty legacy/programmatic name permits only the exact rendered
        viewport tile. If name lookup misses, a catalog-recorded local copy can
        upload the exact asset bytes instead of scanning or scrolling."""
        slot = await VideoGenerationMixin._resolve_frame_slot(
            page, slot_index, label, out_dir=out_dir, wait_ms=12000
        )
        await slot.click()
        await page.wait_for_timeout(1000)  # media dialog opens

        # #287 (live-confirmed): the picker's library view is per-project —
        # align it to the target project BEFORE any lookup. `project_name` is
        # the user's --project-name override for the menu's name-based match.
        await VideoGenerationMixin._sync_picker_project(
            page, out_dir=out_dir, project_name=project_name
        )

        selected = await VideoGenerationMixin._select_existing_asset(
            page, media_id, display_name, out_dir=out_dir, name_resolver=name_resolver
        )
        if not selected:
            if local_path is not None:
                if not matches_recorded_file(local_path, sha256=local_sha256):
                    raise TransportTimeoutError(
                        f"{label} frame asset {media_id!r} local fallback changed "
                        "since it was recorded; refusing to upload different bytes."
                    )
                log.info(
                    "ui_automation_video.frame_ref_upload_fallback",
                    slot=label,
                    media_id=media_id,
                    resolved_by="upload",
                )
                await VideoGenerationMixin._upload_via_open_dialog(
                    page,
                    local_path,
                    log_label=f"{label}_frame_ref",
                    out_dir=out_dir,
                )
                return
            # The frame-slot dialog is the one surface this picker reuse is
            # unproven on (#237's name-search never surfaced generated media
            # here) — capture the dialog state so a live miss is diagnosable.
            shot = await _capture_debug_screenshot(
                page, out_dir, f"debug_frame_ref_miss_{label.lower()}.png"
            )
            msg = (
                f"{label} frame asset {media_id!r} could not be located in the "
                "media picker by its catalog display name — is it in the target "
                "project (missing or wrong --project), and was it recorded by "
                "this profile?"
            )
            if shot is not None:
                msg = f"{msg} Screenshot: {shot}"
            raise TransportTimeoutError(msg)
        log.info(
            "ui_automation_video.frame_ref_attached",
            slot=label,
            media_id=media_id,
            resolved_by="display_name",
        )
