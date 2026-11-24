"""Drive Flow's video-to-video edit surface on ``flow.google.com`` (RPC ``jIps6``).

Supports two empirically measured paths sharing one ``jIps6`` route guard:

1. **Local video file** (Main Composer ``Ingredients``): uploads the local clip via
   toolbar ``+ -> upload`` (``/upload/v1/flow/upload/video/<uuid>``) and any local
   ``--ref`` images (0–5), attaches the video and image references via ``@`` mentions,
   configures ``Omni 1.2 Flash`` / ``aspect`` / ``resolution`` / ``x1``, checks the
   pre-submit credit quote, and observes ``jIps6`` → ``jwpduf``/``as29s``.
2. **Existing Flow ``MEDIA_ID``** (Clip Editor ``/project/<pid>/edit/<MEDIA_ID>``):
   uploads any local ``--ref`` images on the project page first, opens the clip editor,
   resolves the clip's current ``workflow_id`` from ``Zzl0ze`` (supporting both
   generated and uploaded video records), selects the Omni model and reads its credit
   quote from the model menu overlay, attaches ``--ref`` mentions, and observes
   ``jIps6`` → ``jwpduf``/``as29s``.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import TYPE_CHECKING, Any, cast

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api._flow_workspace import validate_image_file
from gflow_cli.api.credit_cap import enforce_credit_cap, parse_credit_quote
from gflow_cli.api.transports._common import extract_project_id, raise_if_known_landing
from gflow_cli.api.transports._mc_common import (
    _CARET_TO_END_JS,
    ASPECT_LIGATURE,
    COMPOSER,
    FRAME_SEARCH_ATTEMPTS,
    FRAME_SEARCH_RETRY_PAUSE_S,
    INGREDIENTS_LIGATURE,
    MENTION_CHIP,
    MENU_ITEM,
    MIGRATED_PROJECT_URL,
    OVERLAY,
    PICKER_CONFIRM,
    PICKER_OPTION,
    VIDEO_MODEL_MENU_MATCHERS,
    SubmitWatch,
    _select_resolution,
)
from gflow_cli.api.transports._mc_upload_video import UploadedVideo, upload_video
from gflow_cli.api.transports.batchexecute import (
    GenerationRecord,
    find_record,
)
from gflow_cli.api.transports.migrated_composer import MigratedComposer, finish_video_result
from gflow_cli.api.transports.migrated_extend import (
    ANCHOR_TIMEOUT_S,
    SourceClip,
    _at,
    _confirm_editor,
    _current_workflow,
    _frames,
    _source_clip,
    batch_rpcids,
    require_video_source,
)
from gflow_cli.api.video import (
    EDIT_DEFAULT_MODEL,
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
    from collections.abc import Awaitable

    from playwright.async_api import Page

    from gflow_cli.api.transports.migrated_composer import ClipSaver

log = structlog.get_logger(__name__)

EDIT_SUBMIT_RPC = "jIps6"
_WRONG_SUBMIT_RPCS = frozenset({"YhhmEf", "eb1hJf", "MZZa6b", "nprQif", "fZytfe"})
_EDIT_SUBMIT_WATCH_RPCS = SubmitWatch._RPCS | {EDIT_SUBMIT_RPC}
_ASPECT_INDEX_TO_ENUM: dict[int, Aspect] = {1: Aspect.PORTRAIT, 2: Aspect.LANDSCAPE}
_EDIT_MODEL_PREFIX: dict[VideoModel, str] = {
    VideoModel.OMNI_1_2_FLASH: "macaron_cookie_edit",
    VideoModel.OMNI_FLASH: "abra_edit",
}

MAIN_COMPOSER_SELECTOR = f"flow-prompt-box {COMPOSER}"
CHIP_CONTAINER_SELECTOR = "flow-prompt-box button.chip-container"
VIDEO_CHIP_SELECTOR = "flow-prompt-box button.chip-container:has(mat-icon:text-is('videocam'))"
MAIN_MENTION_CHIP_SELECTOR = f"flow-prompt-box {MENTION_CHIP}"

CLIP_EDITOR_BOX = "flow-edit-video-prompt-box"
CLIP_EDITOR_PROMPT = f"{CLIP_EDITOR_BOX} .ProseMirror"
CLIP_EDITOR_MENTION_CHIP = f"{CLIP_EDITOR_BOX} {MENTION_CHIP}"
CLIP_EDITOR_SUBMIT = f"{CLIP_EDITOR_BOX} flow-generate-icon-button button"
CLIP_EDITOR_MODEL_TRIGGER = f"{CLIP_EDITOR_BOX} button:has(mat-icon:text-is('arrow_drop_down'))"

#: Budget for an uploaded local video to finish server-side ingest and become attachable
#: via the ``@`` picker (while processing, ``detail-add-to-prompt-btn`` stays disabled).
VIDEO_INGEST_ATTACH_BUDGET_S = 90.0
_VIDEO_INGEST_POLL_S = 3.0


# --- Zzl0ze source classification -------------------------------------------


def _is_uploaded_video_media_info(rec: list[Any]) -> bool:
    """True when ``rec[7]`` matches a locally uploaded video record in ``Zzl0ze``.

    Measured live 2026-11-24: uploaded videos have ``rec[7][0] is None`` and
    ``rec[7][1] == [width, height, [3]]`` (with no ``image/*`` mime at ``rec[7][1][6]``).
    """
    if _at(rec, 7, 0) is not None:
        return False
    info = _at(rec, 7, 1)
    if not isinstance(info, list):
        return False
    typed_info = cast("list[Any]", info)
    if len(typed_info) < 3:
        return False
    return (
        isinstance(typed_info[0], int) and isinstance(typed_info[1], int) and typed_info[2] == [3]
    )


def edit_source_record(payload: Any, media_id: str) -> SourceClip | None:
    """Resolve ``media_id`` from ``Zzl0ze`` into ``SourceClip(workflow_id, kind)``.

    Recognises both Flow-generated videos (``rec[7][0]`` is a list) and locally
    uploaded videos (``rec[7][0] is None`` with ``rec[7][1] == [w, h, [3]]``).
    """
    rec = find_record(payload, (2, media_id))
    if rec is None:
        return None
    current = _current_workflow(payload, media_id)
    head = find_record(payload, (0, current)) if current else None
    if head is not None and head[2] == media_id:
        rec = head
    mime = _at(rec, 7, 1, 6)
    if isinstance(mime, str) and mime.startswith("image/"):
        kind = "image"
    elif isinstance(_at(rec, 7, 0), list) or _is_uploaded_video_media_info(rec):
        kind = "video"
    else:
        kind = "unknown"
    return SourceClip(workflow_id=str(rec[0]), kind=kind)


# --- jIps6 route guard ------------------------------------------------------


def _extract_reference_ids(entry: Any) -> set[str] | None:
    raw = _at(entry, 8)
    if raw is None:
        return set()
    if not isinstance(raw, list):
        return None
    ids: set[str] = set()
    for item in cast("list[Any]", raw):
        if isinstance(item, list):
            typed_item = cast("list[Any]", item)
            if len(typed_item) >= 2 and isinstance(typed_item[1], str):
                ids.add(typed_item[1])
    return ids


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
    if expected_resolution == "360p" and not is_360p:
        return WireFormatError(
            detail=(
                f"migrated host: requested 360p video edit but {EDIT_SUBMIT_RPC} carried "
                f"{model_key!r} — refusing before Flow billed it"
            ),
            route=route,
        )
    if expected_resolution == "720p" and is_360p:
        return WireFormatError(
            detail=(
                f"migrated host: requested 720p video edit but {EDIT_SUBMIT_RPC} carried "
                f"{model_key!r} — refusing before Flow billed it"
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
    if actual is None or actual is expected_aspect:
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


def _check_reference_ids(
    entry: Any, *, expected_reference_ids: tuple[str, ...], route: str
) -> GFlowError | None:
    sent = _extract_reference_ids(entry)
    if sent is None:
        return WireFormatError(
            detail=(
                f"migrated host: could not read {EDIT_SUBMIT_RPC} reference slots — "
                "refusing before Flow billed it"
            ),
            route=route,
        )
    expected = set(expected_reference_ids)
    missing = sorted(expected - sent)
    extra = sorted(sent - expected)
    if missing or extra:
        return WireFormatError(
            detail=(
                f"migrated host: {EDIT_SUBMIT_RPC} references did not match the request "
                f"(missing: {', '.join(missing[:4]) or 'none'}; "
                f"unexpected: {', '.join(extra[:4]) or 'none'}) — refusing before Flow billed it"
            ),
            route=route,
        )
    return None


def _decode_edit_frames(body: str) -> list[Any]:
    decoded: list[Any] = []
    for frame in _frames(body):
        if _at(frame, 0) != EDIT_SUBMIT_RPC:
            continue
        try:
            decoded.append(json.loads(str(_at(frame, 1))))
        except (ValueError, TypeError):
            decoded.append(None)
    return decoded


def _single_edit_entry(body: str, *, route: str) -> tuple[Any, GFlowError | None]:
    edit_frames = _decode_edit_frames(body)
    if len(edit_frames) != 1:
        return None, WireFormatError(
            detail=(
                f"migrated host: expected 1 {EDIT_SUBMIT_RPC} frame in submit batch, "
                f"saw {len(edit_frames)} — refusing before Flow billed it"
            ),
            route=route,
        )
    entries = _at(edit_frames[0], 0)
    if not isinstance(entries, list) or len(cast("list[Any]", entries)) != 1:
        count = len(cast("list[Any]", entries)) if isinstance(entries, list) else 0
        return None, WireFormatError(
            detail=(
                f"migrated host: expected 1 single-clip entry in {EDIT_SUBMIT_RPC}, "
                f"saw {count} — refusing before Flow billed it"
            ),
            route=route,
        )
    return cast("list[Any]", entries)[0], None


def edit_request_problem(
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
    entry, entry_err = _single_edit_entry(body, route=route)
    if entry_err is not None:
        return entry_err
    found_wf = _at(entry, 0, 1)
    if not isinstance(found_wf, str):
        return WireFormatError(
            detail=(
                f"migrated host: could not read the source workflow id in {EDIT_SUBMIT_RPC} — "
                "refusing before Flow billed it"
            ),
            route=route,
        )
    if found_wf != workflow_id:
        return WireFormatError(
            detail=(
                f"migrated host: {EDIT_SUBMIT_RPC} targeted workflow {found_wf}, not "
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
        or _check_reference_ids(entry, expected_reference_ids=expected_reference_ids, route=route)
    )


def batch_edit_request_problem(
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
        with contextlib.suppress(PlaywrightError):
            await first.hover(timeout=2000)
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
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Backspace")
    await page.wait_for_timeout(400)


async def _try_commit_picker_option(
    page: Page,
    *,
    query: str,
    chip_selector: str,
    expect_count: int,
) -> tuple[bool, bool]:
    """Try clicking the picker option matching *query*.

    Returns ``(committed, still_processing)`` where ``still_processing`` is True when
    the asset is listed but ``detail-add-to-prompt-btn`` is still disabled (uploaded
    video transcoding on Flow's servers).
    """
    options = page.locator(f"{OVERLAY} {PICKER_OPTION}").filter(has_text=query)
    if not await options.count() or not await options.first.is_visible():
        return False, False
    await options.first.click(timeout=4000)
    await page.wait_for_timeout(500)
    if await page.locator(chip_selector).count() >= expect_count:
        return True, False
    confirm = page.locator(PICKER_CONFIRM).first
    if await confirm.count() and await confirm.is_visible():
        if await confirm.is_disabled():
            return False, True
        await confirm.click(timeout=4000)
        await page.wait_for_timeout(700)
    return await page.locator(chip_selector).count() >= expect_count, False


async def _attach_mention_asset(
    page: Page,
    *,
    composer_selector: str,
    chip_selector: str,
    query: str,
    expect_count: int,
    max_wait_s: float,
    asset_label: str,
) -> None:
    """Attach one uploaded video or image reference via ``@<query>`` and verify its chip."""
    deadline = time.monotonic() + max_wait_s
    attempt = 0
    while True:
        attempt += 1
        await _caret_to_end(page, composer_selector)
        await page.keyboard.type("@", delay=100)
        await page.wait_for_timeout(1500)
        await page.keyboard.type(query, delay=60)
        await page.wait_for_timeout(2000)
        committed, still_processing = await _try_commit_picker_option(
            page, query=query, chip_selector=chip_selector, expect_count=expect_count
        )
        if committed:
            await page.keyboard.type(" ", delay=60)
            return
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        log.info(
            "migrated_video_edit.mention_retry",
            asset=asset_label,
            attempt=attempt,
            still_processing=still_processing,
        )
        if time.monotonic() >= deadline or (
            not still_processing and attempt >= FRAME_SEARCH_ATTEMPTS
        ):
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: {asset_label} {query!r} did not attach as a chip "
                    f"(expected {expect_count} chip(s) at {chip_selector}; "
                    f"still_processing={still_processing}) — refusing before submit"
                ),
            )
        pause = _VIDEO_INGEST_POLL_S if still_processing else FRAME_SEARCH_RETRY_PAUSE_S
        await asyncio.sleep(pause)


async def _read_overlay_credit_quote(page: Page, *, scope_selector: str) -> int | None:
    try:
        label = await page.locator(f"{scope_selector} flow-credit-cost-label").last.inner_text(
            timeout=2000
        )
    except PlaywrightError:
        return None
    return parse_credit_quote(label)


async def _apply_main_edit_settings(
    composer: MigratedComposer,
    page: Page,
    request: EditVideoRequest,
    *,
    enforce_cap: bool,
) -> int | None:
    """Apply Main Composer video Ingredients settings and optionally check the credit quote."""
    pane = await composer._open_pane(page)
    model = request.model or EDIT_DEFAULT_MODEL
    try:
        await composer._select(page, pane, axis="mode", lig="videocam")
        await composer._select(page, pane, axis="submode", lig=INGREDIENTS_LIGATURE)
        await composer._select_model(page, pane, model)
        if request.aspect is not None:
            await composer._select(page, pane, axis="aspect", lig=ASPECT_LIGATURE[request.aspect])
        if request.resolution is not None:
            await _select_resolution(page, pane, request.resolution)
        await composer._select(page, pane, axis="count", text="x1")
        quoted = await _read_overlay_credit_quote(
            page, scope_selector=".cdk-overlay-pane flow-prompt-box-settings"
        )
        if enforce_cap and request.max_credits is not None:
            quoted = enforce_credit_cap(
                quoted=quoted,
                max_credits=request.max_credits,
                route="flow.google.com video edit settings",
            )
    except BaseException:
        await composer._close_pane(page, strict=False)
        raise
    await composer._close_pane(page, strict=True)
    return quoted


async def _configure_clip_editor_model_and_quote(
    page: Page, *, model: VideoModel, max_credits: int | None
) -> int | None:
    """Select *model* in Clip Editor's dropdown and read the pre-submit credit quote."""
    matcher = VIDEO_MODEL_MENU_MATCHERS.get(model)
    if matcher is None:
        raise ConfigurationError(
            detail=f"no flow.google.com menu matcher for model {model.value!r}"
        )
    trigger = page.locator(CLIP_EDITOR_MODEL_TRIGGER).first
    try:
        await trigger.wait_for(state="visible", timeout=int(ANCHOR_TIMEOUT_S * 1000))
    except PlaywrightTimeoutError as exc:
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: clip editor model selector ({CLIP_EDITOR_MODEL_TRIGGER}) "
                "did not appear (host=migrated)"
            ),
        ) from exc
    current = (await trigger.text_content() or "").strip()
    await trigger.click(timeout=4000)
    items = page.locator(f"{OVERLAY} {MENU_ITEM}")
    try:
        await items.first.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeoutError as exc:
        raise UiSelectorDriftError(
            detail="migrated host: clip editor model menu did not open (host=migrated)",
        ) from exc
    if matcher.matches(current):
        quoted = await _read_overlay_credit_quote(page, scope_selector=OVERLAY)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    else:
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
        await trigger.click(timeout=4000)
        await items.first.wait_for(state="visible", timeout=5000)
        quoted = await _read_overlay_credit_quote(page, scope_selector=OVERLAY)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    if max_credits is not None:
        quoted = enforce_credit_cap(
            quoted=quoted,
            max_credits=max_credits,
            route="flow.google.com clip editor video edit",
        )
    return quoted


async def _submit_edit_and_finish(
    composer: MigratedComposer,
    page: Page,
    request: EditVideoRequest,
    *,
    project_id: str,
    workflow_id: str,
    reference_ids: tuple[str, ...],
    quoted_credits: int | None,
    submit_selector: str | None,
    clip_editor: bool,
    save: ClipSaver,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
) -> VideoResult:
    started_ids: list[str] = []

    def note_started(started: VideoStarted) -> Awaitable[None] | None:
        started_ids.append(started.media_id)
        return on_started(started) if on_started is not None else None

    def check(rpcid: str, body: str) -> GFlowError | None:
        return batch_edit_request_problem(
            [rpcid],
            body,
            workflow_id=workflow_id,
            expected_model=request.model or EDIT_DEFAULT_MODEL,
            expected_resolution=request.resolution,
            expected_aspect=request.aspect,
            expected_reference_ids=reference_ids,
            clip_editor=clip_editor,
        )

    try:
        record: GenerationRecord = await composer.submit_and_observe(
            page,
            poll_timeout_s=poll_timeout_s,
            on_started=note_started,
            project_id=project_id,
            submit_rpcs=(EDIT_SUBMIT_RPC,),
            submit_selector=submit_selector,
            request_check=check,
            adopt_ids=reference_ids,
        )
    except Exception as exc:
        if quoted_credits is not None:
            setattr(exc, "quoted_credits", quoted_credits)  # noqa: B010
        if started_ids:
            setattr(exc, "completed_media_ids", started_ids)  # noqa: B010
            if isinstance(exc, GFlowError):
                exc.charged, exc.next_action = "maybe", "reconcile"
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


async def _validate_edit_references(request: EditVideoRequest) -> None:
    for ref_path in request.reference_images:
        await validate_image_file(ref_path)


async def _run_local_video_edit(
    composer: MigratedComposer,
    page: Page,
    request: EditVideoRequest,
    *,
    project_id: str,
    save: ClipSaver,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
) -> VideoResult:
    assert request.source_video is not None
    with nothing_submitted():
        await _validate_edit_references(request)
        surface = await composer.ensure_editor_for(page, project_id, needs_classic=True)
    if surface == "agent_only":
        raise FlowHostMigratedError(
            detail=(
                "video edit requires the classic migrated composer on flow.google.com; "
                "the agent-only composer is not supported for video edit (#799)"
            ),
        )
    with (
        SubmitWatch(page, rpcs=_EDIT_SUBMIT_WATCH_RPCS) as watch,
        nothing_submitted(sent=watch.seen),
    ):
        uploaded: UploadedVideo = await upload_video(page, project_id, request.source_video)
        ref_uploads: list[tuple[str, str]] = [
            await composer.upload(page, project_id, ref_path)
            for ref_path in request.reference_images
        ]
        await _apply_main_edit_settings(composer, page, request, enforce_cap=False)
        await composer.clear_composer(page)
        await _clear_main_composer_chips(page)
        await _attach_mention_asset(
            page,
            composer_selector=MAIN_COMPOSER_SELECTOR,
            chip_selector=VIDEO_CHIP_SELECTOR,
            query=uploaded.query_name,
            expect_count=1,
            max_wait_s=VIDEO_INGEST_ATTACH_BUDGET_S,
            asset_label="uploaded video",
        )
        for idx, (_ref_id, ref_name) in enumerate(ref_uploads, start=1):
            await _attach_mention_asset(
                page,
                composer_selector=MAIN_COMPOSER_SELECTOR,
                chip_selector=MAIN_MENTION_CHIP_SELECTOR,
                query=ref_name,
                expect_count=idx,
                max_wait_s=30.0,
                asset_label="reference image",
            )
        await composer.send_prompt(page, request.prompt, append=True)
        # Re-open settings after video attachment so the credit quote reflects the
        # attached video's actual duration (Flow locks duration to the source clip).
        quoted = await _apply_main_edit_settings(composer, page, request, enforce_cap=True)
        # Verify video + ref chips survived settings toggle before spending credits.
        if await page.locator(VIDEO_CHIP_SELECTOR).count() < 1:
            raise ReferenceNotFoundError(
                detail="migrated host: source video chip disappeared before submit"
            )
        if len(ref_uploads) and await page.locator(MAIN_MENTION_CHIP_SELECTOR).count() != len(
            ref_uploads
        ):
            raise ReferenceNotFoundError(
                detail="migrated host: reference image chip count changed before submit"
            )
    return await _submit_edit_and_finish(
        composer,
        page,
        request,
        project_id=project_id,
        workflow_id=uploaded.workflow_id,
        reference_ids=tuple(mid for mid, _ in ref_uploads),
        quoted_credits=quoted,
        submit_selector=None,
        clip_editor=False,
        save=save,
        poll_timeout_s=poll_timeout_s,
        download=download,
        on_started=on_started,
    )


async def _run_clip_editor_edit(
    composer: MigratedComposer,
    page: Page,
    request: EditVideoRequest,
    *,
    project_id: str,
    save: ClipSaver,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
) -> VideoResult:
    assert request.source_media_id is not None
    with (
        SubmitWatch(page, rpcs=_EDIT_SUBMIT_WATCH_RPCS) as watch,
        nothing_submitted(sent=watch.seen),
    ):
        await _validate_edit_references(request)
        ref_uploads: list[tuple[str, str]] = []
        if request.reference_images:
            surface = await composer.ensure_editor_for(page, project_id, needs_classic=True)
            if surface == "agent_only":
                raise FlowHostMigratedError(
                    detail=(
                        "uploading reference images for video edit requires the classic composer "
                        "on flow.google.com (#799)"
                    ),
                )
            ref_uploads = [
                await composer.upload(page, project_id, ref_path)
                for ref_path in request.reference_images
            ]
        clip = await _source_clip(
            page,
            project_id=project_id,
            media_id=request.source_media_id,
            resolver=edit_source_record,
        )
        workflow_id = require_video_source(
            clip,
            media_id=request.source_media_id,
            project_id=project_id,
            action="video edit requires a video source",
        )
        _confirm_editor(page, project_id=project_id, media_id=request.source_media_id)
        try:
            await page.locator(CLIP_EDITOR_PROMPT).first.wait_for(
                state="visible", timeout=int(ANCHOR_TIMEOUT_S * 1000)
            )
        except PlaywrightTimeoutError as exc:
            await raise_if_known_landing(
                page, requested=MIGRATED_PROJECT_URL.format(project_id=project_id), at="video_edit"
            )
            raise UiSelectorDriftError(
                detail=(
                    f"migrated host: clip editor prompt box ({CLIP_EDITOR_PROMPT}) did not "
                    f"appear within {ANCHOR_TIMEOUT_S:.0f}s (host=migrated)"
                ),
            ) from exc
        quoted = await _configure_clip_editor_model_and_quote(
            page,
            model=request.model or EDIT_DEFAULT_MODEL,
            max_credits=request.max_credits,
        )
        await _clear_editor_prompt(page, CLIP_EDITOR_PROMPT)
        for idx, (_ref_id, ref_name) in enumerate(ref_uploads, start=1):
            await _attach_mention_asset(
                page,
                composer_selector=CLIP_EDITOR_PROMPT,
                chip_selector=CLIP_EDITOR_MENTION_CHIP,
                query=ref_name,
                expect_count=idx,
                max_wait_s=30.0,
                asset_label="reference image",
            )
        await _caret_to_end(page, CLIP_EDITOR_PROMPT)
        await page.keyboard.insert_text(request.prompt)
        if len(ref_uploads) and await page.locator(CLIP_EDITOR_MENTION_CHIP).count() != len(
            ref_uploads
        ):
            raise ReferenceNotFoundError(
                detail="migrated host: reference image chip count changed before clip-editor submit"
            )
    return await _submit_edit_and_finish(
        composer,
        page,
        request,
        project_id=project_id,
        workflow_id=workflow_id,
        reference_ids=tuple(mid for mid, _ in ref_uploads),
        quoted_credits=quoted,
        submit_selector=CLIP_EDITOR_SUBMIT,
        clip_editor=True,
        save=save,
        poll_timeout_s=poll_timeout_s,
        download=download,
        on_started=on_started,
    )


async def run_video_edit(
    page: Page,
    request: EditVideoRequest,
    *,
    project_id: str | None,
    save: ClipSaver,
    poll_timeout_s: float,
    download: bool,
    on_started: VideoStartedCallback | None,
) -> VideoResult:
    """Drive a single video-to-video edit (local file or existing ``MEDIA_ID``)."""
    pid = project_id or extract_project_id(str(getattr(page, "url", "")))
    if not pid:
        raise ConfigurationError(
            detail=(
                "video edit on flow.google.com needs a project: pass --project <id> "
                "(or let FlowApiClient create a project for a local video file)"
            ),
        )
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
    composer = MigratedComposer()
    log.info(
        "migrated_video_edit.dispatch",
        project_id=pid,
        source_kind="media_id" if request.source_media_id else "local_file",
        ref_count=len(request.reference_images),
    )
    if request.source_video is not None:
        return await _run_local_video_edit(
            composer,
            page,
            request,
            project_id=pid,
            save=save,
            poll_timeout_s=poll_timeout_s,
            download=download,
            on_started=on_started,
        )
    return await _run_clip_editor_edit(
        composer,
        page,
        request,
        project_id=pid,
        save=save,
        poll_timeout_s=poll_timeout_s,
        download=download,
        on_started=on_started,
    )


__all__ = [
    "CLIP_EDITOR_MODEL_TRIGGER",
    "CLIP_EDITOR_PROMPT",
    "CLIP_EDITOR_SUBMIT",
    "EDIT_SUBMIT_RPC",
    "VIDEO_CHIP_SELECTOR",
    "batch_edit_request_problem",
    "edit_request_problem",
    "edit_source_record",
    "run_video_edit",
]
