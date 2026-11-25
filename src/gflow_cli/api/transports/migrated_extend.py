"""Drive Flow's own extend UI on ``flow.google.com`` (design B, #639 / #882 takeover).

The page builds and mints the ``fZytfe`` submit itself; gflow clicks and observes, the
way every other migrated port works. Click path, wire and record shapes are measured in
``docs/superpowers/spikes/2026-10-17-migrated-extend-ui-path.md`` and verified live on
2026-11-24 for local video uploads, ``3..10s`` Omni duration slider, ``0..3`` reference
images, and pre-submit credit quotes:

1. Optional local source video upload via toolbar ``+ -> upload`` and optional ``0..3``
   reference image uploads on the project page.
2. ``goto /project/<pid>/edit/<media>`` — the editor URL renders the extend-capable
   scene builder directly, and its load fires ``Zzl0ze``, whose record maps the media to
   its workflow id and distinguishes generated videos, uploaded videos, and images.
3. Timeline add-clip → the ``keyboard_double_arrow_right`` menu item → optional extend
   settings popover (``3..10s`` duration slider + ``flow-credit-cost-label`` pre-submit
   quote / ``--max-credits`` gate) → optional ``@`` reference mentions → prompt text.
4. Submit: the page sends optional ``rqZuUc`` / ``uwAyfb`` and ``fZytfe`` (the billed
   extend, slot 1 = the WORKFLOW id, slot 2 = ``macaron_cookie_extend_<N>s`` or Veo key,
   slot 8 = attached reference image IDs).

Every ``rqZuUc`` / ``fZytfe`` passes a route guard first: a request for another clip,
wrong duration, wrong aspect, or mismatched reference set is aborted before Flow sees it.
Single segment only; chains, an existing scene and a fixed seed are not ported here yet.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlsplit

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api._flow_workspace import validate_image_file, validate_video_file
from gflow_cli.api.credit_cap import enforce_credit_cap, parse_credit_quote
from gflow_cli.api.dto import GenerationCheckpoint
from gflow_cli.api.transports._common import raise_if_known_landing
from gflow_cli.api.transports._mc_common import (
    _CARET_TO_END_JS,  # pyright: ignore[reportPrivateUsage]
    MENTION_CHIP,
    SubmitWatch,
)
from gflow_cli.api.transports._mc_upload_video import upload_video
from gflow_cli.api.transports.batchexecute import find_record, parse_frames
from gflow_cli.api.transports.migrated_composer import (
    LISTING_RPC,
    MIGRATED_PROJECT_URL,
    MigratedComposer,
    _raise_if_out_of_credits,  # pyright: ignore[reportPrivateUsage]
    request_body,
)
from gflow_cli.api.transports.migrated_recover import MIGRATED_CLIP_URL
from gflow_cli.api.transports.migrated_upscale import is_menu_item_disabled
from gflow_cli.api.video_extend import (
    EXTEND_MAX_DURATION,
    EXTEND_MIN_DURATION,
    MAX_EXTEND_REFERENCE_IMAGES,
    ExtendStarted,
)
from gflow_cli.errors import (
    ConfigurationError,
    ExtendUnavailableError,
    FlowAppError,
    FlowHostMigratedError,
    GFlowError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
    nothing_submitted,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from pathlib import Path

    from playwright.async_api import Page

    from gflow_cli.api.dto import GenerationCheckpointObserver
    from gflow_cli.api.transports.batchexecute import GenerationRecord
    from gflow_cli.api.video import VideoStarted

    #: Fired once Flow accepts the submit (it bills then): the accepted segment and the
    #: aspect the page put in the request.
    ExtendStartedCallback = Callable[[ExtendStarted, str | None], None]

log = structlog.get_logger(__name__)

EXTEND_SUBMIT_RPC = "fZytfe"
SCENE_CREATE_RPC = "rqZuUc"
_EXTEND_SUBMIT_WATCH_RPCS = SubmitWatch._RPCS | {EXTEND_SUBMIT_RPC}  # pyright: ignore[reportPrivateUsage]
#: The page's aspect index in the ``fZytfe`` request entry (measured 2 = 16:9; #882's
#: live capture 1 = 9:16).
ASPECT_BY_INDEX = {1: "9:16", 2: "16:9"}

ADD_CLIP_BUTTON = "flow-scene-timeline button.add-clip-button"
EXTEND_MENU_ITEM = (
    ".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('keyboard_double_arrow_right'))"
)
EXTEND_PROMPT = "flow-edit-video-prompt-box .ProseMirror"
EXTEND_SUBMIT = "flow-edit-video-prompt-box flow-generate-icon-button button"
EXTEND_SETTINGS_TRIGGER = "flow-edit-video-prompt-box button.settings-trigger-button"
EXTEND_SETTINGS_PANE = ".cdk-overlay-pane flow-prompt-box-settings"
EXTEND_CREDIT_QUOTE = "flow-credit-cost-label.settings-credit-cost"
EXTEND_MENTION_CHIP = f"flow-edit-video-prompt-box {MENTION_CHIP}"

#: The editor load's own ``Zzl0ze`` answered within the first seconds on every spike run.
LISTING_BUDGET_S = 30.0
ANCHOR_TIMEOUT_S = 15.0
#: How long a disabled Extend item may take to enable before the run is refused (#1334).
DISABLED_GRACE_S = 3.0
#: How long a re-priced quote label may take to show the chosen duration's price.
QUOTE_SETTLE_S = 3.0
#: Budget for a freshly uploaded local video to finish ingest and enable Extend.
VIDEO_INGEST_READY_BUDGET_S = 90.0
_VIDEO_INGEST_POLL_S = 3.0


@dataclass(frozen=True, slots=True)
class SourceClip:
    """What the passive ``Zzl0ze`` record says about the clip to extend."""

    workflow_id: str
    kind: str  # "video" | "image" | "unknown"
    ready: bool = True


@dataclass(frozen=True, slots=True)
class ExtendOutcome:
    record: GenerationRecord
    video: bytes | None
    model_key: str | None
    aspect: str | None
    scene_id: str | None
    quoted_credits: int | None = None
    source_media_id: str | None = None


# --- refusal matrix ----------------------------------------------------------


def unported_refusal(
    *, segments: int, scene_id: str | None, seed: int | None
) -> FlowHostMigratedError | None:
    """Exit 36 naming what this request asks that the flow.google.com arm does not drive."""
    if segments > 1:
        form = "more than one segment"
    elif scene_id:
        form = "an existing scene"
    elif seed is not None:
        form = "a fixed --seed"
    else:
        return None
    return FlowHostMigratedError(
        detail=(
            f"video extend on flow.google.com drives a single segment from a clip; {form} "
            "is not ported to flow.google.com yet (#639)"
        ),
        remediation_hint=(
            "Run one segment at a time (one PROMPT, no --scene/--resume-from/--seed); "
            "extend the same clip again to go further."
        ),
    )


def require_migrated_host(route: str, *, feature: str = "video extend") -> None:
    """Exit 36 unless *feature* would run on flow.google.com (``route`` is the served route).

    One definition for the transport entry and the worker, which checks it before it
    creates a project.
    """
    if route != "migrated":
        raise FlowHostMigratedError(
            detail=(
                f"migrated {feature} runs on flow.google.com only, and "
                "GFLOW_CLI_FLOW_HOST=labs.google switched that host off"
            ),
            remediation_hint=(
                "Unset GFLOW_CLI_FLOW_HOST (or set it to auto / flow.google.com) "
                f"to run {feature} via flow.google.com."
            ),
        )


# --- media -> workflow + kind --------------------------------------------------


def _at(node: Any, *path: int) -> Any:
    current: Any = node
    for i in path:
        items = cast("list[Any]", current) if isinstance(current, list) else None
        if items is None or i >= len(items):
            return None
        current = items[i]
    return current


def _current_workflow(payload: Any, media_id: str) -> str | None:
    """The workflow the media's own entry (payload slot 1) names as current (slot 3, item 4)."""
    entries = _at(payload, 1)
    for entry in cast("list[Any]", entries) if isinstance(entries, list) else []:
        current = _at(entry, 3, 4)
        if _at(entry, 0) == media_id and isinstance(current, str):
            return current
    return None


def _is_positive_duration_tuple(value: Any) -> bool:
    """True for a protobuf-style video duration list ``[seconds]`` or ``[seconds, nanos]``."""
    if not isinstance(value, list):
        return False
    parts = cast("list[Any]", value)
    if not (1 <= len(parts) <= 2):
        return False
    for part in parts:
        if isinstance(part, bool) or not isinstance(part, (int, float)) or part < 0:
            return False
    return any(part > 0 for part in parts)


def _is_uploaded_video_media_info(rec: list[Any]) -> bool:
    """True when ``rec[7]`` matches a locally uploaded video record in ``Zzl0ze``.

    Measured live 2026-11-24 and spike B3: uploaded videos carry ``rec[7][0] is None`` and
    ``rec[7][1] == [width, height, [duration_s, ...]]`` (with no ``image/*`` mime at
    ``rec[7][1][6]``).
    """
    if _at(rec, 7, 0) is not None:
        return False
    info = _at(rec, 7, 1)
    if not isinstance(info, list):
        return False
    dims = cast("list[Any]", info)
    if len(dims) < 3:
        return False
    return (
        isinstance(dims[0], int)
        and not isinstance(dims[0], bool)
        and dims[0] > 0
        and isinstance(dims[1], int)
        and not isinstance(dims[1], bool)
        and dims[1] > 0
        and _is_positive_duration_tuple(dims[2])
    )


def source_record(payload: Any, media_id: str) -> SourceClip | None:
    """The ``Zzl0ze`` record for ``media_id``: its workflow id (slot 0) and kind.

    Kind: a generated video carries a media-info list at ``rec[7][0]``; a locally
    uploaded video carries ``rec[7][0] is None`` with ``rec[7][1] == [w, h, [3]]``;
    an image carries an ``image/*`` mime at ``rec[7][1][6]``.

    An extended clip has one record per extend under the same media id, in no set order;
    its media entry names the newest, which the page's next extend continues (#1346).

    ``ready`` needs the media metadata to be present (a fresh upload is listed before its
    size and length are) and Flow's processing flag ``rec[5][8] == [1]`` to be clear.
    """
    rec = find_record(payload, (2, media_id))
    if rec is None:
        return None
    current = _current_workflow(payload, media_id)
    head = find_record(payload, (0, current)) if current else None
    if head is not None and head[2] == media_id:
        rec = head
    elif current:  # the guard then expects the first-listed record, which may be stale
        log.warning("migrated_extend.current_workflow_unresolved", media_id=media_id)
    mime = _at(rec, 7, 1, 6)
    if isinstance(mime, str) and mime.startswith("image/"):
        kind = "image"
    elif isinstance(_at(rec, 7, 0), list) or _is_uploaded_video_media_info(rec):
        kind = "video"
    else:
        kind = "unknown"
    ready = kind != "unknown" and _at(rec, 5, 8) != [1]
    return SourceClip(workflow_id=str(rec[0]), kind=kind, ready=ready)


def require_video_source(
    clip: SourceClip | None,
    *,
    media_id: str,
    project_id: str,
    action: str = "extend continues a video clip",
) -> str:
    """The workflow id to extend/edit, or exit 11 before any click."""
    if clip is None:
        raise ConfigurationError(
            detail=(
                f"media {media_id} is not in project {project_id} (Flow's project read "
                "does not list it) — check --project"
            ),
        )
    if clip.kind != "video":
        raise ConfigurationError(
            detail=(
                f"media {media_id} is {'an image' if clip.kind == 'image' else 'not a video'}; "
                f"{action}"
            ),
            remediation_hint=(
                "Pass the media id of a generated video (`gflow data list videos`), pass a "
                "local video file path, or make one from the image first with `gflow video i2v`."
            ),
        )
    return clip.workflow_id


# --- the route guard's verdict ----------------------------------------------


def _frames(body: str) -> list[Any]:
    """Every ``[rpcid, inner, ...]`` frame of a form-decoded ``f.req`` body."""
    start = body.find("f.req=")
    if start < 0:
        return []
    try:
        outer, _ = json.JSONDecoder().raw_decode(body, start + len("f.req="))
    except ValueError:
        return []
    frames = _at(outer, 0)
    return cast("list[Any]", frames) if isinstance(frames, list) else []


def _decode_rpc_frames(rpcid: str, body: str) -> list[Any]:
    """The decoded inner payload of every ``rpcid`` frame (``None`` for an unreadable one)."""
    decoded: list[Any] = []
    for frame in _frames(body):
        if _at(frame, 0) != rpcid:
            continue
        try:
            decoded.append(json.loads(str(_at(frame, 1))))
        except (ValueError, TypeError):
            decoded.append(None)
    return decoded


def _inner(rpcid: str, body: str) -> Any:
    """The decoded inner payload of the first ``rpcid`` frame in an ``f.req`` body, or None."""
    return next(iter(_decode_rpc_frames(rpcid, body)), None)


def url_rpcids(url: str) -> list[str]:
    """Every id in a ``batchexecute`` URL's ``rpcids=`` list (it may batch several)."""
    return [r for v in parse_qs(urlsplit(url).query).get("rpcids", []) for r in v.split(",") if r]


def batch_rpcids(rpcids: list[str], body: str) -> list[str]:
    """``rpcids`` plus the id of every ``f.req`` frame, deduplicated in order."""
    return list(dict.fromkeys([*rpcids, *(str(_at(f, 0)) for f in _frames(body))]))


def extend_key_seconds(model_key: str | None) -> int | None:
    """The length an Omni extend key names (``macaron_cookie_extend_4s`` -> 4; +3.9 s
    measured, #1184), or ``None`` when the key carries no ``_<n>s`` token."""
    m = re.search(r"_extend_(\d+)s(?:_|$)", model_key or "")
    return int(m.group(1)) if m else None


def submit_facts(body: str) -> tuple[str | None, str | None]:
    """(model key, aspect) the page put in its ``fZytfe`` request."""
    # The key is read from its slot, not searched for: the prompt precedes it in the body,
    # and an Omni key (``macaron_cookie_extend_4s``, #1180) matches no shared pattern.
    entry = _at(_inner(EXTEND_SUBMIT_RPC, body or ""), 0, 0)
    key, idx = _at(entry, 2), _at(entry, 3)
    return (
        key if isinstance(key, str) else None,
        ASPECT_BY_INDEX.get(idx) if isinstance(idx, int) else None,
    )


def _single_extend_entry(body: str, *, route: str) -> tuple[Any, GFlowError | None]:
    frames = _decode_rpc_frames(EXTEND_SUBMIT_RPC, body)
    if len(frames) != 1:
        return None, WireFormatError(
            detail=(
                f"migrated host: expected one {EXTEND_SUBMIT_RPC} frame, found {len(frames)} "
                "— refused before Flow acted on it"
            ),
            route=route,
        )
    entries = _at(frames[0], 0)
    if not isinstance(entries, list) or len(cast("list[Any]", entries)) != 1:
        return None, WireFormatError(
            detail=(
                f"migrated host: expected one {EXTEND_SUBMIT_RPC} extend entry — "
                "refused before Flow acted on it"
            ),
            route=route,
        )
    return cast("list[Any]", entries)[0], None


def _extract_reference_ids(entry: Any) -> list[str] | None:
    """The reference ids in ``entry[8]``, in order; ``None`` when any entry is unreadable.

    Fails closed: an entry that does not carry its id at ``[1]`` could be a reference the
    run did not ask for, so the whole slot is reported unreadable rather than skipped.
    """
    raw = _at(entry, 8)
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    ids: list[str] = []
    for item in cast("list[Any]", raw):
        ref_id = _at(item, 1)
        if not isinstance(ref_id, str):
            return None
        ids.append(ref_id)
    return ids


def _check_extend_duration(
    model_key: Any, *, duration: int | None, route: str
) -> GFlowError | None:
    if duration is None:
        return None
    actual = extend_key_seconds(model_key if isinstance(model_key, str) else None)
    if actual != duration:
        return WireFormatError(
            detail=(
                f"migrated host: requested {duration}s extend but {EXTEND_SUBMIT_RPC} "
                f"carried model key {model_key!r} — refused before Flow billed it"
            ),
            route=route,
        )
    return None


def _check_extend_reference_ids(
    entry: Any, *, expected_reference_ids: tuple[str, ...], route: str
) -> GFlowError | None:
    sent = _extract_reference_ids(entry)
    if sent is None:
        return WireFormatError(
            detail=(
                f"migrated host: could not read {EXTEND_SUBMIT_RPC} reference slots — "
                "refused before Flow billed it"
            ),
            route=route,
        )
    expected = list(expected_reference_ids)
    if sent != expected:
        missing = sorted(set(expected) - set(sent))
        extra = sorted(set(sent) - set(expected))
        if missing or extra:
            mismatch = (
                f"missing: {', '.join(missing[:4]) or 'none'}; "
                f"unexpected: {', '.join(extra[:4]) or 'none'}"
            )
        else:
            mismatch = f"expected order {expected[:4]}, got {sent[:4]}"
        return WireFormatError(
            detail=(
                f"migrated host: {EXTEND_SUBMIT_RPC} references did not match the request "
                f"({mismatch}) — refused before Flow billed it"
            ),
            route=route,
        )
    return None


def _check_scene_create(body: str, *, media_id: str, route: str) -> GFlowError | None:
    named = _at(_inner(SCENE_CREATE_RPC, body), 1)
    if not isinstance(named, list) or media_id not in named:
        return WireFormatError(
            detail=(
                f"migrated host: the page's {SCENE_CREATE_RPC} scene request does not name "
                f"the source clip {media_id} — refused before Flow created a scene for another clip"
            ),
            route=route,
        )
    return None


def _check_extend_submit(
    body: str,
    *,
    media_id: str,
    workflow_id: str,
    aspect: str | None,
    duration: int | None,
    expected_reference_ids: tuple[str, ...],
    route: str,
) -> GFlowError | None:
    entry, entry_err = _single_extend_entry(body, route=route)
    if entry_err is not None:
        return entry_err
    found = _at(entry, 0, 1)
    if not isinstance(found, str):
        return WireFormatError(
            detail=(
                f"migrated host: the {EXTEND_SUBMIT_RPC} extend request could not be read, so it "
                "could not be confirmed to continue the requested clip — refused before Flow acted"
            ),
            route=route,
        )
    if found != workflow_id:
        return WireFormatError(
            detail=(
                f"migrated host: the {EXTEND_SUBMIT_RPC} extend request continues workflow "
                f"{found}, not {workflow_id} (the clip {media_id}) — refused before Flow billed it"
            ),
            route=route,
        )
    model_key, page_aspect = submit_facts(body)
    if aspect is not None and page_aspect is not None and page_aspect != aspect:
        return FlowHostMigratedError(
            detail=(
                f"the source clip is {page_aspect} and flow.google.com extends in the source's "
                f"aspect; --aspect {aspect} is not ported to flow.google.com yet — refused "
                "before Flow billed it"
            ),
            remediation_hint="Drop --aspect; the extension keeps the source clip's aspect.",
        )
    return _check_extend_duration(
        model_key, duration=duration, route=route
    ) or _check_extend_reference_ids(
        entry, expected_reference_ids=expected_reference_ids, route=route
    )


def extend_request_problem(  # noqa: PLR0913
    rpcid: str,
    body: str,
    *,
    media_id: str,
    workflow_id: str,
    aspect: str | None,
    duration: int | None = None,
    expected_reference_ids: tuple[str, ...] = (),
) -> GFlowError | None:
    """Why this outgoing request is not the extend the user asked for, or ``None``.

    The verdicts name ids only — never the body, which carries a page-minted token.
    """
    route = f"batchexecute:{rpcid}"
    if rpcid == SCENE_CREATE_RPC:
        return _check_scene_create(body, media_id=media_id, route=route)
    if rpcid != EXTEND_SUBMIT_RPC:
        return None
    return _check_extend_submit(
        body,
        media_id=media_id,
        workflow_id=workflow_id,
        aspect=aspect,
        duration=duration,
        expected_reference_ids=expected_reference_ids,
        route=route,
    )


def batch_request_problem(  # noqa: PLR0913
    rpcids: list[str],
    body: str,
    *,
    media_id: str,
    workflow_id: str,
    aspect: str | None,
    duration: int | None = None,
    expected_reference_ids: tuple[str, ...] = (),
) -> GFlowError | None:
    """The first problem of any rpc this (possibly batched) request carries, or ``None``.

    A batch is judged frame by frame: a wrong ``fZytfe`` behind another rpc
    (``rpcids=uwAyfb,fZytfe``) must not ride through on the first id's verdict.
    """
    for rpcid in batch_rpcids(rpcids, body):
        problem = extend_request_problem(
            rpcid,
            body,
            media_id=media_id,
            workflow_id=workflow_id,
            aspect=aspect,
            duration=duration,
            expected_reference_ids=expected_reference_ids,
        )
        if problem is not None:
            return problem
    return None


# --- the driver -------------------------------------------------------------


async def _release(route: Any, rpcid: str | None) -> None:
    """Let a checked request go on (e2e tests replace this to keep ``fZytfe`` at $0)."""
    del rpcid
    await route.fallback()


async def _anchor(page: Page, selector: str, *, named: str) -> Any:
    locator = page.locator(selector).first
    try:
        await locator.wait_for(state="visible", timeout=int(ANCHOR_TIMEOUT_S * 1000))
    except PlaywrightTimeoutError as exc:
        await raise_if_known_landing(page, requested="the clip editor", at="migrated_extend")
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: the {named} ({selector}) did not appear within "
                f"{ANCHOR_TIMEOUT_S:.0f}s (host=migrated)"
            ),
        ) from exc
    return locator


async def _refuse_if_disabled(page: Page, item: Any) -> None:
    """Exit 37 or 35, before any click, when Flow keeps the Extend item disabled (#1334).

    Measured 2026-11-18 on an account holding 12 credits: ``disabled="true"``, a link to
    Google's AI plans beside it and no credits warning, while a funded account's item was
    enabled. The page names no cause, so the error names the item and both remedies. A
    short grace period keeps an item that is enabled a moment late from being refused.
    A fresh upload reaches this only once Flow's project read lists it ready.
    """
    deadline = asyncio.get_running_loop().time() + DISABLED_GRACE_S
    while await is_menu_item_disabled(item):
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.25)
    else:
        return
    await page.keyboard.press("Escape")  # leave no open menu on a pooled page
    await _raise_if_out_of_credits(page)
    raise ExtendUnavailableError(
        detail=(
            "migrated host: Flow shows the Extend menu item disabled on this account, so "
            "extend cannot start — nothing was submitted (host=migrated)"
        ),
        remediation_hint=(
            "Flow shows a Google AI plan upgrade link beside the disabled item; the plan or "
            "the balance may gate it. Check `gflow credits user`, then upgrade the plan or "
            "top up, or extend from an account where Flow enables Extend."
        ),
    )


def _listing_clip(text: str, media_id: str) -> tuple[bool, SourceClip | None]:
    """(whether any ``Zzl0ze`` frame was in ``text``, the first one holding ``media_id``)."""
    seen = False
    for rid, payload in parse_frames(text):
        if rid != LISTING_RPC:
            continue
        seen = True
        clip = source_record(payload, media_id)
        if clip is not None:
            return True, clip
    return seen, None


async def _source_clip(page: Page, *, project_id: str, media_id: str) -> SourceClip | None:
    """Open the clip editor and read its own ``Zzl0ze`` replies (no request of ours).

    Settles on the reply that holds ``media_id``; at the budget, ``None`` if a read
    answered without it (exit 11), a timeout if none answered at all (exit 9).
    """
    found: asyncio.Future[SourceClip] = asyncio.get_running_loop().create_future()
    seen = False

    async def on_response(response: Any) -> None:
        nonlocal seen
        # Matched on the whole URL, not the first `rpcids` entry: the read can be batched
        # with others (`rpcids=A,Zzl0ze`), and parse_frames picks its frame out.
        if found.done() or LISTING_RPC not in str(getattr(response, "url", "")):
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 - an aborted body is not our frame
            return
        listed, clip = _listing_clip(text, media_id)
        seen = seen or listed
        if clip is not None and not found.done():
            found.set_result(clip)

    page.on("response", on_response)
    try:
        await page.goto(
            MIGRATED_CLIP_URL.format(project_id=project_id, media_id=media_id),
            wait_until="domcontentloaded",
        )
        return await asyncio.wait_for(found, LISTING_BUDGET_S)
    except TimeoutError as exc:
        if seen:
            return None
        await _raise_if_project_missing(page, project_id)
        raise TransportTimeoutError(
            detail=(
                f"migrated host: the clip editor's project read ({LISTING_RPC}) did not "
                f"answer within {LISTING_BUDGET_S:.0f}s — nothing was clicked or submitted"
            ),
        ) from exc
    finally:
        page.remove_listener("response", on_response)
        if not found.done():
            found.cancel()


def _ingest_timeout(state: str, *, media_id: str, project_id: str) -> TransportTimeoutError:
    """Exit 9 for a clip uploaded in this run that Flow had not finished processing."""
    return TransportTimeoutError(
        detail=(
            f"migrated host: the uploaded clip {media_id} was still processing "
            f"{VIDEO_INGEST_READY_BUDGET_S:.0f}s after the upload ({state}) — nothing was "
            "submitted"
        ),
        remediation_hint=(
            f"Flow keeps the upload in project {project_id} as media {media_id}. Re-run "
            f"later with that id: `gflow video extend {media_id} PROMPT --project "
            f"{project_id}`."
        ),
    )


async def _load_source_clip(
    page: Page, *, project_id: str, media_id: str, ingest_deadline: float | None
) -> SourceClip | None:
    """Open the clip editor and read ``media_id``'s record.

    For a clip uploaded in this run (``ingest_deadline`` set), re-read until it is listed
    ready; still not ready at the deadline is a timeout, never returned.
    """
    while True:
        clip = await _source_clip(page, project_id=project_id, media_id=media_id)
        if ingest_deadline is None or (clip is not None and clip.ready):
            return clip
        if time.monotonic() >= ingest_deadline:
            raise _ingest_timeout(
                "Flow's project read did not list it ready",
                media_id=media_id,
                project_id=project_id,
            )
        log.info(
            "migrated_extend.upload_ingest_wait",
            media_id=media_id,
            ready=clip.ready if clip is not None else False,
        )
        await asyncio.sleep(_VIDEO_INGEST_POLL_S)


async def _raise_if_project_missing(page: Page, project_id: str) -> None:
    """After the listing timed out: is the project one Flow will not open (#1053)?

    The clip-editor URL never redirects for such a project (it sat unchanged for 30 s,
    2026-10-23, ffroliva), but the project page goes to ``/404?reason=project``. So
    this one navigation runs only in the failure branch, and any probe error leaves
    the caller's timeout standing.
    """
    try:
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=project_id),
            wait_until="domcontentloaded",
            timeout=10_000,  # a probe: bounded, not Playwright's 30 s default
        )
        await page.wait_for_url(
            lambda url: urlsplit(url).path.rstrip("/") == "/404", timeout=10_000
        )
    except Exception:  # noqa: BLE001 - a probe; the caller's own error stands
        return
    await raise_if_known_landing(page, requested=f"project {project_id}", at="migrated_extend")


def _confirm_editor(page: Page, *, project_id: str, media_id: str) -> None:
    path = urlsplit(str(page.url)).path
    if path.rstrip("/") != f"/project/{project_id}/edit/{media_id}":
        raise FlowAppError(
            detail=(
                f"migrated host: opening the clip editor for {media_id} landed on {path!r}, "
                "not the editor — nothing was clicked or submitted"
            ),
            retryable=False,
            charged="no",
        )


# --- extend settings (duration slider + pre-submit quote) --------------------

_CAP_ROUTE = "flow.google.com video extend"


async def _set_extend_duration_slider(pane: Any, duration: int) -> bool:
    """Set the popover's ``flow-duration-slider`` to *duration* seconds and verify it.

    Returns whether the value moved (the quote label then has to catch up).
    """
    component = pane.locator("flow-duration-slider")
    slider = component.locator('input[type="range"]')
    try:
        await slider.first.wait_for(state="visible", timeout=4000)
    except PlaywrightError as exc:
        raise UiSelectorDriftError(
            detail=(
                "migrated host: extend duration slider (flow-duration-slider) did not appear "
                "(host=migrated)"
            ),
        ) from exc
    if await slider.count() != 1:
        raise UiSelectorDriftError(detail="migrated host: extend duration slider ambiguous")
    min_raw = await slider.get_attribute("min")
    max_raw = await slider.get_attribute("max")
    step_raw = await slider.get_attribute("step")
    if (
        step_raw != "1"
        or not (min_raw and min_raw.isdigit())
        or not (max_raw and max_raw.isdigit())
    ):
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: extend duration slider range changed "
                f"(min={min_raw!r}, max={max_raw!r}, step={step_raw!r})"
            ),
        )
    min_val, max_val = int(min_raw), int(max_raw)
    if not (min_val <= duration <= max_val) or not await slider.is_enabled():
        raise ConfigurationError(
            detail=(
                f"extend duration {duration}s is not offered by this account ({min_val}-{max_val}s)"
            ),
            remediation_hint=f"Pass --duration between {min_val} and {max_val}, or omit it.",
        )
    if await slider.input_value() == str(duration):
        return False
    await slider.press("Home")
    for _ in range(duration - min_val):
        await slider.press("ArrowRight")
    await asyncio.sleep(0.2)
    actual_val = await slider.input_value()
    label = await component.inner_text()
    if actual_val != str(duration) or not re.search(rf"(?<!\d){duration}(?!\d)", label):
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: extend duration slider did not bind {duration}s "
                f"(value={actual_val!r}, label={label!r})"
            ),
        )
    return True


async def _read_extend_credit_quote(pane: Any) -> int | None:
    for selector in (EXTEND_CREDIT_QUOTE, "flow-credit-cost-label"):
        try:
            label = await pane.locator(selector).last.inner_text(timeout=2000)
        except PlaywrightError:
            continue
        parsed = parse_credit_quote(label)
        if parsed is not None:
            return parsed
    return None


async def _settled_quote(
    pane: Any, *, before: int | None, duration: int | None, moved: bool
) -> int | None:
    """The quote for the chosen duration, or ``None`` if it cannot be confirmed.

    A moved slider re-prices asynchronously: a changed label is taken at once, and an
    unchanged one (two lengths can cost the same) after :data:`QUOTE_SETTLE_S`. Either
    way the slider must still show *duration*, so a label is never trusted for a length
    that is not the one set.
    """
    deadline = asyncio.get_running_loop().time() + QUOTE_SETTLE_S
    while True:
        quoted = await _read_extend_credit_quote(pane)
        settled = asyncio.get_running_loop().time() >= deadline
        if quoted is not None and not moved:
            return quoted
        if (
            quoted is not None
            and (before is None or quoted != before or settled)
            and await _slider_shows(pane, duration)
        ):
            return quoted
        if settled:
            return None
        await asyncio.sleep(0.25)


async def _slider_shows(pane: Any, duration: int | None) -> bool:
    slider = pane.locator("flow-duration-slider").locator('input[type="range"]')
    try:
        return await slider.input_value() == str(duration)
    except PlaywrightError:
        return False


async def _close_extend_settings_pane(page: Page, *, strict: bool) -> None:
    pane = page.locator(EXTEND_SETTINGS_PANE)
    for _ in range(3):
        try:
            if not await pane.count() or not await pane.last.is_visible():
                return
        except PlaywrightError:
            return
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.25)
    if strict:
        raise UiSelectorDriftError(
            detail=(
                "migrated host: extend settings pane stayed open after Escape and would block "
                "the prompt box (host=migrated)"
            ),
        )


async def _open_extend_settings_pane(page: Page) -> Any:
    """Open the extend settings popover, or exit 23 naming the control that failed."""
    trigger = await _anchor(page, EXTEND_SETTINGS_TRIGGER, named="extend settings trigger")
    pane = page.locator(EXTEND_SETTINGS_PANE).last
    try:
        await trigger.click(timeout=4000)
        await pane.wait_for(state="visible", timeout=5000)
    except PlaywrightError as exc:
        await _close_extend_settings_pane(page, strict=False)
        raise UiSelectorDriftError(
            detail=(
                f"migrated host: extend settings pane ({EXTEND_SETTINGS_PANE}) did not open "
                "(host=migrated)"
            ),
        ) from exc
    return pane


async def _configure_extend_settings(
    page: Page, *, duration: int | None, max_credits: int | None
) -> int | None:
    """Set *duration* and read the quote in the settings popover; enforce *max_credits*.

    Not opened at all when neither is asked for. Run after the references are attached,
    so the quote prices the request as it will be sent. A pane gflow cannot drive is exit
    23, or exit 45 when only the cap needed it (the quote could not be verified).
    """
    if duration is None and max_credits is None:
        return None
    try:
        pane = await _open_extend_settings_pane(page)
    except UiSelectorDriftError:
        if duration is None:
            enforce_credit_cap(quoted=None, max_credits=max_credits, route=_CAP_ROUTE)
        raise
    try:
        before = await _read_extend_credit_quote(pane)
        moved = await _set_extend_duration_slider(pane, duration) if duration else False
        quoted = await _settled_quote(pane, before=before, duration=duration, moved=moved)
        log.info("migrated_extend.credit_quote_checked", quoted=quoted, max_credits=max_credits)
        quoted = enforce_credit_cap(quoted=quoted, max_credits=max_credits, route=_CAP_ROUTE)
    except PlaywrightError as exc:
        await _close_extend_settings_pane(page, strict=False)
        if max_credits is not None:
            enforce_credit_cap(quoted=None, max_credits=max_credits, route=_CAP_ROUTE)
        raise UiSelectorDriftError(
            detail="migrated host: the extend settings pane could not be driven (host=migrated)",
        ) from exc
    except BaseException:
        await _close_extend_settings_pane(page, strict=False)
        raise
    await _close_extend_settings_pane(page, strict=True)
    return quoted


# --- reference attachment in extend prompt box -------------------------------


async def _prepare_extend_prompt(
    composer: MigratedComposer, page: Page, *, prompt: str, ref_names: tuple[str, ...]
) -> None:
    """Attach each uploaded reference as an ``@`` mention, then type the prompt after them."""
    prompt_box = await _anchor(page, EXTEND_PROMPT, named="extend prompt box")
    await prompt_box.click()
    for idx, name in enumerate(ref_names, start=1):
        await composer._mention_by_name(  # pyright: ignore[reportPrivateUsage]
            page, name, expect_chips=idx, composer=EXTEND_PROMPT, chip=EXTEND_MENTION_CHIP
        )
    if ref_names:
        await page.locator(EXTEND_PROMPT).first.click(timeout=5000)
        await page.evaluate(_CARET_TO_END_JS, EXTEND_PROMPT)
    await page.keyboard.insert_text(prompt)


# --- input validation & upload preparation -----------------------------------


_INPUT_HINT = "Correct the extend inputs and re-run; nothing was uploaded or charged."


async def validate_extend_inputs(
    *,
    media_id: str | None,
    source_video: Path | None,
    duration: int | None,
    reference_images: tuple[Path, ...],
    max_credits: int | None,
) -> None:
    """Exit 11 (nothing charged) for any input Flow must not be sent, before any project.

    Called at each edge (CLI, worker) and again at the transport entry.
    """
    if bool(media_id) == bool(source_video is not None):
        raise ConfigurationError(
            detail="provide exactly one of media_id or source_video", remediation_hint=_INPUT_HINT
        )
    if duration is not None and not (EXTEND_MIN_DURATION <= duration <= EXTEND_MAX_DURATION):
        raise ConfigurationError(
            detail=(
                f"extend duration must be between {EXTEND_MIN_DURATION} and "
                f"{EXTEND_MAX_DURATION}s, got {duration}"
            ),
            remediation_hint=_INPUT_HINT,
        )
    if len(reference_images) > MAX_EXTEND_REFERENCE_IMAGES:
        raise ConfigurationError(
            detail=(
                f"video extend accepts at most {MAX_EXTEND_REFERENCE_IMAGES} reference images "
                f"(got {len(reference_images)})"
            ),
            remediation_hint=_INPUT_HINT,
        )
    if max_credits is not None and max_credits < 0:
        raise ConfigurationError(
            detail=f"max_credits must be >= 0, got {max_credits}", remediation_hint=_INPUT_HINT
        )
    await validate_upload_files(source_video, reference_images)


async def validate_upload_files(
    source_video: Path | None, reference_images: tuple[Path, ...]
) -> None:
    """Exit 11 (nothing charged) for a local clip or reference image Flow must not be sent."""
    try:
        if source_video is not None:
            await validate_video_file(source_video)
        for ref_path in reference_images:
            await validate_image_file(ref_path)
    except (ValueError, OSError) as exc:  # incl. a missing, unreadable or folder path
        raise ConfigurationError(
            detail=f"cannot upload this file: {exc}",
            remediation_hint=(
                "Pass an existing .mp4, .m4v, .mov, .3gp or .avi video of up to 1 GB, and "
                "image files as --ref. Nothing was uploaded or charged."
            ),
        ) from exc


async def _upload_extend_inputs(
    composer: MigratedComposer,
    page: Page,
    *,
    project_id: str,
    source_video: Path | None,
    reference_images: tuple[Path, ...],
) -> tuple[str | None, tuple[tuple[str, str], ...]]:
    """Upload the local clip and references: ``(clip media id, ((ref id, name), ...))``."""
    if source_video is None and not reference_images:
        return None, ()
    if await composer.ensure_editor_for(page, project_id, needs_classic=True) == "agent_only":
        raise FlowHostMigratedError(
            detail=(
                "Flow served the agent-only composer (#799), which has no upload entry; "
                "video extend from local files is not ported to it yet"
            ),
            remediation_hint=(
                "Extend a clip already in the project by its media id, or re-run later: "
                "Flow serves the classic composer on most loads."
            ),
        )
    uploaded = await upload_video(page, project_id, source_video) if source_video else None
    refs = [await composer.upload(page, project_id, ref) for ref in reference_images]
    return uploaded, tuple(refs)


# --- route guard plumbing ----------------------------------------------------


def _request_verdict(  # noqa: PLR0913
    rpcids: list[str],
    body: str,
    *,
    media_id: str,
    workflow_id: str,
    aspect: str | None,
    duration: int | None,
    expected_reference_ids: tuple[str, ...],
    released_submits: int,
) -> GFlowError | None:
    """Why one intercepted ``batchexecute`` request must be aborted, or ``None``."""
    verdict = batch_request_problem(
        rpcids,
        body,
        media_id=media_id,
        workflow_id=workflow_id,
        aspect=aspect,
        duration=duration,
        expected_reference_ids=expected_reference_ids,
    )
    if verdict is None and EXTEND_SUBMIT_RPC in rpcids and released_submits:
        # One billed extend per run: a second fZytfe would bill a second clip.
        verdict = WireFormatError(
            detail=f"migrated host: a second {EXTEND_SUBMIT_RPC} in one run — refused",
            route=f"batchexecute:{EXTEND_SUBMIT_RPC}",
        )
    return verdict


def _released_rpc(rpcids: list[str], *, is_submit: bool) -> str | None:
    """The rpc id a released request is reported to ``_release`` under."""
    if is_submit:
        return EXTEND_SUBMIT_RPC
    return rpcids[0] if rpcids else None


def _route_guard(  # noqa: PLR0913
    *,
    media_id: str,
    workflow_id: str,
    aspect: str | None,
    duration: int | None,
    expected_reference_ids: tuple[str, ...],
    facts: dict[str, str | None],
) -> Callable[[Any, Any], Awaitable[None]]:
    """The ``page.route`` handler: abort a wrong request, release a right one."""
    released_submits = 0

    async def guard(route: Any, request: Any) -> None:
        nonlocal released_submits
        body = request_body(request)
        rpcids = batch_rpcids(url_rpcids(str(getattr(request, "url", ""))), body)
        verdict = _request_verdict(
            rpcids,
            body,
            media_id=media_id,
            workflow_id=workflow_id,
            aspect=aspect,
            duration=duration,
            expected_reference_ids=expected_reference_ids,
            released_submits=released_submits,
        )
        is_submit = EXTEND_SUBMIT_RPC in rpcids
        if verdict is not None:
            await route.abort()
            log.warning(
                "migrated_extend.request_refused", rpcs=rpcids, error=type(verdict).__name__
            )
            return
        if is_submit:
            released_submits += 1
            facts["model_key"], facts["aspect"] = submit_facts(body)
        await _release(route, _released_rpc(rpcids, is_submit=is_submit))

    return guard


def _is_batchexecute(url: str) -> bool:
    return "batchexecute" in url


def _pre_submit_guard(
    held: list[str], rpcs: frozenset[str] = frozenset({EXTEND_SUBMIT_RPC})
) -> Callable[[Any, Any], Awaitable[None]]:
    """While gflow prepares the request, no billed submit (*rpcs*) may leave the page.

    A stray keystroke (an Enter that lands with no picker open) submits the generation
    before the references, the settings and the credit cap are in place. Such a submit is
    aborted and recorded in ``held``; only the checked submit after preparation is let
    through.
    """

    async def hold(route: Any, request: Any) -> None:
        body = request_body(request)
        sent = set(batch_rpcids(url_rpcids(str(getattr(request, "url", ""))), body)) & rpcs
        if sent:
            held.extend(sorted(sent))
            await route.abort()
            log.warning("migrated.early_submit_aborted", rpcs=sorted(sent))
            return
        await route.fallback()

    return hold


@contextlib.asynccontextmanager
async def holding_early_submits(
    page: Page, *, rpcs: frozenset[str], noun: str
) -> AsyncGenerator[None]:
    """Abort any billed submit (*rpcs*) the page sends while the body prepares the request.

    One aborted early submit fails the run afterwards (``WireFormatError``, charged ``no``).
    """
    held: list[str] = []
    hold = _pre_submit_guard(held, rpcs)
    await page.route(_is_batchexecute, hold)
    try:
        yield
    finally:
        try:
            await page.unroute(_is_batchexecute, hold)
        except Exception as exc:  # noqa: BLE001 - must not mask the real outcome
            log.debug("migrated.unroute_failed", error=type(exc).__name__)
    if held:
        early = WireFormatError(
            detail=(
                f"migrated host: the page sent {held[0]} while gflow was still preparing the "
                f"{noun}; it was aborted before Flow saw it (host=migrated)"
            ),
            route=f"batchexecute:{held[0]}",
            remediation_hint="Nothing was submitted or charged; re-run the command.",
        )
        early.charged = "no"
        raise early


def _started_handler(
    *,
    workflow_id: str,
    facts: dict[str, str | None],
    on_started: ExtendStartedCallback | None,
    on_checkpoint: GenerationCheckpointObserver | None = None,
) -> Callable[[VideoStarted], None]:
    """The composer's ``on_started``: refuse a reply naming the source, then report it."""

    def started(info: VideoStarted) -> None:
        if info.workflow_id == workflow_id:
            raise WireFormatError(
                detail=(
                    f"migrated host: Flow's {EXTEND_SUBMIT_RPC} reply names the source clip's "
                    "own workflow, not a new generation — Flow may still have billed it"
                ),
                route=f"batchexecute:{EXTEND_SUBMIT_RPC}",
            )
        if not facts:
            log.warning("migrated_extend.no_submit_facts", workflow_id=info.workflow_id)
        if on_checkpoint is not None:
            on_checkpoint(
                GenerationCheckpoint(
                    phase="remote_started",
                    operation_id=info.media_id,
                    media_ids=(info.media_id,),
                    workflow_ids=(info.workflow_id,) if info.workflow_id else (),
                )
            )
        if on_started is not None:
            # The guard read the page's own fZytfe before this reply arrived.
            accepted = ExtendStarted(
                media_id=info.media_id,
                workflow_id=info.workflow_id or "",
                model_key=facts.get("model_key") or "unknown",
            )
            on_started(accepted, facts.get("aspect"))

    return started


async def _prepare_clip_extend(  # noqa: PLR0913
    composer: MigratedComposer,
    page: Page,
    *,
    project_id: str,
    media_id: str | None,
    source_video: Path | None,
    prompt: str,
    duration: int | None,
    reference_images: tuple[Path, ...],
    max_credits: int | None,
) -> tuple[str, str, tuple[str, ...], int | None]:
    """Upload optional inputs, open the clip editor, apply prompt, refs & settings (pre-submit)."""
    uploaded, ref_uploads = await _upload_extend_inputs(
        composer,
        page,
        project_id=project_id,
        source_video=source_video,
        reference_images=reference_images,
    )
    resolved_media_id = uploaded or str(media_id)
    # One processing budget for a fresh upload, from the end of the upload: the listing
    # and the timeline share it, so the total wait is what the timeout message says.
    ingest_deadline = time.monotonic() + VIDEO_INGEST_READY_BUDGET_S if uploaded else None
    clip = await _load_source_clip(
        page,
        project_id=project_id,
        media_id=resolved_media_id,
        ingest_deadline=ingest_deadline,
    )
    workflow_id = require_video_source(clip, media_id=resolved_media_id, project_id=project_id)
    _confirm_editor(page, project_id=project_id, media_id=resolved_media_id)
    log.info("migrated_extend.source", media_id=resolved_media_id, workflow_id=workflow_id)
    while True:
        try:
            add_clip_btn = await _anchor(page, ADD_CLIP_BUTTON, named="timeline add-clip button")
            break
        except UiSelectorDriftError:
            if ingest_deadline is None:
                raise
            if time.monotonic() >= ingest_deadline:
                raise _ingest_timeout(
                    "the timeline offered no add-clip button",
                    media_id=resolved_media_id,
                    project_id=project_id,
                ) from None
            log.info("migrated_extend.upload_timeline_wait", media_id=resolved_media_id)
            await asyncio.sleep(_VIDEO_INGEST_POLL_S)
    await add_clip_btn.click()
    extend = await _anchor(page, EXTEND_MENU_ITEM, named="Extend menu item")
    # A click on a disabled item would wait out 30 s (#1334).
    await _refuse_if_disabled(page, extend)
    await extend.click()
    await _prepare_extend_prompt(
        composer, page, prompt=prompt, ref_names=tuple(name for _, name in ref_uploads)
    )
    # After the references: the quote must price the request that will be sent.
    quoted = await _configure_extend_settings(page, duration=duration, max_credits=max_credits)
    expected_ref_ids = tuple(ref_id for ref_id, _ in ref_uploads)
    return resolved_media_id, workflow_id, expected_ref_ids, quoted


async def run_extend(  # noqa: PLR0913
    page: Page,
    *,
    project_id: str,
    media_id: str | None = None,
    source_video: Path | None = None,
    prompt: str,
    aspect: str | None,
    poll_timeout_s: float,
    duration: int | None = None,
    reference_images: tuple[Path, ...] = (),
    max_credits: int | None = None,
    on_started: ExtendStartedCallback | None = None,
    on_checkpoint: GenerationCheckpointObserver | None = None,
) -> ExtendOutcome:
    """Extend ``media_id`` (or an uploaded local ``source_video``) by one segment via Flow UI.

    Inputs are validated by the caller (:func:`validate_extend_inputs`).
    """
    composer = MigratedComposer()
    async with holding_early_submits(page, rpcs=frozenset({EXTEND_SUBMIT_RPC}), noun="extend"):
        # #1320: the submit inside submit_and_observe is the first send; watch for strays.
        with (
            SubmitWatch(page, rpcs=_EXTEND_SUBMIT_WATCH_RPCS) as watch,
            nothing_submitted(sent=watch.seen),
        ):
            resolved_media_id, workflow_id, expected_ref_ids, quoted = await _prepare_clip_extend(
                composer,
                page,
                project_id=project_id,
                media_id=media_id,
                source_video=source_video,
                prompt=prompt,
                duration=duration,
                reference_images=reference_images,
                max_credits=max_credits,
            )

    facts: dict[str, str | None] = {}

    def check(rpcid: str, body: str) -> GFlowError | None:
        return batch_request_problem(
            [rpcid],
            body,
            media_id=resolved_media_id,
            workflow_id=workflow_id,
            aspect=aspect,
            duration=duration,
            expected_reference_ids=expected_ref_ids,
        )

    guard = _route_guard(
        media_id=resolved_media_id,
        workflow_id=workflow_id,
        aspect=aspect,
        duration=duration,
        expected_reference_ids=expected_ref_ids,
        facts=facts,
    )
    started = _started_handler(
        workflow_id=workflow_id,
        facts=facts,
        on_started=on_started,
        on_checkpoint=on_checkpoint,
    )

    if on_checkpoint is not None:
        # Everything before this point is pre-submit (charged "no"); only the click is next.
        on_checkpoint(GenerationCheckpoint(phase="submit_attempted"))
    await page.route(_is_batchexecute, guard)
    try:
        record = await composer.submit_and_observe(
            page,
            poll_timeout_s=poll_timeout_s,
            on_started=started,
            project_id=project_id,
            submit_rpcs=(EXTEND_SUBMIT_RPC,),
            submit_selector=EXTEND_SUBMIT,
            request_check=check,
        )
    finally:
        try:
            await page.unroute(_is_batchexecute, guard)
        except Exception as exc:  # noqa: BLE001 - must not mask the real outcome
            log.debug("migrated_extend.unroute_failed", error=type(exc).__name__)
    url = str(page.url)
    scene_id = url.split("/scene/", 1)[1].split("?")[0] if "/scene/" in url else None
    video = await MigratedComposer.clip_bytes(page, record) if record.is_done else None
    return ExtendOutcome(
        record=record,
        video=video,
        model_key=facts.get("model_key"),
        aspect=facts.get("aspect"),
        scene_id=scene_id,
        quoted_credits=quoted,
        source_media_id=resolved_media_id,
    )


__all__ = [
    "EXTEND_CREDIT_QUOTE",
    "EXTEND_MAX_DURATION",
    "EXTEND_MENTION_CHIP",
    "EXTEND_MIN_DURATION",
    "EXTEND_SETTINGS_PANE",
    "EXTEND_SETTINGS_TRIGGER",
    "EXTEND_SUBMIT_RPC",
    "MAX_EXTEND_REFERENCE_IMAGES",
    "ExtendOutcome",
    "SourceClip",
    "batch_request_problem",
    "extend_key_seconds",
    "extend_request_problem",
    "holding_early_submits",
    "require_migrated_host",
    "require_video_source",
    "run_extend",
    "source_record",
    "submit_facts",
    "unported_refusal",
    "validate_extend_inputs",
    "validate_upload_files",
]
