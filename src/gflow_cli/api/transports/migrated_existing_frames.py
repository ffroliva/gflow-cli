"""Bind in-project image UUIDs onto i2v Start/End frame chips on flow.google.com (#1542).

Mirrors ``migrated_existing_refs.py`` (#913/#995) for the Frames picker:
1. Resolve each requested UUID's ``/asb/<token>`` from the project grid BEFORE
   ``apply_video_settings()`` runs, reloading up to :data:`EXISTING_REF_WAIT_S` if a
   newly generated tile has not appeared in the grid yet.
2. Open the Start (or End) Frames picker, filter with the sanitized catalog caption
   (stripping trailing ellipsis ``…``), fall back to the unfiltered picker list via
   ``search.fill("")`` if the caption went stale, and click the exact option whose
   ``<img src>`` carries the grid tile's ``/asb/<token>``.
3. Confirm the picker closes (including #792's confirm cohort) and ``BOUND_CHIP`` count
   reaches ``expect_bound_chips`` without ever echoing raw prompt-derived captions in
   error messages.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from typing import TYPE_CHECKING, Any

import structlog

from gflow_cli.api.transports._mc_common import (
    _GRID_TOKEN_JS,  # pyright: ignore[reportPrivateUsage]
    EXISTING_REF_RELOAD_PAUSE_S,
    EXISTING_REF_WAIT_S,
    FRAME_SEARCH_ATTEMPTS,
    FRAME_SEARCH_RETRY_PAUSE_S,
    PICKER,
    PICKER_OPTION,
    PICKER_SEARCH,
)
from gflow_cli.api.video import GenerateVideoRequest, Mode
from gflow_cli.errors import ReferenceNotFoundError

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.transports.migrated_composer import MigratedComposer

log = structlog.get_logger(__name__)

#: Maximum caption length accepted for picker typing (mirrors ``migrated_existing_refs``).
_FRAME_CAPTION_MAX = 120
#: How long a Frames picker query gets to settle ``UpteDb`` options before fallback.
FRAME_PICKER_SETTLE_S = 3.0
#: Poll cadence while waiting for a filtered/unfiltered option to carry the target token.
FRAME_PICKER_POLL_S = 0.25
#: Valid `/asb/<token>` characters (mirrors `_GRID_TOKEN_JS` / `_FRAME_PICKER_OPTION_TOKENS_JS`).
_ASB_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")

#: Extract ``/asb/<token>`` for each option inside the LIVE (last) picker popover, so an
#: earlier detached Angular overlay pane cannot skew option indices.
_FRAME_PICKER_OPTION_TOKENS_JS = (
    "([pickerSel, optSel]) => {"
    " const pickers = document.querySelectorAll(pickerSel);"
    " const root = pickers.length ? pickers[pickers.length - 1] : document;"
    " return [...root.querySelectorAll(optSel)].map((o) => {"
    "  const img = o.querySelector('img');"
    "  const m = ((img && img.getAttribute('src')) || '').match(/\\/asb\\/([A-Za-z0-9_-]+)/);"
    "  return m ? m[1] : '';"
    " }); }"
)


def frame_search_query(display_name: str) -> str:
    """Return a safe search string for the Frames picker input, or ``""`` to skip typing.

    Trailing ``…`` / ``.`` characters from Flow's truncated tile captions are stripped.
    Overlong captions or those containing control, format, or line-separator characters
    return ``""`` so the caller matches directly in the unfiltered picker list.
    """
    if not display_name or len(display_name) > _FRAME_CAPTION_MAX:
        return ""
    if any(unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} for ch in display_name):
        return ""
    return display_name.strip().rstrip("…. ").strip()


def existing_frame_ref_ids(request: GenerateVideoRequest) -> tuple[str, ...]:
    """Deduplicated in-project image UUID frame IDs requested by an i2v request."""
    if request.mode is not Mode.I2V:
        return ()
    return tuple(
        dict.fromkeys(mid for mid in (request.start_image_ref_id, request.end_image_ref_id) if mid)
    )


async def await_existing_frame_tokens(
    composer: MigratedComposer,
    page: Page,
    project_id: str,
    media_ids: tuple[str, ...],
) -> dict[str, str]:
    """Read each frame UUID's ``/asb/<token>`` from the project grid, reloading if needed.

    Must run BEFORE :meth:`MigratedComposer.apply_video_settings` so any reload for a
    just-generated tile cannot wipe selected video settings.
    """
    from gflow_cli.api.transports import agent_only_composer as agent  # noqa: PLC0415 - cycle

    unique_ids = tuple(dict.fromkeys(media_ids))
    if not unique_ids:
        return {}
    deadline = time.monotonic() + EXISTING_REF_WAIT_S
    missing: list[str] = list(unique_ids)
    while True:
        tokens = {mid: str(await page.evaluate(_GRID_TOKEN_JS, mid) or "") for mid in unique_ids}
        missing = [mid for mid, tok in tokens.items() if not tok]
        if not missing:
            return tokens
        if time.monotonic() >= deadline:
            break
        log.info("migrated.existing_frame_grid_wait", missing=missing)
        await asyncio.sleep(EXISTING_REF_RELOAD_PAUSE_S)
        await page.reload(wait_until="domcontentloaded", timeout=45_000)
        if await composer.ensure_editor(page, project_id) == "agent_only":
            raise agent.refuse_unported("image-to-video")
    raise ReferenceNotFoundError(
        detail=(
            f"migrated host: frame image(s) {', '.join(missing)} did not appear in the "
            f"project grid within {EXISTING_REF_WAIT_S:.0f}s (host=migrated)"
        ),
    )


async def _find_token_index(page: Page, token: str) -> tuple[int | None, int]:
    """Poll the open Frames picker until an option carries ``token``, or settle expires."""
    deadline = time.monotonic() + FRAME_PICKER_SETTLE_S
    offered = 0
    while True:
        raw = await page.evaluate(_FRAME_PICKER_OPTION_TOKENS_JS, [PICKER, PICKER_OPTION])
        tokens = [str(t or "") for t in (raw or ())]
        offered = len(tokens)
        if token in tokens:
            return tokens.index(token), offered
        if time.monotonic() >= deadline:
            return None, offered
        await asyncio.sleep(FRAME_PICKER_POLL_S)


async def _locate_option_in_open_picker(
    page: Page,
    picker: Any,
    query: str,
    token: str,
) -> tuple[int | None, int]:
    """Search the open picker by ``query``; on a miss, clear with ``fill("")`` and retry."""
    search = picker.locator(PICKER_SEARCH).first
    if query:
        await search.click(timeout=4000)
        await page.keyboard.insert_text(query)
        idx, offered = await _find_token_index(page, token)
        if idx is not None:
            return idx, offered
        # Stale/renamed caption fallback (2026-11-28 spike Q4): clearing Angular's input
        # restores the unfiltered project asset list client-side with zero extra RPCs.
        await search.fill("")
    return await _find_token_index(page, token)


async def attach_existing_frame(  # noqa: PLR0913
    composer: MigratedComposer,
    page: Page,
    *,
    media_id: str,
    display_name: str,
    token: str,
    chip_label: str = "Start",
    expect_bound_chips: int = 1,
) -> str:
    """Bind an already-in-project image onto a Frames chip by exact ``/asb/<token>``."""
    query = frame_search_query(display_name)
    offered = 0
    for attempt in range(1, FRAME_SEARCH_ATTEMPTS + 1):
        picker = await composer._open_frame_picker(page, chip_label)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        idx, offered = await _locate_option_in_open_picker(page, picker, query, token)
        if idx is not None:
            # Anchor the click on the exact `/asb/<token>` attribute rather than a positional
            # index so an in-flight `UpteDb` re-render shrinking the option list cannot shift
            # the target between `evaluate` and `click`.
            option = (
                picker.locator(f"{PICKER_OPTION}:has(img[src*='/asb/{token}'])").first
                if _ASB_TOKEN_RE.fullmatch(token)
                else picker.locator(PICKER_OPTION).nth(idx)
            )
            await option.click(timeout=4000)
            await composer._confirm_frame_bound(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                page,
                media_id,
                label=f"media {media_id}",
                chip_label=chip_label,
                expect_bound_chips=expect_bound_chips,
                event_name="migrated.existing_frame_bound",
            )
            return media_id
        await page.keyboard.press("Escape")
        if attempt < FRAME_SEARCH_ATTEMPTS:
            log.info(
                "migrated.existing_frame_search_miss",
                attempt=attempt,
                media_id=media_id,
                chip_label=chip_label,
                offered=offered,
            )
            await asyncio.sleep(FRAME_SEARCH_RETRY_PAUSE_S)
    raise ReferenceNotFoundError(
        detail=(
            f"migrated host: the {chip_label} frame picker offered {offered} option(s) across "
            f"{FRAME_SEARCH_ATTEMPTS} attempt(s), none matching media {media_id} (host=migrated)"
        ),
    )


async def attach_video_frames(
    composer: MigratedComposer,
    page: Page,
    project_id: str,
    request: GenerateVideoRequest,
    frame_tokens: dict[str, str],
) -> tuple[str | None, str | None]:
    """Attach Start (and optional End) i2v frames, local or in-project UUID (#1542)."""
    if request.mode is not Mode.I2V:
        return None, None
    media_id: str | None = None
    if request.start_image is not None:
        media_id = await composer.attach_start_frame(page, project_id, request.start_image)
    elif request.start_image_ref_id is not None:
        media_id = await attach_existing_frame(
            composer,
            page,
            media_id=request.start_image_ref_id,
            display_name=request.start_image_ref_display_name,
            token=frame_tokens[request.start_image_ref_id],
            chip_label="Start",
            expect_bound_chips=1,
        )
    end_media_id: str | None = None
    if request.end_image is not None:
        end_media_id = await composer.attach_end_frame(page, project_id, request.end_image)
    elif request.end_image_ref_id is not None:
        end_media_id = await attach_existing_frame(
            composer,
            page,
            media_id=request.end_image_ref_id,
            display_name=request.end_image_ref_display_name,
            token=frame_tokens[request.end_image_ref_id],
            chip_label="End",
            expect_bound_chips=2,
        )
    return media_id, end_media_id
