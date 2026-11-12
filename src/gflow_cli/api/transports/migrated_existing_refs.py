"""Referencing an image already in the project, on flow.google.com (#913, #546).

Moved out of ``migrated_composer.py`` (#1249 ratchet) so the #546 page-side heal could land
without growing that file; ``migrated_composer`` imports these under their old names.
"""

from __future__ import annotations

import asyncio
import unicodedata
from dataclasses import replace
from typing import TYPE_CHECKING

import structlog

from gflow_cli.errors import ReferenceNotFoundError

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.image import ImageRef
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

log = structlog.get_logger(__name__)

#: Flow writes captions with its own model; they reach the composer by typing.
_CAPTION_MAX = 120


def require_appended(before: list[dict[str, str]], chips: list[dict[str, str]]) -> None:
    """Refuse a mention that did not land after every chip already in the prompt (#997).

    A count cannot see a chip inserted mid-prompt; the earlier chips staying a prefix can.
    shortcut: chips that read identically (same caption and kind) cannot be told apart.
    Retryability is the class default, unmeasured for this condition (Bug Lane).
    """
    if chips[: len(before)] != before:
        raise ReferenceNotFoundError(
            detail=(
                f"migrated host: a reference attached out of order — it did not land after "
                f"the {len(before)} reference(s) already in the prompt, so the prompt is no "
                "longer the one requested. Refusing to submit"
            ),
            # The chip WAS found, so the class default ("not in the picker") would mislead.
            remediation_hint=(
                "The reference was found but attached out of order, so nothing was "
                "submitted. Re-run the command; if it repeats, report it with the "
                "number of references and your window size."
            ),
        )


def picker_query(ref: ImageRef) -> str:
    """The search text for an existing image: its Flow caption, only if safe to type.

    A newline would press Enter mid-query and ``@`` opens a nested mention. Rather than
    search a string other than the caption, refuse (SCENARIO #18, #19).
    """
    caption = ref.display_name
    if (
        not caption.strip()
        or "@" in caption
        # Control, format (zero-width, direction marks) and line/paragraph separators.
        or any(unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} for ch in caption)
        or len(caption) > _CAPTION_MAX
    ):
        raise ReferenceNotFoundError(
            detail=(
                f"migrated host: image {ref.name} has no caption that can be searched "
                f"safely ({len(caption)} chars), so it cannot be referenced in place"
            ),
        )
    return caption.strip()


async def heal_catalog(
    name_resolver: object, refs: tuple[ImageRef, ...], stale: dict[str, str]
) -> None:
    """Tell the catalog the captions this run found on the page (#546).

    The CLI's resolver carries ``heal(media_id, name)`` (``cli_image.RefreshResolver``),
    which writes with ``sync.source == "refresh"`` and honours ``history_prompts``. It is
    sync and opens the store, so it runs on a worker thread. A failed write never fails a
    run that attached: it is logged by media id only.
    """
    heal = getattr(name_resolver, "heal", None)
    if not callable(heal):
        return
    for ref in refs:
        if ref.display_name == stale.get(ref.name, ref.display_name):
            continue
        try:
            await asyncio.to_thread(heal, ref.name, ref.display_name)
        except Exception:  # noqa: BLE001 - the catalog write must never kill the generation
            log.warning("migrated.caption_heal_failed", media_id=ref.name)


async def refreshed_from_picker(
    composer: MigratedComposer, page: Page, refs: tuple[ImageRef, ...]
) -> tuple[ImageRef, ...]:
    """``refs`` with each caption replaced by the one the page shows now (#546).

    Measured (``docs/superpowers/spikes/2026-11-12-migrated-listing-captions.md``): with
    nothing typed, the ``@`` picker lists the project's first page of assets, and an
    option's title is its caption; the option is this image when its ``/asb/`` token is
    the grid tile's. A reference the page does not list keeps its caption, and the
    caller's reload budget decides what happens next.
    """
    from gflow_cli.api.transports import migrated_composer as mc  # noqa: PLC0415 - cycle

    grid = {
        ref.name: str(
            await page.evaluate(
                mc._GRID_TOKEN_JS,  # pyright: ignore[reportPrivateUsage]
                ref.name,
            )
            or ""
        )
        for ref in refs
    }
    await composer._caret_to_end(page)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    await page.keyboard.type("@", delay=120)
    await page.wait_for_timeout(2200)
    js = mc._OPTION_TOKENS_JS  # pyright: ignore[reportPrivateUsage]
    tokens = [str(t) for t in await page.evaluate(js, mc.PICKER_OPTION)]
    js = mc._OPTION_TITLES_JS  # pyright: ignore[reportPrivateUsage]
    titles = [str(t) for t in await page.evaluate(js, mc.PICKER_OPTION)]
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(800)
    captions = dict(zip(tokens, titles, strict=False))
    fresh: list[ImageRef] = []
    for ref in refs:
        name = captions.get(grid[ref.name], "").strip() if grid[ref.name] else ""
        if name and name != ref.display_name:
            log.info("migrated.existing_reference_renamed", media_id=ref.name)
            fresh.append(replace(ref, display_name=name))
        else:
            fresh.append(ref)
    return tuple(fresh)
