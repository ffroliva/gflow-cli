"""Drive Flow's own extend UI on ``flow.google.com`` (design B, #639 / #882 takeover).

The page builds and mints the ``fZytfe`` submit itself; gflow clicks and observes, the
way every other migrated port works. Click path, wire and record shapes are measured in
``docs/superpowers/spikes/2026-10-17-migrated-extend-ui-path.md``:

1. ``goto /project/<pid>/edit/<media>`` — the editor URL renders the extend-capable
   scene builder directly, and its load fires ``Zzl0ze``, whose record maps the media to
   its workflow id and tells a video from an image (round 2, B1/B3/B4).
2. timeline add-clip → the ``keyboard_double_arrow_right`` menu item → type the prompt.
3. submit: the page sends ``rqZuUc`` (creates a scene from the MEDIA id), ``uwAyfb``
   (reads it back) and ``fZytfe`` (the billed extend, slot 1 = the WORKFLOW id).

Every ``rqZuUc`` / ``fZytfe`` passes a route guard first: a request for another clip is
aborted before Flow sees it. Single segment only; chains, an existing scene and a fixed
seed are not ported here yet.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlsplit

import structlog
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.transports._common import raise_if_known_landing
from gflow_cli.api.transports.batchexecute import find_record, parse_frames
from gflow_cli.api.transports.migrated_composer import (
    LISTING_RPC,
    MIGRATED_PROJECT_URL,
    MODEL_KEY,
    MigratedComposer,
    request_body,
)
from gflow_cli.api.transports.migrated_recover import MIGRATED_CLIP_URL
from gflow_cli.api.video_extend import ExtendStarted
from gflow_cli.errors import (
    ConfigurationError,
    FlowAppError,
    FlowHostMigratedError,
    GFlowError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Awaitable, Callable

    from playwright.async_api import Page

    from gflow_cli.api.transports.batchexecute import GenerationRecord
    from gflow_cli.api.video import VideoStarted

    #: Fired once Flow accepts the submit (it bills then): the accepted segment and the
    #: aspect the page put in the request.
    ExtendStartedCallback = Callable[[ExtendStarted, str | None], None]

log = structlog.get_logger(__name__)

EXTEND_SUBMIT_RPC = "fZytfe"
SCENE_CREATE_RPC = "rqZuUc"
#: The page's aspect index in the ``fZytfe`` request entry (measured 2 = 16:9; #882's
#: live capture 1 = 9:16).
ASPECT_BY_INDEX = {1: "9:16", 2: "16:9"}

ADD_CLIP_BUTTON = "flow-scene-timeline button.add-clip-button"
EXTEND_MENU_ITEM = (
    ".cdk-overlay-pane [role='menuitem']:has(mat-icon:text-is('keyboard_double_arrow_right'))"
)
EXTEND_PROMPT = "flow-edit-video-prompt-box .ProseMirror"
EXTEND_SUBMIT = "flow-edit-video-prompt-box flow-generate-icon-button button"

#: The editor load's own ``Zzl0ze`` answered within the first seconds on every spike run.
LISTING_BUDGET_S = 30.0
ANCHOR_TIMEOUT_S = 15.0


@dataclass(frozen=True, slots=True)
class SourceClip:
    """What the passive ``Zzl0ze`` record says about the clip to extend."""

    workflow_id: str
    kind: str  # "video" | "image" | "unknown"


@dataclass(frozen=True, slots=True)
class ExtendOutcome:
    record: GenerationRecord
    video: bytes | None
    model_key: str | None
    aspect: str | None
    scene_id: str | None


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
            "extend the new clip again to go further."
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


def source_record(payload: Any, media_id: str) -> SourceClip | None:
    """The ``Zzl0ze`` record for ``media_id``: its workflow id (slot 0) and kind.

    Kind (spike round 2, B3): a video carries a media-info list at ``rec[7][0]``; an
    image carries an ``image/*`` mime at ``rec[7][1][6]``.
    """
    rec = find_record(payload, (2, media_id))
    if rec is None:
        return None
    mime = _at(rec, 7, 1, 6)
    if isinstance(mime, str) and mime.startswith("image/"):
        kind = "image"
    elif isinstance(_at(rec, 7, 0), list):
        kind = "video"
    else:
        kind = "unknown"
    return SourceClip(workflow_id=str(rec[0]), kind=kind)


def require_video_source(clip: SourceClip | None, *, media_id: str, project_id: str) -> str:
    """The workflow id to extend, or exit 11 before any click."""
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
                "extend continues a video clip"
            ),
            remediation_hint=(
                "Pass the media id of a generated video (`gflow data list videos`), or make "
                "one from the image first with `gflow video i2v`."
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


def _inner(rpcid: str, body: str) -> Any:
    """The decoded inner payload of ``rpcid`` in a form-decoded ``f.req`` body, or None."""
    for frame in _frames(body):
        if _at(frame, 0) == rpcid:
            try:
                return json.loads(str(_at(frame, 1)))
            except (ValueError, TypeError):
                return None
    return None


def url_rpcids(url: str) -> list[str]:
    """Every id in a ``batchexecute`` URL's ``rpcids=`` list (it may batch several)."""
    return [r for v in parse_qs(urlsplit(url).query).get("rpcids", []) for r in v.split(",") if r]


def batch_rpcids(rpcids: list[str], body: str) -> list[str]:
    """``rpcids`` plus the id of every ``f.req`` frame, deduplicated in order."""
    return list(dict.fromkeys([*rpcids, *(str(_at(f, 0)) for f in _frames(body))]))


def submit_facts(body: str) -> tuple[str | None, str | None]:
    """(model key, aspect) the page put in its ``fZytfe`` request."""
    key = MODEL_KEY.search(body or "")
    idx = _at(_inner(EXTEND_SUBMIT_RPC, body or ""), 0, 0, 3)
    return (
        key.group(0) if key else None,
        ASPECT_BY_INDEX.get(idx) if isinstance(idx, int) else None,
    )


def extend_request_problem(
    rpcid: str, body: str, *, media_id: str, workflow_id: str, aspect: str | None
) -> GFlowError | None:
    """Why this outgoing request is not the extend the user asked for, or ``None``.

    The verdicts name ids only — never the body, which carries a page-minted token.
    """
    route = f"batchexecute:{rpcid}"
    if rpcid == SCENE_CREATE_RPC:
        named = _at(_inner(rpcid, body), 1)
        if not isinstance(named, list) or media_id not in named:
            return WireFormatError(
                detail=(
                    f"migrated host: the page's {rpcid} scene request does not name the source "
                    f"clip {media_id} — refused before Flow created a scene for another clip"
                ),
                route=route,
            )
        return None
    if rpcid != EXTEND_SUBMIT_RPC:
        return None
    entry = _at(_inner(rpcid, body), 0, 0)
    found = _at(entry, 0, 1)
    if not isinstance(found, str):
        return WireFormatError(
            detail=(
                f"migrated host: the {rpcid} extend request could not be read, so it could not "
                "be confirmed to continue the requested clip — refused before Flow acted on it"
            ),
            route=route,
        )
    if found != workflow_id:
        return WireFormatError(
            detail=(
                f"migrated host: the {rpcid} extend request continues workflow {found}, not "
                f"{workflow_id} (the clip {media_id}) — refused before Flow billed it"
            ),
            route=route,
        )
    _, page_aspect = submit_facts(body)
    if aspect is not None and page_aspect is not None and page_aspect != aspect:
        return FlowHostMigratedError(
            detail=(
                f"the source clip is {page_aspect} and flow.google.com extends in the source's "
                f"aspect; --aspect {aspect} is not ported to flow.google.com yet — refused "
                "before Flow billed it"
            ),
            remediation_hint="Drop --aspect; the extension keeps the source clip's aspect.",
        )
    return None


def batch_request_problem(
    rpcids: list[str], body: str, *, media_id: str, workflow_id: str, aspect: str | None
) -> GFlowError | None:
    """The first problem of any rpc this (possibly batched) request carries, or ``None``.

    A batch is judged frame by frame: a wrong ``fZytfe`` behind another rpc
    (``rpcids=uwAyfb,fZytfe``) must not ride through on the first id's verdict.
    """
    for rpcid in batch_rpcids(rpcids, body):
        problem = extend_request_problem(
            rpcid, body, media_id=media_id, workflow_id=workflow_id, aspect=aspect
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
        )


def _request_verdict(
    rpcids: list[str],
    body: str,
    *,
    media_id: str,
    workflow_id: str,
    aspect: str | None,
    released_submits: int,
) -> GFlowError | None:
    """Why one intercepted ``batchexecute`` request must be aborted, or ``None``."""
    verdict = batch_request_problem(
        rpcids, body, media_id=media_id, workflow_id=workflow_id, aspect=aspect
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


def _route_guard(
    *, media_id: str, workflow_id: str, aspect: str | None, facts: dict[str, str | None]
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


def _started_handler(
    *,
    workflow_id: str,
    facts: dict[str, str | None],
    on_started: ExtendStartedCallback | None,
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
        if on_started is not None:
            # The guard read the page's own fZytfe before this reply arrived.
            accepted = ExtendStarted(
                media_id=info.media_id,
                workflow_id=info.workflow_id or "",
                model_key=facts.get("model_key") or "unknown",
            )
            on_started(accepted, facts.get("aspect"))

    return started


async def run_extend(  # noqa: PLR0913
    page: Page,
    *,
    project_id: str,
    media_id: str,
    prompt: str,
    aspect: str | None,
    poll_timeout_s: float,
    on_started: ExtendStartedCallback | None = None,
) -> ExtendOutcome:
    """Extend ``media_id`` by one segment through Flow's own UI; download the result."""
    clip = await _source_clip(page, project_id=project_id, media_id=media_id)
    workflow_id = require_video_source(clip, media_id=media_id, project_id=project_id)
    _confirm_editor(page, project_id=project_id, media_id=media_id)
    log.info("migrated_extend.source", media_id=media_id, workflow_id=workflow_id)

    composer = MigratedComposer()
    await (await _anchor(page, ADD_CLIP_BUTTON, named="timeline add-clip button")).click()
    await (await _anchor(page, EXTEND_MENU_ITEM, named="Extend menu item")).click()
    await (await _anchor(page, EXTEND_PROMPT, named="extend prompt box")).click()
    await page.keyboard.insert_text(prompt)

    facts: dict[str, str | None] = {}

    def check(rpcid: str, body: str) -> GFlowError | None:
        return batch_request_problem(
            [rpcid], body, media_id=media_id, workflow_id=workflow_id, aspect=aspect
        )

    guard = _route_guard(media_id=media_id, workflow_id=workflow_id, aspect=aspect, facts=facts)
    started = _started_handler(workflow_id=workflow_id, facts=facts, on_started=on_started)

    def is_batchexecute(url: str) -> bool:
        return "batchexecute" in url

    await page.route(is_batchexecute, guard)
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
            await page.unroute(is_batchexecute, guard)
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
    )


__all__ = [
    "EXTEND_SUBMIT_RPC",
    "ExtendOutcome",
    "SourceClip",
    "batch_request_problem",
    "extend_request_problem",
    "require_video_source",
    "run_extend",
    "source_record",
    "submit_facts",
    "unported_refusal",
]
