"""The asset picker's project sync and grid scrolling.

A part of `VideoGenerationMixin`, moved byte-identical from
`ui_automation_video.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from gflow_cli.api.transports._common import extract_project_id
from gflow_cli.api.transports._video_common import (
    _FLOW_BRANDING_TITLES,
    _PICKER_GRID_SCROLL_JS,
    _PICKER_GRID_TILE_IDS_JS,
    _PICKER_PROJECT_MENU_CHILD_COUNT_JS,
    _PICKER_PROJECT_MENU_DUMP_JS,
    _PICKER_PROJECT_MENU_SCROLL_JS,
    _PICKER_PROJECT_OPTION_MATCH_JS,
    _PICKER_PROJECT_TRIGGER_ACTIVE_JS,
    _PROJECT_NAME_FROM_PAGE_JS,
    DIALOG_ANY,
    PICKER_GRID_SCROLL_ATTEMPTS,
    PICKER_GRID_SCROLL_DELTA_PX,
    PICKER_GRID_SCROLL_MAX_ATTEMPTS,
    PICKER_GRID_SCROLL_STALL_LIMIT,
    PICKER_PROJECT_MENU_OPEN,
    PICKER_PROJECT_MENU_POLL_MS,
    PICKER_PROJECT_MENU_POLLS,
    PICKER_PROJECT_MENU_SCROLL_DELTA_PX,
    PICKER_PROJECT_MENU_SCROLL_MAX,
    PICKER_PROJECT_MENU_SCROLL_SETTLE_MS,
    PICKER_PROJECT_SELECTOR_TRIGGERS,
    PICKER_SEARCH_INPUT,
    _write_project_menu_dump,
)
from gflow_cli.api.transports._video_settings import VideoSettingsMixin

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

    from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin

log = structlog.get_logger(__name__)


class PickerProjectMixin(VideoSettingsMixin):
    """The asset picker's project sync and grid scrolling."""

    @staticmethod
    async def _tile_is_fully_visible(tile: Locator) -> bool:
        """Use the browser's intersection engine to prevent implicit click scrolling."""
        try:
            return bool(
                await tile.evaluate(
                    """(el) => new Promise(resolve => {
                      const observer = new IntersectionObserver(([entry]) => {
                        observer.disconnect();
                        resolve(entry.intersectionRatio === 1);
                      }, {threshold: 1});
                      observer.observe(el);
                    })"""
                )
            )
        except Exception:  # noqa: BLE001 - stale/vanished tile is not selectable
            return False

    @staticmethod
    async def _sync_picker_project(
        page: Page, *, out_dir: Path | None = None, project_name: str | None = None
    ) -> None:
        """#287 CONFIRMED (live round 2): the media picker's library view has
        its OWN active project — ``--project`` only navigates the EDITOR — so
        the picker can open on a different project, making the target asset
        unreachable no matter how deep the grid is scrolled. Derives the
        target project from the editor URL (``--project`` navigation put it
        there), resolves its display NAME (the picker menu lists projects by
        NAME, not id — round 4 dump), and aligns the picker's project
        selector. ``project_name`` is the user-supplied override
        (``--project-name`` / ``GFLOW_CLI_PROJECT_NAME``) and takes precedence
        over page-derived resolution. No-op when the URL carries no project id
        or the picker has no project selector (older cohort)."""
        # cast + isinstance: a unit-test fake's page.url may not be a str.
        page_url = cast("object", page.url)
        project_id = extract_project_id(page_url) if isinstance(page_url, str) else None
        if not project_id:
            log.info(
                "ui_automation_video.picker_project_sync_skipped",
                reason="no_project_in_url",
            )
            return
        if project_name:
            log.info(
                "ui_automation_video.picker_project_name_override",
                project_id=project_id,
                name=project_name,
            )
            resolved_name: str | None = project_name
        else:
            resolved_name = await VideoGenerationMixin._resolve_project_name(page, project_id)
        await VideoGenerationMixin._ensure_picker_project(
            page, project_id, project_name=resolved_name, out_dir=out_dir
        )

    @staticmethod
    def _strip_flow_branding(title: str) -> str:
        """Strip Flow branding from a tab title, tolerantly (#287 round 5):
        try the suffix/prefix separator variants; fall back to the raw title.
        The raw title is logged on every resolution, so the real live pattern
        is learnable and this list can be tightened from evidence."""
        cleaned = title.strip()
        # 'Google Flow' first (longer, more specific) — the round-5 live run
        # observed 'Google Flow - <project name>'.
        for brand in ("Google Flow", "Flow"):
            for sep in (" - ", " – ", " — ", " | "):
                suffix = f"{sep}{brand}"
                if cleaned.endswith(suffix):
                    return cleaned[: -len(suffix)].strip()
                prefix = f"{brand}{sep}"
                if cleaned.startswith(prefix):
                    return cleaned[len(prefix) :].strip()
        return cleaned

    @staticmethod
    async def _resolve_project_name(page: Page, project_id: str) -> str | None:
        """Resolve the target project's display NAME (#287 rounds 2/5): the
        picker's project menu lists projects by NAME (unnamed projects show
        only creation timestamps), and the CLI only knows the UUID. Tier 0:
        the editor tab title (document.title — the editor was navigated to
        /project/<id> before the picker opened), stripped of Flow branding
        and rejected when branding-only. Tier 1: an element whose href
        references the project id. Tier 2: a project-title-classed element.
        The ``--project-name`` override in `_sync_picker_project` beats all
        of this. The local catalog's ``projects.title`` was considered but
        the transport layer has no catalog access, and the live page also
        reflects renames and non-gflow projects. Best-effort: ``None`` when
        nothing usable is found."""
        raw: object = None
        try:
            raw = await page.evaluate(_PROJECT_NAME_FROM_PAGE_JS, project_id)
        except Exception:  # noqa: BLE001 - name resolution is best-effort
            raw = None
        raw_title = ""
        candidates: list[tuple[str, str]] = []
        if isinstance(raw, dict):
            data = cast("dict[str, object]", raw)
            raw_title = str(data.get("title") or "").strip()
            title_name = VideoGenerationMixin._strip_flow_branding(raw_title)
            if title_name and title_name.lower() not in _FLOW_BRANDING_TITLES:
                candidates.append(("title", title_name))
            for source_key, source in (("href_text", "href"), ("class_text", "class")):
                text = str(data.get(source_key) or "").strip()
                if text:
                    candidates.append((source, text))
        if candidates:
            source, name = candidates[0]
            log.info(
                "ui_automation_video.picker_project_name_resolved",
                project_id=project_id,
                name=name,
                source=source,
                raw_title=raw_title,
            )
            return name
        log.info(
            "ui_automation_video.picker_project_name_unresolved",
            project_id=project_id,
            raw_title=raw_title,
        )
        return None

    @staticmethod
    async def _wait_project_menu_open(page: Page) -> bool:
        """Whether the (portal-rendered) project menu is open — Radix stamps
        `data-state='open'` on the visible [role='menu'] (#287 round 2)."""
        menu = page.locator(PICKER_PROJECT_MENU_OPEN).last
        try:
            await menu.wait_for(state="visible", timeout=2000)
        except Exception:  # noqa: BLE001 - not-open is an expected branch
            return False
        return True

    @staticmethod
    async def _wait_project_menu_populated(page: Page) -> int:
        """Poll the OPEN portal menu for element children (#287 round 3: the
        open-state flips before the project list populates — matching or
        dumping too early sees an empty portal and is a guaranteed miss).
        Bounded at PICKER_PROJECT_MENU_POLLS x PICKER_PROJECT_MENU_POLL_MS
        (~3s); stops as soon as any elements render. Returns the final
        element count (0 = never populated / probe failed)."""
        elements = 0
        for _ in range(PICKER_PROJECT_MENU_POLLS):
            raw: object = None
            try:
                raw = await page.evaluate(_PICKER_PROJECT_MENU_CHILD_COUNT_JS)
            except Exception:  # noqa: BLE001 - poll probe is best-effort
                raw = None
            elements = int(raw) if isinstance(raw, (int, float)) else 0
            if elements > 0:
                break
            await page.wait_for_timeout(PICKER_PROJECT_MENU_POLL_MS)
        return elements

    @staticmethod
    async def _match_project_option(
        page: Page, match_args: dict[str, str | None]
    ) -> tuple[bool, str | None, int]:
        """One pass of the option-match JS over the open portal. Returns
        ``(clicked, matched_by, candidate_count)`` — best-effort, never
        raises (a failed probe is a miss)."""
        try:
            outcome_raw: object = await page.evaluate(_PICKER_PROJECT_OPTION_MATCH_JS, match_args)
        except Exception:  # noqa: BLE001 - dropdown scan is best-effort
            return False, None, 0
        if not isinstance(outcome_raw, dict):
            return False, None, 0
        outcome = cast("dict[str, object]", outcome_raw)
        matched_by_value = outcome.get("matched_by")
        candidates_value = outcome.get("candidates")
        return (
            bool(outcome.get("clicked")),
            str(matched_by_value) if matched_by_value else None,
            int(candidates_value) if isinstance(candidates_value, (int, float)) else 0,
        )

    @staticmethod
    async def _scroll_project_menu_and_match(
        page: Page, match_args: dict[str, str | None]
    ) -> tuple[bool, str | None, int]:
        """#287 round 5: the project menu is the full recency-ordered project
        list (80 items observed) — the target's entry can sit below the
        visible fold or outside a virtualised window. Scroll the open portal
        with the progress-bounded pattern: scroll one step, re-match, keep
        going while the rendered item set still changes, stall-terminate
        after ``PICKER_GRID_SCROLL_STALL_LIMIT`` no-progress scrolls, hard
        ceiling at ``PICKER_PROJECT_MENU_SCROLL_MAX``. Same return shape as
        `_match_project_option`."""
        stalls = 0
        previous: frozenset[str] | None = None
        attempts = 0
        reason = "ceiling"
        clicked = False
        matched_by: str | None = None
        candidates = 0
        while attempts < PICKER_PROJECT_MENU_SCROLL_MAX:
            raw: object = None
            try:
                raw = await page.evaluate(
                    _PICKER_PROJECT_MENU_SCROLL_JS, PICKER_PROJECT_MENU_SCROLL_DELTA_PX
                )
            except Exception:  # noqa: BLE001 - scroll probe is best-effort
                raw = None
            attempts += 1
            fingerprint: frozenset[str] | None = None
            if isinstance(raw, list) and raw:
                fingerprint = frozenset(str(item) for item in cast("list[object]", raw))
            await page.wait_for_timeout(PICKER_PROJECT_MENU_SCROLL_SETTLE_MS)
            clicked, matched_by, candidates = await VideoGenerationMixin._match_project_option(
                page, match_args
            )
            log.info(
                "ui_automation_video.picker_project_menu_scroll_probe",
                attempt=attempts,
                items=len(fingerprint) if fingerprint is not None else None,
                new_items=(
                    len(fingerprint - previous)
                    if fingerprint is not None and previous is not None
                    else None
                ),
            )
            if clicked:
                reason = "found"
                break
            if fingerprint is None:
                reason = "no_menu"
                break
            if fingerprint == previous:
                stalls += 1
                if stalls >= PICKER_GRID_SCROLL_STALL_LIMIT:
                    reason = "stall"
                    break
            else:
                stalls = 0
                previous = fingerprint
        log.info(
            "ui_automation_video.picker_project_menu_scroll_done",
            reason=reason,
            attempts=attempts,
            found=clicked,
        )
        return clicked, matched_by, candidates

    @staticmethod
    async def _ensure_picker_project(
        page: Page,
        project_id: str,
        *,
        project_name: str | None = None,
        out_dir: Path | None = None,
    ) -> bool | None:
        """Align the OPEN picker's library view to ``project_id`` (#287).

        Live round 2 confirmed the mechanics: the trigger is a Radix
        `ProjectDropdownSubTrigger` rendering the ACTIVE project's name; the
        submenu options render project NAMES in a portal. Sequence: probe the
        trigger cascade; consider the target active when the trigger matches
        by id or resolved name; otherwise open the submenu (click, then hover,
        then focus+ArrowRight — Radix SubTriggers may ignore plain click),
        verify `[role='menu'][data-state='open']`, poll for the portal to
        POPULATE (round 3: the open-state flips before the list renders), and
        click the innermost candidate matching by href-with-id, id-in-markup,
        then normalized name (round 3: the portal had ZERO classic menu-item
        ARIA roles, so generic clickables are swept). Returns ``None`` when
        the picker has no project selector (older cohort — pure no-op),
        ``True`` when the target project is (already) active, ``False`` when a
        selector exists but the target could not be selected — a miss dumps
        the open portal's raw bounded innerHTML (+ child count and tag
        histogram) to the out-dir, presses Escape (never leave an open
        overlay), and lets callers proceed: the asset lookup stays the
        authority."""
        trigger = None
        matched_selector: str | None = None
        for selector in PICKER_PROJECT_SELECTOR_TRIGGERS:
            candidate = page.locator(selector).first
            if await candidate.count():
                trigger = candidate
                matched_selector = selector
                break
        if trigger is None:
            log.info(
                "ui_automation_video.picker_project_selector_absent",
                project_id=project_id,
            )
            return None
        match_args = {"projectId": project_id, "projectName": project_name}
        already_active = False
        try:
            already_active = bool(
                await trigger.evaluate(_PICKER_PROJECT_TRIGGER_ACTIVE_JS, match_args)
            )
        except Exception:  # noqa: BLE001 - probe is best-effort; fall through to a switch
            already_active = False
        if already_active:
            log.info(
                "ui_automation_video.picker_project_already_active",
                project_id=project_id,
                selector=matched_selector,
            )
            return True

        # Open the Radix submenu: click, then hover, then keyboard — each
        # verified against the portal-rendered open-state menu (#287 round 2:
        # SubTriggers open on hover/ArrowRight; plain click may be a no-op).
        opened = False
        open_method = "none"
        await trigger.click()
        if await VideoGenerationMixin._wait_project_menu_open(page):
            opened, open_method = True, "click"
        if not opened:
            try:
                await trigger.hover(timeout=1000)
            except Exception:  # noqa: BLE001 - hover unsupported -> keyboard tier
                pass
            if await VideoGenerationMixin._wait_project_menu_open(page):
                opened, open_method = True, "hover"
        if not opened:
            try:
                await trigger.focus(timeout=1000)
                await page.keyboard.press("ArrowRight")
            except Exception:  # noqa: BLE001 - focus unsupported -> match attempt anyway
                pass
            if await VideoGenerationMixin._wait_project_menu_open(page):
                opened, open_method = True, "keyboard"
        log.info(
            "ui_automation_video.picker_project_menu_opened",
            project_id=project_id,
            opened=opened,
            method=open_method,
        )
        # #287 round 3: the portal populates AFTER the open-state flips —
        # matching an empty portal was a guaranteed miss. Poll (bounded)
        # before matching or dumping.
        menu_elements = await VideoGenerationMixin._wait_project_menu_populated(page)
        log.info(
            "ui_automation_video.picker_project_menu_populated",
            project_id=project_id,
            elements=menu_elements,
        )

        clicked, matched_by, candidates = await VideoGenerationMixin._match_project_option(
            page, match_args
        )
        if not clicked:
            # #287 round 5: the target's entry may be below the menu's fold —
            # scroll the open portal (progress-bounded) and re-match.
            (
                clicked,
                matched_by,
                candidates,
            ) = await VideoGenerationMixin._scroll_project_menu_and_match(page, match_args)
        if not clicked:
            # Raw open-portal dump (#287 round 4): role-filtered item lists
            # blinded us twice — capture bounded raw markup instead.
            portal: object = None
            try:
                portal = await page.evaluate(_PICKER_PROJECT_MENU_DUMP_JS)
            except Exception:  # noqa: BLE001 - dump probe is best-effort
                portal = None
            menu_dump = _write_project_menu_dump(
                out_dir,
                project_id,
                {
                    "project_id": project_id,
                    "project_name": project_name,
                    "menu_opened": opened,
                    "menu_elements": menu_elements,
                    "candidates": candidates,
                    "portal": portal,
                },
            )
            log.warning(
                "ui_automation_video.picker_project_switch_miss",
                project_id=project_id,
                project_name=project_name,
                selector=matched_selector,
                menu_opened=opened,
                menu_elements=menu_elements,
                candidates=candidates,
                menu_dump=str(menu_dump) if menu_dump is not None else None,
                note="no portal candidate matched the target project by href, id, or name",
            )
            # Never leave an open overlay on the pooled Page.
            await page.keyboard.press("Escape")
            return False
        await page.wait_for_timeout(800)
        log.info(
            "ui_automation_video.picker_project_switched",
            project_id=project_id,
            selector=matched_selector,
            matched_by=matched_by,
        )
        return True

    @staticmethod
    async def _search_picker_for_tile(page: Page, tile: Locator, term: str) -> bool | None:
        """Type ``term`` into the picker search box and report whether it
        surfaced ``tile``. Clears any previous term first so repeated searches
        don't concatenate (``press_sequentially`` appends). Returns ``None``
        (without touching the page) when this picker variant has no search
        input at all (#174's full-page media-library drift) — search must
        never become a hard dependency."""
        search = page.locator(PICKER_SEARCH_INPUT).first
        try:
            await search.wait_for(state="visible", timeout=4000)
        except Exception:  # noqa: BLE001 - absent search is a supported picker cohort
            log.info(
                "ui_automation_video.picker_search_unavailable",
                term_length=len(term),
            )
            return None
        await search.fill("")
        # Human-like typing jitter to dodge WAF heuristics — not security.
        await search.press_sequentially(term, delay=random.randint(10, 50))  # NOSONAR  # noqa: S311 - human-pacing jitter, not cryptography
        await page.wait_for_timeout(800)
        rendered = await VideoGenerationMixin._picker_grid_fingerprint(page)
        found = True
        try:
            await tile.wait_for(state="visible", timeout=6000)
            found = await VideoGenerationMixin._tile_is_fully_visible(tile)
        except Exception:  # noqa: BLE001 - not surfaced by this term
            found = False
        log.info(
            "ui_automation_video.picker_search_tier",
            term_length=len(term),
            found=found,
            rendered_tiles=len(rendered) if rendered is not None else None,
        )
        return found

    @staticmethod
    async def _scroll_picker_grid(
        page: Page, delta_px: int = PICKER_GRID_SCROLL_DELTA_PX
    ) -> dict[str, object] | None:
        """Scroll the open resource picker down one step. The Tudo grid is
        virtualised, so off-screen tiles are absent from the DOM until
        scrolled into view. #287 round 6: react-virtuoso scrolls its own
        container, so the primary path drives the dialog's ACTUAL scrollable
        node via JS and returns evidence (tag/class + scrollTop before/after
        — a no-op scroll where scrollTop never moves is then visible in
        telemetry). Falls back to the blind hover+wheel when the JS probe
        fails, returning ``None`` (no evidence)."""
        info_raw: object = None
        try:
            info_raw = await page.evaluate(_PICKER_GRID_SCROLL_JS, delta_px)
        except Exception:  # noqa: BLE001 - JS probe is best-effort
            info_raw = None
        if isinstance(info_raw, dict):
            await page.wait_for_timeout(350)
            return cast("dict[str, object]", info_raw)
        dialog = page.locator(DIALOG_ANY).last
        try:
            await dialog.hover(timeout=2000)
        except Exception:  # noqa: BLE001 - hover is best-effort; wheel still scrolls
            pass
        await page.mouse.wheel(0, delta_px)
        await page.wait_for_timeout(350)
        return None

    @staticmethod
    async def _picker_grid_fingerprint(page: Page) -> frozenset[str] | None:
        """Identifiers of the tiles currently rendered in the open picker, or
        ``None`` when there is no evidence (the probe failed, or zero tiles
        matched — e.g. a DOM drift), so the scroll loop falls back to the
        legacy fixed budget instead of misreading 'no evidence' as 'end of
        grid'."""
        try:
            tile_ids = await page.evaluate(_PICKER_GRID_TILE_IDS_JS)
        except Exception:  # noqa: BLE001 - probe is best-effort evidence only
            return None
        if not isinstance(tile_ids, list) or not tile_ids:
            return None
        return frozenset(str(tile_id) for tile_id in cast("list[object]", tile_ids))

    @staticmethod
    async def _scroll_picker_grid_until_rendered(page: Page, tile: Locator) -> bool:
        """Scroll the virtualised picker grid until ``tile`` is in the DOM.

        #287: bounded by evidence of progress, not a fixed attempt count —
        keeps scrolling while the set of rendered tile identifiers still
        CHANGES between scrolls (the grid is still advancing) and stops after
        ``PICKER_GRID_SCROLL_STALL_LIMIT`` consecutive scrolls with no new
        tiles (end of grid), so the reachable depth is proportional to the
        grid size. When the DOM probe yields no evidence the legacy fixed
        ``PICKER_GRID_SCROLL_ATTEMPTS`` budget applies, and
        ``PICKER_GRID_SCROLL_MAX_ATTEMPTS`` is a hard safety ceiling either
        way. Returns whether the tile ended up in the DOM — including a tile
        rendered by the very last scroll (#283 off-by-one: the count runs
        BEFORE each scroll, so it is re-checked once after the loop)."""
        attempts = 0
        stalls = 0
        previous: frozenset[str] | None = None
        reason = "ceiling"
        while attempts < PICKER_GRID_SCROLL_MAX_ATTEMPTS:
            if await tile.count():
                log.info(
                    "ui_automation_video.picker_scroll_done",
                    reason="found",
                    attempts=attempts,
                    found=True,
                )
                return True
            scroll_info = await VideoGenerationMixin._scroll_picker_grid(page)
            attempts += 1
            rendered = await VideoGenerationMixin._picker_grid_fingerprint(page)
            log.info(
                "ui_automation_video.picker_scroll_probe",
                attempt=attempts,
                rendered_tiles=len(rendered) if rendered is not None else None,
                new_tiles=(
                    len(rendered - previous)
                    if rendered is not None and previous is not None
                    else None
                ),
                # #287 round 6 audit: WHICH node scrolled, and did it move at
                # all — a wrong-node no-op (scrollTop frozen) is now visible.
                scrolled_tag=scroll_info.get("tag") if scroll_info is not None else None,
                scrolled_class=scroll_info.get("cls") if scroll_info is not None else None,
                scroll_top_before=scroll_info.get("before") if scroll_info is not None else None,
                scroll_top_after=scroll_info.get("after") if scroll_info is not None else None,
            )
            if rendered is None:
                if attempts >= PICKER_GRID_SCROLL_ATTEMPTS:
                    reason = "legacy_budget"
                    break
            elif rendered == previous:
                stalls += 1
                if stalls >= PICKER_GRID_SCROLL_STALL_LIMIT:
                    reason = "stall"
                    break
            else:
                stalls = 0
                previous = rendered
        found = bool(await tile.count())
        log.info(
            "ui_automation_video.picker_scroll_done",
            reason=reason,
            attempts=attempts,
            found=found,
        )
        return found

    @staticmethod
    async def _find_picker_entity_tile(page: Page, entity_id: str) -> Locator:
        """Locate the Personagens-tab tile for a character entity. Each tile is
        keyed by the entity id as `data-tile-id="fe_id_<entityId>"` (exact — no
        display-name ambiguity). Scroll the grid until it renders (#287:
        progress-bounded, so a crowded character grid is fully reachable),
        then return the locator (the caller still waits for visibility)."""
        tile = page.locator(f"[data-tile-id='fe_id_{entity_id}']").first
        await VideoGenerationMixin._scroll_picker_grid_until_rendered(page, tile)
        return tile
