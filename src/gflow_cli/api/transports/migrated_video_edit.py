"""Drive Flow's video-to-video edit surface on ``flow.google.com`` (RPC ``jIps6``).

Two empirically measured paths share one ``jIps6`` request check:

1. **Local video file** (Main Composer ``Ingredients``): uploads the clip and any local
   ``--ref`` images (0-5) through the shared toolbar uploader, attaches the clip and the
   references as ``@`` mentions, configures ``Omni`` / aspect / resolution / ``x1``, reads
   the pre-submit credit quote once the clip is attached, and observes ``jIps6``.
2. **Existing Flow ``MEDIA_ID``** (Clip Editor ``/project/<pid>/edit/<MEDIA_ID>``):
   uploads any local ``--ref`` images on the project page first, opens the clip editor,
   resolves the clip's current workflow id from ``Zzl0ze`` (the extend port's reader),
   selects the Omni model, attaches the references and the prompt, reads the quote from
   the model menu, and observes ``jIps6``.

While either path prepares the request, every billed submit RPC the page might send is
aborted (the extend port's pre-submit hold); the credit cap is enforced before the click.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from playwright.async_api import Error as PlaywrightError

from gflow_cli.api.credit_cap import enforce_credit_cap, parse_credit_quote
from gflow_cli.api.dto import GenerationCheckpoint
from gflow_cli.api.transports._mc_common import (
    _CARET_TO_END_JS,
    ASPECT_LIGATURE,
    COMPOSER,
    IMAGE_SUBMIT_RPC,
    INGREDIENTS_LIGATURE,
    MENTION_CHIP,
    MENU_ITEM,
    OVERLAY,
    PICKER_CONFIRM,
    PICKER_OPTION,
    SUBMIT_RPCS,
    VIDEO_MODEL_MENU_MATCHERS,
    SubmitWatch,
    _select_resolution,
)
from gflow_cli.api.transports._mc_upload_video import upload_video_for_mention
from gflow_cli.api.transports.migrated_composer import (
    MigratedComposer,
    attach_submit_evidence,
    finish_video_result,
)
from gflow_cli.api.transports.migrated_extend import (
    _VIDEO_INGEST_POLL_S,
    EXTEND_SUBMIT_RPC,
    QUOTE_SETTLE_S,
    VIDEO_INGEST_READY_BUDGET_S,
    _anchor,
    _at,
    _check_extend_reference_ids,
    _confirm_editor,
    _single_extend_entry,
    _source_clip,
    batch_rpcids,
    holding_early_submits,
    require_video_source,
    validate_upload_files,
)
from gflow_cli.api.transports.migrated_extend import (
    EXTEND_MENTION_CHIP as CLIP_EDITOR_MENTION_CHIP,
)
from gflow_cli.api.transports.migrated_extend import (
    EXTEND_PROMPT as CLIP_EDITOR_PROMPT,
)
from gflow_cli.api.transports.migrated_extend import (
    EXTEND_SUBMIT as CLIP_EDITOR_SUBMIT,
)
from gflow_cli.api.video import (
    Aspect,
    EditVideoRequest,
    VideoModel,
    VideoResult,
    VideoStarted,
    VideoStartedCallback,
)
from gflow_cli.errors import (
    ConfigurationError,
    FlowHostMigratedError,
    GFlowError,
    ReferenceNotFoundError,
    UiSelectorDriftError,
    WireFormatError,
    nothing_submitted,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from playwright.async_api import Page

    from gflow_cli.api.dto import GenerationCheckpointObserver
    from gflow_cli.api.transports.migrated_composer import ClipSaver

log = structlog.get_logger(__name__)

EDIT_SUBMIT_RPC = "jIps6"
#: A billed submit that is not the edit: the source was not bound, or the wrong surface.
_WRONG_SUBMIT_RPCS = frozenset(SUBMIT_RPCS) | {EXTEND_SUBMIT_RPC, IMAGE_SUBMIT_RPC}
_EDIT_SUBMIT_WATCH_RPCS = SubmitWatch._RPCS | {EDIT_SUBMIT_RPC}
#: Every RPC that bills: none may leave the page while the edit is being prepared.
_EDIT_HELD_RPCS = _EDIT_SUBMIT_WATCH_RPCS | _WRONG_SUBMIT_RPCS
_ASPECT_INDEX_TO_ENUM: dict[int, Aspect] = {1: Aspect.PORTRAIT, 2: Aspect.LANDSCAPE}
_EDIT_MODEL_PREFIX: dict[VideoModel, str] = {
    VideoModel.OMNI_1_2_FLASH: "macaron_cookie_edit",
    VideoModel.OMNI_FLASH: "abra_edit",
}

MAIN_COMPOSER_SELECTOR = f"flow-prompt-box {COMPOSER}"
CHIP_CONTAINER_SELECTOR = "flow-prompt-box button.chip-container"
VIDEO_CHIP_SELECTOR = "flow-prompt-box button.chip-container:has(mat-icon:text-is('videocam'))"
MAIN_MENTION_CHIP_SELECTOR = f"flow-prompt-box {MENTION_CHIP}"
MAIN_CREDIT_LABEL = (
    ".cdk-overlay-pane flow-prompt-box-settings flow-credit-cost-label.settings-credit-cost"
)

CLIP_EDITOR_BOX = "flow-edit-video-prompt-box"
CLIP_EDITOR_MODEL_TRIGGER = f"{CLIP_EDITOR_BOX} button:has(mat-icon:text-is('arrow_drop_down'))"
#: The clip editor's own quote, read with every menu closed. Unmeasured: a miss is ``None``,
#: so an active cap refuses rather than trusting a price shown in an open menu.
CLIP_EDITOR_CREDIT_LABEL = f"{CLIP_EDITOR_BOX} flow-credit-cost-label"

_CAP_ROUTE = "flow.google.com video edit"


# --- input validation ----------------------------------------------------------


async def validate_edit_inputs(request: EditVideoRequest) -> None:
    """Refuse (nothing charged) an edit Flow must not be sent, before any project exists.

    Exit 36 for an option the clip editor does not offer, exit 11 for a local file that
    cannot be uploaded. Called at each edge (CLI, worker) and again at the client entry.
    """
    if request.source_media_id is not None and request.resolution == "360p":
        raise FlowHostMigratedError(
            detail=(
                "editing an existing Flow media UUID in the clip editor has no 360p toggle; "
                "--resolution 360p is only supported when editing a local video file"
            ),
            remediation_hint=(
                "Drop --resolution (or use --resolution 720p) when editing an existing media UUID, "
                "or pass a local video file path for 360p."
            ),
        )
    await validate_upload_files(request.source_video, request.reference_images)


# --- jIps6 request check ---------------------------------------------------------


def _check_model_key(
    model_key: Any,
    *,
    expected_model: VideoModel | None,
    expected_resolution: str | None,
    route: str,
) -> GFlowError | None:
    if not isinstance(model_key, str) or "_edit" not in model_key:
        return WireFormatError(
            detail=(
                f"migrated host: {EDIT_SUBMIT_RPC} carried model key {model_key!r} instead of "
                "an edit model key — refusing before Flow billed it"
            ),
            route=route,
        )
    if expected_model is not None:
        prefix = _EDIT_MODEL_PREFIX.get(expected_model)
        if prefix and not model_key.startswith(prefix):
            return WireFormatError(
                detail=(
                    f"migrated host: requested {expected_model.value} ({prefix}*) but "
                    f"{EDIT_SUBMIT_RPC} carried {model_key!r} — refusing before Flow billed it"
                ),
                route=route,
            )
    is_360p = model_key.endswith("_360p")
    if (expected_resolution == "360p") != is_360p and expected_resolution is not None:
        return WireFormatError(
            detail=(
                f"migrated host: requested {expected_resolution} video edit but "
                f"{EDIT_SUBMIT_RPC} carried {model_key!r} — refusing before Flow billed it"
            ),
            route=route,
        )
    return None


def _check_aspect(
    idx: Any, *, expected_aspect: Aspect | None, clip_editor: bool, route: str
) -> GFlowError | None:
    if expected_aspect is None:
        return None
    actual = _ASPECT_INDEX_TO_ENUM.get(idx) if isinstance(idx, int) else None
    if actual is None:
        return WireFormatError(
            detail=(
                f"migrated host: requested {expected_aspect.value} video edit but could not "
                f"read the aspect in {EDIT_SUBMIT_RPC} ({idx!r}) — refusing before Flow billed it"
            ),
            route=route,
        )
    if actual is expected_aspect:
        return None
    if clip_editor:
        return FlowHostMigratedError(
            detail=(
                f"the source clip is {actual.value} and flow.google.com edits existing clips "
                f"in the source's aspect; --aspect {expected_aspect.value} does not match — "
                "refused before Flow billed it"
            ),
            remediation_hint=(
                "Drop --aspect when editing an existing Flow media UUID, or pass a local video "
                "file to change output aspect in the main composer."
            ),
        )
    return WireFormatError(
        detail=(
            f"migrated host: requested {expected_aspect.value} video edit but {EDIT_SUBMIT_RPC} "
            f"carried {actual.value} — refusing before Flow billed it"
        ),
        route=route,
    )


def edit_request_problem(  # noqa: PLR0913
    rpcid: str,
    body: str,
    *,
    workflow_id: str,
    expected_model: VideoModel | None = None,
    expected_resolution: str | None = None,
    expected_aspect: Aspect | None = None,
    expected_reference_ids: tuple[str, ...] = (),
    clip_editor: bool = False,
) -> GFlowError | None:
    """Return a pre-submit refusal error if *body* does not match this V2V edit request."""
    route = f"batchexecute:{rpcid}"
    if rpcid in _WRONG_SUBMIT_RPCS:
        return WireFormatError(
            detail=(
                f"migrated host: video edit submitted on {rpcid} instead of {EDIT_SUBMIT_RPC} — "
                "the source video was not bound; refused before Flow billed it"
            ),
            route=route,
        )
    if rpcid != EDIT_SUBMIT_RPC:
        return None
    entry, entry_err = _single_extend_entry(body, route=route, rpc=EDIT_SUBMIT_RPC)
    if entry_err is not None:
        return entry_err
    found_wf = _at(entry, 0, 1)
    if found_wf != workflow_id:
        return WireFormatError(
            detail=(
                f"migrated host: {EDIT_SUBMIT_RPC} targeted workflow {found_wf!r}, not "
                f"{workflow_id} — refusing before Flow billed it"
            ),
            route=route,
        )
    return (
        _check_model_key(
            _at(entry, 2),
            expected_model=expected_model,
            expected_resolution=expected_resolution,
            route=route,
        )
        or _check_aspect(
            _at(entry, 3),
            expected_aspect=expected_aspect,
            clip_editor=clip_editor,
            route=route,
        )
        or _check_extend_reference_ids(
            entry,
            expected_reference_ids=expected_reference_ids,
            route=route,
            rpc=EDIT_SUBMIT_RPC,
        )
    )


def batch_edit_request_problem(  # noqa: PLR0913
    rpcids: list[str],
    body: str,
    *,
    workflow_id: str,
    expected_model: VideoModel | None = None,
    expected_resolution: str | None = None,
    expected_aspect: Aspect | None = None,
    expected_reference_ids: tuple[str, ...] = (),
    clip_editor: bool = False,
) -> GFlowError | None:
    """Check every RPC frame in a (possibly batched) ``batchexecute`` request."""
    for rpcid in batch_rpcids(rpcids, body):
        verdict = edit_request_problem(
            rpcid,
            body,
            workflow_id=workflow_id,
            expected_model=expected_model,
            expected_resolution=expected_resolution,
            expected_aspect=expected_aspect,
            expected_reference_ids=expected_reference_ids,
            clip_editor=clip_editor,
        )
        if verdict is not None:
            return verdict
    return None


# --- UI helpers -------------------------------------------------------------


async def _caret_to_end(page: Page, selector: str) -> None:
    await page.locator(selector).first.click(timeout=5000)
    await page.evaluate(_CARET_TO_END_JS, selector)


async def _clear_main_composer_chips(page: Page) -> None:
    """Remove leftover ingredient chips persisted in Flow's project composer draft."""
    chips = page.locator(CHIP_CONTAINER_SELECTOR)
    for _ in range(16):
        if await chips.count() == 0:
            return
        first = chips.first
        try:
            await first.hover(timeout=2000)
        except PlaywrightError:
            log.debug("migrated_video_edit.chip_hover_failed")
        cancel = first.locator("mat-icon:text-is('cancel')").first
        if await cancel.count():
            await cancel.click(force=True, timeout=3000)
        else:
            await first.click(timeout=3000)
        await page.wait_for_timeout(300)
    if await chips.count() > 0:
        raise UiSelectorDriftError(
            detail=(
                "migrated host: could not clear leftover ingredient chips from the "
                "composer before video edit — refusing before submit"
            ),
        )


async def _clear_editor_prompt(page: Page, selector: str) -> None:
    await page.locator(selector).first.click(timeout=5000)
    await page.keyboard.press("ControlOrMeta+a")
    await page.keyboard.press("Backspace")
    await page.wait_for_timeout(400)


_EXTENSION = re.compile(r"\.[a-z0-9]{2,4}$", re.IGNORECASE)
_EXTENSION_PREFIX = re.compile(r"\.[a-z0-9]{2,4}(?=$|[^0-9a-z_-]|video|image)", re.IGNORECASE)


def _bare_name(name: str) -> str:
    """*name* compared case-insensitively, without a file extension."""
    return _EXTENSION.sub("", name.strip()).casefold()


#: What may follow the name in an option's word: nothing, a character that cannot continue
#: the run-unique token, or a media-type label run on without a separator (live
#: 2026-11-25: ``source-5e66c86fVideo``).
_AFTER_NAME = re.compile(r"(?:(?:video|image)(?![0-9a-z_-])|(?![0-9a-z_-]))", re.IGNORECASE)


def _names_option(query: str, text: str) -> bool:
    """Whether a picker option's visible *text* names *query*.

    True when a word of *text* starts with the name (casefolded, any file extension
    stripped from *query* and skipped in the word) and what follows is accepted by
    :data:`_AFTER_NAME`. The run-unique tag in *query* keeps one upload from naming
    another, so ``source-5e66c86f0Video`` does not name ``source-5e66c86f``.
    """
    wanted = _bare_name(query)
    for word in text.casefold().split():
        if not word.startswith(wanted):
            continue
        rest = word[len(wanted) :]
        if (ext := _EXTENSION_PREFIX.match(rest)) is not None:
            rest = rest[ext.end() :]
        if _AFTER_NAME.match(rest):
            return True
    return False


@dataclass(frozen=True, slots=True)
class _Picker:
    """Where an ``@`` lists the project's assets, and how a listed one is added."""

    option: str
    add: str
    #: Type the name without its extension (the asset browser filters on it).
    bare_query: bool


#: The main composer's ``@`` mention picker.
COMPOSER_PICKER = _Picker(option=f"{OVERLAY} {PICKER_OPTION}", add=PICKER_CONFIRM, bare_query=False)
#: The clip editor's ``@`` opens Flow's asset browser, not the mention picker
#: (docs/superpowers/spikes/2026-11-25-clip-editor-references-asset-browser.md).
ASSET_BROWSER_OPTION = "flow-add-menu-asset-list button[role='option'].asset-item"
ASSET_BROWSER_ADD = "flow-add-menu-detail-pane button.detail-add-to-prompt-btn"
CLIP_EDITOR_ASSET_BROWSER = _Picker(
    option=ASSET_BROWSER_OPTION, add=ASSET_BROWSER_ADD, bare_query=True
)
#: How long a listed asset's add button may stay disabled before the attempt is retried.
_ADD_ENABLED_BUDGET_S = 5.0


async def _wait_enabled(button: Any) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _ADD_ENABLED_BUDGET_S
    while await button.is_disabled():
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.5)
    return True


async def _try_commit_picker_option(  # noqa: PLR0913
    page: Page,
    *,
    query: str,
    chip_selector: str,
    expect_count: int,
    offered: list[str],
    picker: _Picker = COMPOSER_PICKER,
) -> tuple[bool, bool]:
    """Click the one picker option naming *query*: ``(committed, still_processing)``.

    ``offered`` receives the options' visible texts, for the timeout message.
    ``still_processing`` is True when the asset is listed but the picker's confirm button
    is still disabled (Flow is still processing an upload). No Enter is pressed: in the
    edit prompt boxes Enter can submit the edit. Two options naming it are refused.
    """
    options = page.locator(picker.option)
    texts = [" ".join(t.split()) for t in await options.all_text_contents()]
    offered[:] = texts
    hits = [i for i, text in enumerate(texts) if _names_option(query, text)]
    if len(hits) > 1:
        raise ReferenceNotFoundError(
            detail=(
                f"migrated host: the @ picker lists {len(hits)} assets named {query!r} — "
                "refusing to guess before submit"
            ),
        )
    if not hits:
        return False, False
    option = options.nth(hits[0])
    if not await option.is_visible():
        return False, False
    await option.click(timeout=4000)
    await page.wait_for_timeout(500)
    if await page.locator(chip_selector).count() >= expect_count:
        return True, False
    confirm = page.locator(picker.add).first
    if await confirm.count() and await confirm.is_visible():
        if not await _wait_enabled(confirm):
            return False, True
        await confirm.click(timeout=4000)
        await page.wait_for_timeout(700)
    return await page.locator(chip_selector).count() >= expect_count, False


async def _attach_by_click(  # noqa: PLR0913
    page: Page,
    *,
    composer_selector: str,
    chip_selector: str,
    query: str,
    media_id: str,
    expect_count: int,
    what: str,
    picker: _Picker = COMPOSER_PICKER,
) -> None:
    """Attach an asset uploaded in this run via ``@<query>``, by clicking its option.

    A fresh upload is listed in the picker, and becomes attachable, only once Flow has
    processed it, so the picker is polled for the extend port's whole ingest budget
    rather than a few quick retries.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + VIDEO_INGEST_READY_BUDGET_S
    attempt = 0
    offered: list[str] = []
    while True:
        attempt += 1
        await _caret_to_end(page, composer_selector)
        await page.keyboard.type("@", delay=100)
        await page.wait_for_timeout(1500)
        typed = _EXTENSION.sub("", query.strip()) if picker.bare_query else query
        await page.keyboard.type(typed, delay=60)
        await page.wait_for_timeout(2000)
        committed, still_processing = await _try_commit_picker_option(
            page,
            query=query,
            chip_selector=chip_selector,
            expect_count=expect_count,
            offered=offered,
            picker=picker,
        )
        if committed:
            await page.keyboard.type(" ", delay=60)
            return
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        log.info(
            "migrated_video_edit.mention_retry",
            what=what,
            attempt=attempt,
            still_processing=still_processing,
        )
        if loop.time() >= deadline:
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: the {what} uploaded as media {media_id} ({query!r}) did "
                    f"not attach as a chip within {VIDEO_INGEST_READY_BUDGET_S:.0f}s "
                    f"(still_processing={still_processing}); the picker offered: "
                    f"{_offered_text(offered)} — refusing before submit"
                ),
            )
        await asyncio.sleep(_VIDEO_INGEST_POLL_S)


def _offered_text(offered: list[str]) -> str:
    shown = [text if len(text) <= 60 else f"{text[:57]}..." for text in offered[:5]]
    more = f" (+{len(offered) - 5} more)" if len(offered) > 5 else ""
    return (", ".join(repr(t) for t in shown) or "<nothing>") + more


async def _stable_quote(read: Callable[[], Awaitable[int | None]]) -> int | None:
    """A quote that has held one value for :data:`QUOTE_SETTLE_S`, else ``None``.

    Called once the settings are confirmed applied. A re-price is asynchronous and may
    start late, so two quick equal reads are not proof; ``None`` makes the cap refuse.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 3 * QUOTE_SETTLE_S
    value: int | None = None
    since = loop.time()
    while True:
        quoted = await read()
        now = loop.time()
        if quoted != value:
            value, since = quoted, now
        if value is not None and now - since >= QUOTE_SETTLE_S:
            return value
        if now >= deadline:
            return None
        await asyncio.sleep(0.25)


async def _read_label(page: Page, selector: str) -> int | None:
    try:
        return parse_credit_quote(await page.locator(selector).first.inner_text(timeout=2000))
    except PlaywrightError:
        return None


async def _apply_main_edit_settings(
    composer: MigratedComposer, page: Page, request: EditVideoRequest, *, quote: bool
) -> int | None:
    """Apply Main Composer video Ingredients settings; with *quote*, read the settled quote."""
    pane = await composer._open_pane(page)
    try:
        await composer._select(page, pane, axis="mode", lig="videocam")
        await composer._select(page, pane, axis="submode", lig=INGREDIENTS_LIGATURE)
        await composer._select_model(page, pane, request.model)
        if request.aspect is not None:
            await composer._select(page, pane, axis="aspect", lig=ASPECT_LIGATURE[request.aspect])
        if request.resolution is not None:
            await _select_resolution(page, pane, request.resolution)
        await composer._select(page, pane, axis="count", text="x1")
        quoted = (
            await _stable_quote(lambda: _read_label(page, MAIN_CREDIT_LABEL)) if quote else None
        )
    except BaseException:
        await composer._close_pane(page, strict=False)
        raise
    await composer._close_pane(page, strict=True)
    return quoted


async def _open_clip_editor_model_menu(page: Page) -> tuple[Any, Any]:
    """Open the clip editor's model menu: ``(trigger, menu items)``."""
    trigger = await _anchor(page, CLIP_EDITOR_MODEL_TRIGGER, named="clip editor model selector")
    await trigger.click(timeout=4000)
    items = page.locator(f"{OVERLAY} {MENU_ITEM}")
    try:
        await items.first.wait_for(state="visible", timeout=5000)
    except PlaywrightError as exc:
        raise UiSelectorDriftError(
            detail="migrated host: clip editor model menu did not open (host=migrated)",
        ) from exc
    return trigger, items


def _model_matcher(model: VideoModel) -> Any:
    matcher = VIDEO_MODEL_MENU_MATCHERS.get(model)
    if matcher is None:
        raise ConfigurationError(
            detail=f"no flow.google.com menu matcher for model {model.value!r}"
        )
    return matcher


async def _select_clip_editor_model(page: Page, model: VideoModel) -> None:
    """Select *model* in the clip editor's model menu (no-op when it is already shown)."""
    matcher = _model_matcher(model)
    trigger = await _anchor(page, CLIP_EDITOR_MODEL_TRIGGER, named="clip editor model selector")
    if matcher.matches((await trigger.text_content() or "").strip()):
        return
    _, items = await _open_clip_editor_model_menu(page)
    offered = [t.strip() for t in await items.all_text_contents()]
    hits = [i for i, text in enumerate(offered) if matcher.matches(text)]
    if len(hits) != 1:
        await page.keyboard.press("Escape")
        raise ConfigurationError(
            detail=(
                f"model {model.value!r} matched {len(hits)} entry/entries in the clip editor "
                f"model menu (offered: {', '.join(offered)})"
            ),
        )
    await items.nth(hits[0]).click(timeout=4000)
    await page.wait_for_timeout(400)
    shown = (await trigger.text_content() or "").strip()
    if not matcher.matches(shown):
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: the clip editor shows model {shown!r} after selecting "
                f"{model.value!r} — refusing before submit (host=migrated)"
            ),
        )


async def _require_chips(page: Page, *, selector: str, count: int, what: str) -> None:
    if await page.locator(selector).count() != count:
        raise ReferenceNotFoundError(
            detail=f"migrated host: the {what} chip count changed before submit"
        )


async def _require_classic_composer(
    composer: MigratedComposer, page: Page, project_id: str
) -> None:
    if await composer.ensure_editor_for(page, project_id, needs_classic=True) == "agent_only":
        raise FlowHostMigratedError(
            detail=(
                "Flow served the agent-only composer (#799), which has no upload entry; "
                "video edit from local files is not ported to it yet"
            ),
            remediation_hint=(
                "Re-run later: Flow serves the classic composer on most loads. Editing a clip "
                "already in the project by its media id needs no upload when no --ref is given."
            ),
        )


# --- the two preparation paths ------------------------------------------------------

#: (source workflow id, reference media ids, quoted credits) — everything the submit needs.
_Prepared = tuple[str, tuple[str, ...], int | None]


async def _prepare_local_edit(
    composer: MigratedComposer, page: Page, request: EditVideoRequest, *, project_id: str
) -> _Prepared:
    assert request.source_video is not None
    await _require_classic_composer(composer, page, project_id)
    uploaded = await upload_video_for_mention(page, project_id, request.source_video)
    refs = [await composer.upload(page, project_id, ref) for ref in request.reference_images]
    await _apply_main_edit_settings(composer, page, request, quote=False)
    await composer.clear_composer(page)
    await _clear_main_composer_chips(page)
    await _attach_by_click(
        page,
        composer_selector=MAIN_COMPOSER_SELECTOR,
        chip_selector=VIDEO_CHIP_SELECTOR,
        query=uploaded.query_name,
        media_id=uploaded.media_id,
        expect_count=1,
        what="source video",
    )
    for idx, (ref_id, name) in enumerate(refs, start=1):
        await _attach_by_click(
            page,
            composer_selector=MAIN_COMPOSER_SELECTOR,
            chip_selector=MAIN_MENTION_CHIP_SELECTOR,
            query=name,
            media_id=ref_id,
            expect_count=idx,
            what="reference image",
        )
    await composer.send_prompt(page, request.prompt, append=True)
    # Flow locks the duration to the attached clip: price the request as it will be sent.
    quoted = await _apply_main_edit_settings(
        composer, page, request, quote=request.max_credits is not None
    )
    quoted = enforce_credit_cap(quoted=quoted, max_credits=request.max_credits, route=_CAP_ROUTE)
    await _require_chips(page, selector=VIDEO_CHIP_SELECTOR, count=1, what="source video")
    await _require_chips(
        page, selector=MAIN_MENTION_CHIP_SELECTOR, count=len(refs), what="reference image"
    )
    return uploaded.workflow_id, tuple(ref_id for ref_id, _ in refs), quoted


async def _prepare_clip_editor_edit(
    composer: MigratedComposer, page: Page, request: EditVideoRequest, *, project_id: str
) -> _Prepared:
    media_id = request.source_media_id
    assert media_id is not None
    refs: list[tuple[str, str]] = []
    if request.reference_images:
        await _require_classic_composer(composer, page, project_id)
        refs = [await composer.upload(page, project_id, ref) for ref in request.reference_images]
    clip = await _source_clip(page, project_id=project_id, media_id=media_id)
    workflow_id = require_video_source(
        clip, media_id=media_id, project_id=project_id, action="video edit needs a video source"
    )
    _confirm_editor(page, project_id=project_id, media_id=media_id)
    await _anchor(page, CLIP_EDITOR_PROMPT, named="clip editor prompt box")
    model = request.model
    await _select_clip_editor_model(page, model)
    await _clear_editor_prompt(page, CLIP_EDITOR_PROMPT)
    # By click in the asset browser the clip editor's @ opens, never by Enter (which can
    # submit the edit there).
    for idx, (ref_id, name) in enumerate(refs, start=1):
        await _attach_by_click(
            page,
            composer_selector=CLIP_EDITOR_PROMPT,
            chip_selector=CLIP_EDITOR_MENTION_CHIP,
            query=name,
            media_id=ref_id,
            expect_count=idx,
            what="reference image",
            picker=CLIP_EDITOR_ASSET_BROWSER,
        )
    await _caret_to_end(page, CLIP_EDITOR_PROMPT)
    await page.keyboard.insert_text(request.prompt)
    # After the model is confirmed, the references attached and the prompt typed, with
    # every menu closed: the quote prices the request as it will be sent.
    quoted = (
        await _stable_quote(lambda: _read_label(page, CLIP_EDITOR_CREDIT_LABEL))
        if request.max_credits is not None
        else None
    )
    quoted = enforce_credit_cap(quoted=quoted, max_credits=request.max_credits, route=_CAP_ROUTE)
    await _require_chips(
        page, selector=CLIP_EDITOR_MENTION_CHIP, count=len(refs), what="reference image"
    )
    return workflow_id, tuple(ref_id for ref_id, _ in refs), quoted


# --- submit -----------------------------------------------------------------


async def _submit_edit_and_finish(  # noqa: PLR0913
    composer: MigratedComposer,
    page: Page,
    request: EditVideoRequest,
    prepared: _Prepared,
    *,
    project_id: str,
    clip_editor: bool,
    save: ClipSaver,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
    on_checkpoint: GenerationCheckpointObserver | None,
) -> VideoResult:
    workflow_id, reference_ids, quoted_credits = prepared
    started_ids: list[str] = []

    def note_started(started: VideoStarted) -> Awaitable[None] | None:
        started_ids.append(started.media_id)
        if on_checkpoint is not None:
            on_checkpoint(
                GenerationCheckpoint(
                    phase="remote_started",
                    operation_id=started.flow_operation_id,
                    media_ids=(started.media_id,),
                )
            )
        return on_started(started) if on_started is not None else None

    def check(rpcid: str, body: str) -> GFlowError | None:
        return batch_edit_request_problem(
            [rpcid],
            body,
            workflow_id=workflow_id,
            expected_model=request.model,
            expected_resolution=request.resolution,
            expected_aspect=request.aspect,
            expected_reference_ids=reference_ids,
            clip_editor=clip_editor,
        )

    if on_checkpoint is not None:
        # Everything before this point is pre-submit (charged "no"); only the click is next.
        on_checkpoint(GenerationCheckpoint(phase="submit_attempted"))
    try:
        record = await composer.submit_and_observe(
            page,
            poll_timeout_s=poll_timeout_s,
            on_started=note_started,
            project_id=project_id,
            submit_rpcs=(EDIT_SUBMIT_RPC,),
            submit_selector=CLIP_EDITOR_SUBMIT if clip_editor else None,
            request_check=check,
            # Not the clip uploaded in this run: its own record names it, so a lost reply
            # would adopt the source as the edit. The reference ids are run-unique uploads.
            adopt_ids=reference_ids,
        )
    except Exception as exc:
        attach_submit_evidence(exc, quoted_credits=quoted_credits, started_ids=started_ids)
        raise

    return await finish_video_result(
        composer,
        page,
        record,
        project_id=project_id,
        quoted_credits=quoted_credits,
        save=save,
        download=download,
    )


async def run_video_edit(  # noqa: PLR0913
    page: Page,
    request: EditVideoRequest,
    *,
    project_id: str,
    save: ClipSaver,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
    on_checkpoint: GenerationCheckpointObserver | None = None,
) -> VideoResult:
    """Drive a single video-to-video edit (local file or existing ``MEDIA_ID``)."""
    await validate_edit_inputs(request)
    composer = MigratedComposer()
    clip_editor = request.source_video is None
    log.info(
        "migrated_video_edit.dispatch",
        project_id=project_id,
        source_kind="media_id" if clip_editor else "local_file",
        ref_count=len(request.reference_images),
    )
    prepare = _prepare_clip_editor_edit if clip_editor else _prepare_local_edit
    async with holding_early_submits(page, rpcs=_EDIT_HELD_RPCS, noun="video edit"):
        with (
            SubmitWatch(page, rpcs=_EDIT_SUBMIT_WATCH_RPCS) as watch,
            nothing_submitted(sent=watch.seen),
        ):
            try:
                prepared = await prepare(composer, page, request, project_id=project_id)
            except PlaywrightError as exc:  # incl. a click or wait that timed out
                raise UiSelectorDriftError(
                    detail=(
                        "migrated host: a video edit control did not respond "
                        f"({type(exc).__name__}) — nothing was submitted (host=migrated)"
                    ),
                ) from exc
    return await _submit_edit_and_finish(
        composer,
        page,
        request,
        prepared,
        project_id=project_id,
        clip_editor=clip_editor,
        save=save,
        poll_timeout_s=poll_timeout_s,
        download=download,
        on_started=on_started,
        on_checkpoint=on_checkpoint,
    )


__all__ = [
    "CLIP_EDITOR_MODEL_TRIGGER",
    "CLIP_EDITOR_PROMPT",
    "CLIP_EDITOR_SUBMIT",
    "EDIT_SUBMIT_RPC",
    "VIDEO_CHIP_SELECTOR",
    "batch_edit_request_problem",
    "edit_request_problem",
    "run_video_edit",
    "validate_edit_inputs",
]
