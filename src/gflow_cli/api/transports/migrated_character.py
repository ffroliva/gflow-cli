"""Characters on flow.google.com: the ``Zzl0ze`` project read.

The labs routes gflow used for characters (``flow.createEntity``,
``flow.projectInitialData``) are retired on this host. The migrated frontend reads a
project with one ``batchexecute`` rpc, ``Zzl0ze``, sent on project load with
``["projects/<id>", null, null, null, [1]]``. Its reply carries everything a character
needs, positionally (measured 2026-10-16,
``docs/superpowers/spikes/2026-10-16-migrated-character-lifecycle-wire.md``):

* slot 1 — workflows: ``[workflow_id, _, _, [filename, …, media_id, …], project, entity_id?]``
* slot 2 — media: ``[media_id, project, workflow_id, …, <download links>…]``
* slot 3 — the live voice catalog: ``[id, kind, "Name", [id, …, [[Name, description, …]]]]``
* slot 5 — characters: ``[project, entity_id, _, [1, name, [images, voices, personality]],
  thumbnail_media_id, [w, h], created, updated]``

``images`` is ``[[workflow_id], …]`` (slot 0 portrait, 1 body), ``voices`` is
``[[null, "<wire id>"]]`` or null, and the third cell is absent on a character that never
had a personality. A fresh character is ``[1, name, []]``.

Parsing is pure. Signed/capability download links are kept apart from
:class:`~gflow_cli.api.character.Character`, which by design never holds a URL.

Building a character from local files (:func:`create_from_files`, #639) drives the
character editor instead, because the host offers no other write: the first image a draft
receives creates the entity (``C4BZMd``), each image is an upload (``maseQ``) bound to it,
and each field the editor commits is one ``rzMKMb`` write that echoes the whole entity.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.character import Character, Voice, voice_from_wire
from gflow_cli.api.transports import migrated_composer
from gflow_cli.api.transports._common import (
    flow_host_kind,
    raise_if_known_landing,
    safe_page_url,
)
from gflow_cli.api.transports._mc_editor_gate import clear_overlays
from gflow_cli.api.transports.batchexecute import discovery_head, parse_frames
from gflow_cli.data.redaction import scrub_secrets
from gflow_cli.errors import (
    ConfigurationError,
    GFlowError,
    MediaUploadRejectedError,
    ReferenceNotFoundError,
    TransportTimeoutError,
    UiSelectorDriftError,
    WireFormatError,
)
from gflow_cli.redaction import redact_sensitive_text

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from playwright.async_api import Page

log = structlog.get_logger(__name__)

PROJECT_READ_RPC = "Zzl0ze"
#: The editor's field write: ``[[project, entity, null, [1, name|null, [_, voices, notes]]],
#: [[<mask>]]]`` -> the whole entity, echoed. One rpc per committed field.
PATCH_RPC = "rzMKMb"
#: Creates the entity, fired by the FIRST image a draft character receives:
#: ``[[project, null, null, [1, "<default name>", []]]]`` -> ``[[project, entity, …]]``.
CREATE_RPC = "C4BZMd"
#: The upload; bound to a character when its request ends ``[null, null, [entity, [slot]]]``.
UPLOAD_RPC = "maseQ"

#: Editor anchors (spike ``docs/superpowers/spikes/2026-10-16-migrated-character-lifecycle-
#: wire.md``). Structural only: component tags, classes and icon ligatures, never a label.
CHARACTER_URL = "https://flow.google.com/project/{project_id}/character/{entity_id}"
#: The draft page: no name field until the first image creates the entity.
DRAFT_URL = "https://flow.google.com/project/{project_id}/character"
#: The draft footer's Upload, the create gesture (slot 0, the portrait).
FOOTER_UPLOAD = "flow-create-character-footer button:has(mat-icon:text-is('upload'))"
#: Slot chips in the preview panel: nth 0 portrait, nth 1 body.
SLOT_CHIP = "flow-slot-chip-button button"
#: The selected empty slot's own Upload, which binds into that slot. NOT the prompt box's
#: add-menu Upload, which sends no binding and only adds project media.
SLOT_UPLOAD = "flow-character-image-preview button:has(mat-icon:text-is('upload'))"
NAME_INPUT = "flow-character-form input.name-input"
PERSONALITY_INPUT = "flow-character-form textarea.personality-textarea"
#: With no voice yet the component renders one plain button; ``voice-card-start-btn`` once set.
VOICE_BUTTON = "flow-character-voice-button button"
VOICE_DIALOG = "mat-dialog-container"
VOICE_SEARCH = "input.search-input"
VOICE_OPTION = "[role='option']"
VOICE_CONFIRM = "button[class*='detail-add']"
#: Reads lag writes by seconds; a decision-driving re-read polls this long.
READ_SETTLE_S = 60.0
#: How long a voice the editor shows may take to appear after a confirm with no reply.
VOICE_SHOWN_WAIT_S = 5.0
_POLL_S = 0.25
_REREAD_S = 8.0

_CHARACTERS, _VOICES = 5, 3
_CHARACTER_AT = "migrated.character"
_CHARACTER_ROUTE = re.compile(
    r"/project/(?P<project>[0-9a-f-]{36})/character/(?P<entity>[0-9a-f-]{36})"
)


@dataclass(frozen=True)
class ProjectRead:
    characters: tuple[Character, ...]
    voices: tuple[Voice, ...]


def _drift(payload: object, what: str, rpcid: str = PROJECT_READ_RPC) -> WireFormatError:
    return WireFormatError(
        detail=f"batchexecute {rpcid}: {what}",
        route=f"batchexecute:{rpcid}",
        discovery={"rpcid": rpcid, "payload_head": discovery_head(payload)},
    )


def _list(node: object) -> list[Any] | None:
    return cast("list[Any]", node) if isinstance(node, list) else None


def _str(node: object) -> str | None:
    return node if isinstance(node, str) and node else None


def _character_row_header(cells: list[Any], payload: object) -> tuple[str, str, list[Any]]:
    info = _list(cells[3]) if len(cells) > 3 else None
    project = _str(cells[0]) if cells else None
    entity = _str(cells[1]) if len(cells) > 1 else None
    if (
        info is None
        or project is None
        or entity is None
        or len(info) < 2
        or type(info[0]) is not int
    ):
        raise _drift(payload, "a character row has no [project, entity, _, [kind, name, …]]")
    return project, entity, info


def _workflow_ids(parts: list[Any], payload: object) -> tuple[str, ...]:
    images_cell = parts[0] if parts else None
    images = _list(images_cell)
    if images_cell is not None and images is None:
        raise _drift(payload, "character images are not [[workflow_id, …], …]")
    ids: list[str] = []
    for ref in images or []:
        cells = _list(ref)
        if cells is None:
            raise _drift(payload, "a character image ref is not [workflow_id, …]")
        if cells and (wf := _str(cells[0])) is not None:
            ids.append(wf)
    return tuple(ids)


def _character_voice(parts: list[Any], payload: object) -> str | None:
    voice_cell = parts[1] if len(parts) > 1 else None
    voice_refs = _list(voice_cell)
    if voice_cell is not None and voice_refs is None:
        raise _drift(payload, "character voice refs are not [[_, wire_id], …]")
    voice_id: str | None = None
    if voice_refs:
        first_ref = _list(voice_refs[0])
        if first_ref is None or len(first_ref) < 2:
            raise _drift(payload, "character voice ref has no [_, wire_id]")
        voice_id = _str(first_ref[1])
    return voice_from_wire(voice_id) if voice_id else None


def _character_parts(info: list[Any], payload: object) -> list[Any]:
    raw = info[2] if len(info) > 2 else None
    if raw is None:
        return []
    parts = _list(raw)
    if parts is None:
        raise _drift(payload, "character parts (info[2]) is not a list")
    return parts


def _character(row: object, payload: object) -> Character | None:
    cells = _list(row) or []
    project, entity, info = _character_row_header(cells, payload)
    if info[0] != 1:
        return None
    parts = _character_parts(info, payload)
    workflow_ids = _workflow_ids(parts, payload)
    voice = _character_voice(parts, payload)
    personality = _str(parts[2]) if len(parts) > 2 else None
    return Character(
        entity_id=entity,
        display_name=_str(info[1]) or "",
        project_id=project,
        workflow_ids=workflow_ids,
        voice=voice,
        personality=personality,
        thumbnail_media_id=_str(cells[4]) if len(cells) > 4 else None,
    )


def _voice(entry: object) -> Voice | None:
    cells = _list(entry) or []
    name = _str(cells[2]) if len(cells) > 2 else None
    if name is None:
        return None
    detail = _list(cells[3]) if len(cells) > 3 else None
    labels = _list(detail[10]) if detail and len(detail) > 10 else None
    first = _list(labels[0]) if labels else None
    return Voice(name, _str(first[1]) if first and len(first) > 1 else None)


def _matches_project(payload: object, project_id: str) -> bool:
    top = _list(payload)
    if top is None or len(top) <= _CHARACTERS:
        return True
    rows = _list(top[_CHARACTERS])
    if not rows:
        return True
    projects = {_str(cells[0]) for r in rows if (cells := _list(r))}
    projects.discard(None)
    return not projects or project_id in projects


def parse_project_read(payload: object, *, project_id: str | None = None) -> ProjectRead:
    """Parse a decoded ``Zzl0ze`` reply into characters and live voices.

    A slot that is null is an empty project (no characters yet), not drift; a slot
    whose rows do not have the measured shape raises :class:`WireFormatError` rather
    than being skipped, so a changed wire never reads as "no characters"."""
    top = _list(payload)
    if top is None or len(top) <= _CHARACTERS:
        raise _drift(payload, "the reply is not the measured 8-slot project read")
    rows = top[_CHARACTERS]
    if rows is not None and _list(rows) is None:
        raise _drift(payload, "slot 5 (characters) is not a list")
    characters = tuple(
        char
        for row in _list(rows) or []
        if (char := _character(row, payload)) is not None
        and (project_id is None or char.project_id == project_id)
    )
    voices = tuple(v for e in _list(top[_VOICES]) or [] if (v := _voice(e)) is not None)
    return ProjectRead(characters=characters, voices=voices)


async def read_project(
    page: Page,
    project_id: str,
    *,
    timeout_s: float = 45.0,
) -> ProjectRead:
    """Navigate *page* to ``/project/<id>`` (or reload it) and parse ``Zzl0ze``."""
    is_at, rpc_reply = migrated_composer.is_at, migrated_composer.rpc_reply
    target = migrated_composer.MIGRATED_PROJECT_URL.format(project_id=project_id)

    async def trigger() -> None:
        if is_at(getattr(page, "url", ""), target):
            await page.reload(wait_until="domcontentloaded", timeout=45_000)
        else:
            await page.goto(target, wait_until="domcontentloaded", timeout=45_000)

    async def retry() -> None:
        await page.goto(target, wait_until="domcontentloaded", timeout=45_000)

    def accept(payload: object) -> bool:
        return _matches_project(payload, project_id)

    try:
        try:
            raw = await rpc_reply(
                page, PROJECT_READ_RPC, trigger, timeout_s=timeout_s, accept=accept
            )
        except (TransportTimeoutError, PlaywrightTimeoutError):
            await raise_if_known_landing(page, requested=target, at=_CHARACTER_AT)
            log.warning("character.project_read_retry", project_id=project_id, timeout_s=timeout_s)
            raw = await rpc_reply(page, PROJECT_READ_RPC, retry, timeout_s=timeout_s, accept=accept)
    except PlaywrightTimeoutError as e:
        await raise_if_known_landing(page, requested=target, at=_CHARACTER_AT)
        raise TransportTimeoutError(
            detail=(
                f"flow.google.com navigation to {safe_page_url(target)} timed out "
                f"before {PROJECT_READ_RPC} replied (host=migrated)"
            ),
        ) from e
    except TransportTimeoutError:
        await raise_if_known_landing(page, requested=target, at=_CHARACTER_AT)
        raise
    return parse_project_read(raw, project_id=project_id)


# --- editor writes: reply readers (pure) ---------------------------------------------


def _same(field: str, got: str | None, want: str) -> bool:
    # Only the voice id changes case on the wire (canonical ``Aoede``, wire ``aoede``); a
    # name or personality must come back exactly, or a dropped case-only edit reads as set.
    if field == "voice":
        return (got or "").casefold() == want.casefold()
    return got == want


def verify_patch(
    payload: object,
    *,
    entity_id: str,
    name: str | None = None,
    voice: str | None = None,
    personality: str | None = None,
) -> Character:
    """Confirm an ``rzMKMb`` echo carries what was asked, field by field.

    The editor commits on blur; success is read from the reply Flow sends back, never
    from the DOM: a field can show the typed text while the write was dropped."""
    rows = _list(payload)
    char = _character(rows[0], payload) if rows else None
    if char is None:
        raise _drift(payload, "the reply carries no character", PATCH_RPC)
    asked = {"name": name, "voice": voice, "personality": personality}
    got = {"name": char.display_name, "voice": char.voice, "personality": char.personality}
    wrong = [
        f"{field} is {got[field]!r}, not {want!r}"
        for field, want in asked.items()
        if want is not None and not _same(field, got[field], want)
    ]
    if char.entity_id != entity_id:
        wrong.insert(0, f"it names entity {char.entity_id}, not {entity_id}")
    if wrong:
        raise _drift(payload, "the write was not applied: " + "; ".join(wrong), PATCH_RPC)
    return char


@dataclass(frozen=True)
class CreatedCharacter:
    """What a file-built create leaves behind: the entity and its bound images, in slot
    order (portrait first). A media id lost with its upload reply is left out."""

    entity_id: str
    workflow_ids: tuple[str, ...]
    media_ids: tuple[str, ...]


def created_entity(payload: object, *, project_id: str) -> str:
    """The new entity id from a ``C4BZMd`` reply, for the project it was created in."""
    rows = _list(payload) or []
    row = _list(rows[0]) if rows else None
    project = _str(row[0]) if row else None
    entity = _str(row[1]) if row and len(row) > 1 else None
    if entity is None or project != project_id:
        raise _drift(payload, f"the reply names no new entity in {project_id}", CREATE_RPC)
    return entity


def _upload_fields(payload: object) -> tuple[str | None, str | None, str | None]:
    """``(media_id, workflow_id, bound entity)`` from a ``maseQ`` reply, each or ``None``."""
    rows = _list(payload) or []
    media = _list(rows[0]) if rows else None
    workflow = _list(rows[1]) if len(rows) > 1 else None
    return (
        _str(media[0]) if media else None,
        _str(workflow[0]) if workflow else None,
        _str(workflow[5]) if workflow and len(workflow) > 5 else None,
    )


def bound_upload(payload: object, *, entity_id: str) -> tuple[str, str]:
    """``(workflow_id, media_id)`` from a ``maseQ`` reply, refusing an upload that did not
    land in ``entity_id``: the add-menu Upload binds nothing, and an image bound elsewhere
    is not this character's."""
    media_id, workflow_id, bound = _upload_fields(payload)
    if media_id is None or workflow_id is None:
        raise _drift(payload, "the reply names no media and workflow", UPLOAD_RPC)
    if bound != entity_id:
        raise _drift(
            payload, f"the upload was not bound to {entity_id} (bound to {bound})", UPLOAD_RPC
        )
    return workflow_id, media_id


def _recover_created(got: dict[str, Any], url: object, project_id: str) -> str | None:
    """The created character's id when its ``C4BZMd`` reply was lost, or ``None``.

    The upload reply says which character the image went into, and Flow moves the page to
    the new character's editor; either proves the create. Neither means nothing was
    created (an unbound upload is plain project media)."""
    if UPLOAD_RPC in got:
        bound = _upload_fields(got[UPLOAD_RPC])[2]
        if bound is not None:
            log.warning("migrated.character_create_reply_lost", source="upload_reply")
            return bound
    match = _CHARACTER_ROUTE.search(str(url or ""))
    if match and match["project"] == project_id:
        log.warning("migrated.character_create_reply_lost", source="page_url")
        return match["entity"]
    return None


def _names(text: str, voice: str) -> bool:
    """Whether one LINE of ``text`` is ``voice``. The voice name is an untranslated proper
    noun; the description under it is translated, so the whole text is never matched."""
    return voice.casefold() in (line.strip().casefold() for line in text.splitlines())


def _editor_drift(exc: PlaywrightError, when: str) -> UiSelectorDriftError:
    first_line = redact_sensitive_text(str(exc).splitlines()[0] if str(exc) else "")[:300]
    return UiSelectorDriftError(
        detail=(
            f"the character editor did not respond {when} "
            f"({type(exc).__name__}: {first_line}) (host=migrated)"
        ),
    )


def _head_tail(text: str, *, head: int = 300, tail: int = 1500) -> str:
    return text if len(text) <= head + tail else f"{text[:head]} … {text[-tail:]}"


def _orphan_hint(entity_id: str, name: str, project_id: str) -> str:
    return (
        f"Flow already created this character (entity {entity_id}, named {name!r} if the "
        f"name step ran) in project {project_id}. gflow does not delete it: finish it in "
        f"Flow, or delete it there before re-running."
    )


# --- editor gestures ----------------------------------------------------------------


async def _batchexecute_text(response: Any) -> str | None:
    if "batchexecute" not in str(getattr(response, "url", "")):
        return None
    try:
        return await response.text()
    except Exception:  # noqa: BLE001 - an aborted body is not a frame we can read
        return None


async def _first_then_grace(first: asyncio.Event, done: asyncio.Event, grace_s: float) -> None:
    await first.wait()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(done.wait(), timeout=grace_s)


async def _rpc_replies(
    page: Page,
    rpcids: tuple[str, ...],
    action: Callable[[], Awaitable[None]],
    *,
    timeout_s: float,
    grace_s: float = 15.0,
) -> dict[str, Any]:
    """Run ``action`` and collect the first reply of each rpcid, for up to ``timeout_s``,
    and for at most ``grace_s`` more once any one of them has arrived.

    Returns whatever arrived; a missing rpcid is absent from the dict, never raised here.
    For an upload a lost reply is a question for a re-read, not a failure: a ``maseQ``
    reply dropped after the server got it can still be bound. The grace keeps a run from
    waiting the full timeout for a ``C4BZMd`` that never comes after ``maseQ``, which says
    what was created, has answered."""
    got: dict[str, Any] = {}
    first, done = asyncio.Event(), asyncio.Event()

    async def on_response(response: Any) -> None:
        text = await _batchexecute_text(response)
        for rid, frame in parse_frames(text) if text else ():
            if rid in rpcids and rid not in got:
                got[rid] = frame
                first.set()
        if all(r in got for r in rpcids):
            done.set()

    page.on("response", on_response)
    try:
        await action()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_first_then_grace(first, done, grace_s), timeout=timeout_s)
        if not done.is_set():
            log.warning(
                "migrated.character_reply_missing", missing=[r for r in rpcids if r not in got]
            )
    finally:
        page.remove_listener("response", on_response)
    return got


async def _settle(page: Page, *, timeout_s: float = 10.0) -> None:
    """Let a freshly loaded editor finish loading before the first interaction.

    A cold load is still fetching fonts, voice samples and the character's image while
    the app wires its handlers, and edits sent then go unanswered. Network idle, capped
    and best-effort: a page that never idles is driven anyway."""
    try:
        await page.wait_for_load_state("networkidle", timeout=int(timeout_s * 1000))
    except PlaywrightError:
        log.info("migrated.character_settle_capped", seconds=timeout_s)


async def _wait_form_loaded(page: Page, *, timeout_s: float = 15.0) -> None:
    """Wait until the editor has loaded the character into its form.

    The name input is visible but EMPTY for about 1.5 s after load, then the app fills it;
    a value typed before that is overwritten and nothing commits. Every character has a
    name (Flow gives a new one a default), so a non-empty name is the loaded signal."""
    box = page.locator(NAME_INPUT).first
    await box.wait_for(state="visible", timeout=30_000)
    for _ in range(int(timeout_s / _POLL_S)):
        if await box.input_value():
            return
        await page.wait_for_timeout(int(_POLL_S * 1000))
    raise UiSelectorDriftError(
        detail=(
            f"the character editor showed its name field but never loaded a name into it "
            f"within {timeout_s:.0f}s (host=migrated)"
        ),
    )


async def _open_editor(page: Page, project_id: str, entity_id: str) -> None:
    await page.goto(
        CHARACTER_URL.format(project_id=project_id, entity_id=entity_id),
        wait_until="domcontentloaded",
        timeout=45_000,
    )
    await _settle(page)
    await clear_overlays(page)
    await _wait_form_loaded(page)


async def _commit_text(box: Any, value: str) -> None:
    await box.fill(value)
    await box.press("Tab")


async def _patch_text(
    page: Page,
    project_id: str,
    entity_id: str,
    field: tuple[str, str],
    value: str,
    timeout_s: float,
) -> None:
    """Type ``value`` into one field and confirm it from its ``rzMKMb`` echo.

    A field already showing ``value`` is left alone: the editor sends nothing for an
    unchanged field. The first edit after a create can go unanswered on a cold profile
    although the form shows the character, so an unanswered edit is redone once after a
    reload; an edit that did land then reads back as already set."""
    selector, name = field
    for attempt in (1, 2):
        box = page.locator(selector).first
        if await box.input_value() == value:
            return
        try:
            reply = await migrated_composer.rpc_reply(
                page, PATCH_RPC, partial(_commit_text, box, value), timeout_s=timeout_s
            )
        except TransportTimeoutError:
            if attempt == 2:
                raise
            log.warning("migrated.character_patch_retry", field=name)
            await _open_editor(page, project_id, entity_id)
            continue
        verify_patch(reply, entity_id=entity_id, **{name: value})
        log.info("migrated.character_patched", field=name, entity_id=entity_id)
        return


async def _voice_hits(dialog: Any, voice: str, *, timeout_s: float = 10.0) -> tuple[Any, list[int]]:
    """Poll the searched list until exactly one option has a line equal to ``voice``.

    Polled, not slept: a fixed wait reads a slow-rendering list as "no options"."""
    options = dialog.locator(VOICE_OPTION)
    hits: list[int] = []
    for _ in range(int(timeout_s / _POLL_S)):
        texts = [await options.nth(i).inner_text() for i in range(await options.count())]
        hits = [i for i, text in enumerate(texts) if _names(text, voice)]
        if len(hits) == 1:
            break
        await asyncio.sleep(_POLL_S)
    return options, hits


async def _voice_shown(page: Page, voice: str) -> bool:
    """Whether the editor's voice button names ``voice`` within a few seconds."""
    for _ in range(int(VOICE_SHOWN_WAIT_S / _POLL_S)):
        try:
            text = await page.locator(VOICE_BUTTON).first.inner_text()
        except PlaywrightError:
            text = ""
        if _names(text, voice):
            return True
        await asyncio.sleep(_POLL_S)
    return False


async def _confirm_enabled(button: Any, *, timeout_s: float = 10.0) -> bool:
    for _ in range(int(timeout_s / _POLL_S)):
        if await button.is_enabled():
            return True
        await asyncio.sleep(_POLL_S)
    return False


async def _voice_option(page: Page, voice: str) -> tuple[Any, Any]:
    """Open the picker, search ``voice``; return ``(dialog, its one option)``."""
    await page.locator(VOICE_BUTTON).first.click()
    dialog = page.locator(VOICE_DIALOG).last
    await dialog.locator(VOICE_SEARCH).fill(voice)
    options, hits = await _voice_hits(dialog, voice)
    if len(hits) != 1:
        await page.keyboard.press("Escape")
        raise ReferenceNotFoundError(
            detail=(
                f"the voice picker offered {len(hits)} option(s) named {voice!r} after "
                f"searching; nothing was changed"
            ),
        )
    return dialog, options.nth(hits[0])


async def _confirm_voice(
    page: Page, entity_id: str, voice: str, button: Any, *, timeout_s: float
) -> None:
    try:
        reply = await migrated_composer.rpc_reply(
            page, PATCH_RPC, partial(button.click, timeout=15_000), timeout_s=timeout_s
        )
    except (PlaywrightError, TransportTimeoutError):
        # Flow can set the voice and close the picker while the click still waits, so
        # neither the click nor its reply completes. The editor's voice button is Flow's
        # own state: if it shows the voice, it is set; otherwise the failure stands.
        if not await _voice_shown(page, voice):
            raise
        log.warning("migrated.character_voice_set_without_reply", entity_id=entity_id)
        return
    verify_patch(reply, entity_id=entity_id, voice=voice)
    log.info("migrated.character_patched", field="voice", entity_id=entity_id)


async def _pick_voice(page: Page, entity_id: str, voice: str, *, timeout_s: float) -> None:
    """Search the voice picker for ``voice``, pick its one option, confirm it.

    On a cold profile the option click can fail to take, so the confirm never becomes
    actionable. The confirm is clicked only once it is ENABLED, and a pick that never
    enables is redone once from a freshly opened picker."""
    if _names(await page.locator(VOICE_BUTTON).first.inner_text(), voice):
        return
    for attempt in (1, 2):
        dialog, option = await _voice_option(page, voice)
        await option.click()
        button = dialog.locator(VOICE_CONFIRM).first
        if await _confirm_enabled(button):
            await _confirm_voice(page, entity_id, voice, button, timeout_s=timeout_s)
            return
        log.warning("migrated.character_voice_retry", attempt=attempt, entity_id=entity_id)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
    raise ReferenceNotFoundError(
        detail=f"the voice picker never accepted {voice!r} (2 attempts); the voice was not set"
    )


async def _patch_fields(
    page: Page,
    project_id: str,
    entity_id: str,
    *,
    name: str | None,
    personality: str | None,
    voice: str | None,
    timeout_s: float,
) -> None:
    url = CHARACTER_URL.format(project_id=project_id, entity_id=entity_id)
    if not migrated_composer.is_at(getattr(page, "url", ""), url):
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await _settle(page)
    await clear_overlays(page)
    await _wait_form_loaded(page)
    for field, value in (
        ((NAME_INPUT, "name"), name),
        ((PERSONALITY_INPUT, "personality"), personality),
    ):
        if value is not None:
            await _patch_text(page, project_id, entity_id, field, value, timeout_s)
    if voice is not None:
        await _pick_voice(page, entity_id, voice, timeout_s=timeout_s)


async def patch_character(
    page: Page,
    project_id: str,
    entity_id: str,
    *,
    name: str | None = None,
    personality: str | None = None,
    voice: str | None = None,
    timeout_s: float = 30.0,
) -> None:
    """Set a character's name, personality and voice through its editor.

    Each field is committed by the editor's own gesture and confirmed from its own
    ``rzMKMb`` echo. A browser timeout (a control that never appeared) is selector drift,
    exit 23."""
    try:
        await _patch_fields(
            page,
            project_id,
            entity_id,
            name=name,
            personality=personality,
            voice=voice,
            timeout_s=timeout_s,
        )
    except PlaywrightError as exc:
        raise _editor_drift(exc, "while editing the character") from exc


async def _choose_file(page: Page, selector: str, path: Path) -> None:
    async with page.expect_file_chooser(timeout=15_000) as chooser:
        await page.locator(selector).first.click()
    await (await chooser.value).set_files(str(path))


async def _character_ids(page: Page, project_id: str) -> set[str]:
    return {c.entity_id for c in (await read_project(page, project_id)).characters}


async def _settled_refs(page: Page, project_id: str, entity_id: str, want: int) -> tuple[str, ...]:
    """Re-read the project until the entity lists ``want`` images or the settle window ends;
    return what the last read showed. Reads lag writes, so one early read decides nothing."""
    deadline = asyncio.get_running_loop().time() + READ_SETTLE_S
    while True:
        read = await read_project(page, project_id)
        char = next((c for c in read.characters if c.entity_id == entity_id), None)
        refs = char.workflow_ids if char else ()
        if len(refs) >= want or asyncio.get_running_loop().time() >= deadline:
            return refs
        await asyncio.sleep(_REREAD_S)


async def _created_since(page: Page, project_id: str, known: set[str]) -> str | None:
    """The one character the project gained since ``known``, read until the settle window
    ends (reads lag writes); ``None`` when none appeared. Two new ones are refused: which
    is this run's cannot be told, and creating a third would not help."""
    deadline = asyncio.get_running_loop().time() + READ_SETTLE_S
    while True:
        new = sorted((await _character_ids(page, project_id)) - known)
        if len(new) > 1:
            raise _drift(
                None,
                f"both create replies were lost and the project gained {len(new)} characters "
                f"({', '.join(new)}); not creating another",
                CREATE_RPC,
            )
        if new:
            log.warning("migrated.character_create_reply_lost", source="project_read")
            return new[0]
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(_REREAD_S)


async def _open_draft(page: Page, project_id: str) -> None:
    await page.goto(
        DRAFT_URL.format(project_id=project_id), wait_until="domcontentloaded", timeout=45_000
    )
    if flow_host_kind(page.url) != "migrated":
        raise ConfigurationError(
            detail=(
                "building a character from local files is driven on flow.google.com; this "
                "project opened on another host, where it is not ported yet (#639)"
            ),
        )


async def _create_attempt(page: Page, project_id: str, portrait: Path) -> dict[str, Any]:
    """One portrait upload from a fresh draft; the create and upload replies that came."""
    await _open_draft(page, project_id)
    await clear_overlays(page)
    await page.locator(FOOTER_UPLOAD).first.wait_for(state="visible", timeout=30_000)
    upload = partial(_choose_file, page, FOOTER_UPLOAD, portrait)
    return await _rpc_replies(page, (CREATE_RPC, UPLOAD_RPC), upload, timeout_s=90)


async def _created_id(
    page: Page, project_id: str, got: dict[str, Any], known: set[str]
) -> str | None:
    """The entity an attempt created: from its reply, else the upload reply or the page,
    else (both replies lost) the one character the project gained."""
    if CREATE_RPC in got:
        return created_entity(got[CREATE_RPC], project_id=project_id)
    entity_id = _recover_created(got, getattr(page, "url", ""), project_id)
    if entity_id is None and not got:
        return await _created_since(page, project_id, known)
    return entity_id


async def _create_entity(page: Page, project_id: str, portrait: Path) -> tuple[str, Any]:
    """Create the character by uploading its portrait; return its id and the upload reply
    (``None`` when that reply was lost). Nothing exists to clean up if this fails."""
    try:
        await _open_draft(page, project_id)
        # The characters already here, so a create whose replies are BOTH lost is found by
        # a re-read rather than repeated: a blind retry would make a second character.
        known = await _character_ids(page, project_id)
        for attempt in (1, 2):
            got = await _create_attempt(page, project_id, portrait)
            entity_id = await _created_id(page, project_id, got, known)
            if entity_id is not None:
                return entity_id, got.get(UPLOAD_RPC)
            log.warning("migrated.character_create_retry", attempt=attempt, project_id=project_id)
    except PlaywrightError as exc:
        raise _editor_drift(exc, "before the character was created") from exc
    raise _drift(None, "uploading the portrait created no character (2 attempts)", CREATE_RPC)


async def _upload_body(
    page: Page, project_id: str, entity_id: str, body: Path
) -> tuple[str, str | None]:
    """Upload ``body`` into slot 1. A lost reply is settled by a re-read: bound means done
    (a blind retry would bind a second copy); unbound earns exactly one retry."""
    for attempt in (1, 2):
        await page.locator(SLOT_CHIP).nth(1).click()
        await page.locator(SLOT_UPLOAD).first.wait_for(state="visible", timeout=15_000)
        upload = partial(_choose_file, page, SLOT_UPLOAD, body)
        got = await _rpc_replies(page, (UPLOAD_RPC,), upload, timeout_s=90)
        if UPLOAD_RPC in got:
            return bound_upload(got[UPLOAD_RPC], entity_id=entity_id)
        refs = await _settled_refs(page, project_id, entity_id, want=2)
        if len(refs) >= 2:
            log.info("migrated.character_upload_bound_without_reply", slot=1)
            return refs[1], None  # bound, but the lost reply took its media id with it
        log.warning("migrated.character_upload_unbound", slot=1, attempt=attempt)
        await _open_editor(page, project_id, entity_id)
    raise MediaUploadRejectedError(
        detail=f"flow.google.com never bound {body.name} as the body image (2 attempts)"
    )


async def _bound_portrait(page: Page, project_id: str, entity_id: str, portrait: Path) -> str:
    """The portrait's workflow id read back, for a create whose upload reply was lost."""
    refs = await _settled_refs(page, project_id, entity_id, want=1)
    if not refs:
        raise MediaUploadRejectedError(
            detail=f"flow.google.com created the character but bound no {portrait.name}"
        )
    await _open_editor(page, project_id, entity_id)
    return refs[0]


async def _follow_to_editor(page: Page, entity_id: str) -> None:
    """Let the app finish its own move to the editor before navigating there. A create
    recovered from the upload reply can leave the page on the draft; the character exists
    and the editor is opened directly next, so that is not a failed create."""
    try:
        await page.wait_for_url(f"**/character/{entity_id}*", timeout=30_000)
    except PlaywrightTimeoutError:
        log.info("migrated.character_route_not_followed", entity_id=entity_id)


@dataclass(frozen=True)
class _Fill:
    name: str
    portrait: Path
    body: Path | None
    personality: str | None
    voice: str | None


async def _fill_character(
    page: Page, project_id: str, entity_id: str, upload: Any, fill: _Fill
) -> CreatedCharacter:
    """Name the created character, then add its body image and voice.

    The editor is always reopened by direct navigation: the in-app draft-to-editor switch
    after a create often never loads the name, while a direct load fills the form in
    about 2 s."""
    workflows: list[str] = []
    media: list[str] = []
    if upload is not None:
        wf, mid = bound_upload(upload, entity_id=entity_id)
        workflows.append(wf)
        media.append(mid)
    await _follow_to_editor(page, entity_id)
    await _open_editor(page, project_id, entity_id)
    await patch_character(page, project_id, entity_id, name=fill.name, personality=fill.personality)
    if not workflows:
        workflows.append(await _bound_portrait(page, project_id, entity_id, fill.portrait))
    if fill.body is not None:
        wf, body_media = await _upload_body(page, project_id, entity_id, fill.body)
        workflows.append(wf)
        if body_media is not None:
            media.append(body_media)
    if fill.voice is not None:
        await patch_character(page, project_id, entity_id, voice=fill.voice)
    return CreatedCharacter(entity_id, tuple(workflows), tuple(media))


async def create_from_files(
    page: Page,
    project_id: str,
    *,
    name: str,
    portrait: Path,
    body: Path | None = None,
    personality: str | None = None,
    voice: str | None = None,
) -> CreatedCharacter:
    """Build a character from local images through the character editor.

    The order is forced by the host: a character only exists once its first image does,
    so the portrait upload creates it; the name is set immediately after, so anything
    that fails later leaves an identifiable character; then the body, then the voice.
    Nothing here deletes: a failure after the create names the entity in the error."""
    entity_id, upload = await _create_entity(page, project_id, portrait)
    log.info("migrated.character_created", entity_id=entity_id, project_id=project_id)
    fill = _Fill(name=name, portrait=portrait, body=body, personality=personality, voice=voice)
    try:
        return await _fill_character(page, project_id, entity_id, upload, fill)
    except PlaywrightError as exc:
        # A browser timeout is not a GFlowError: on its own it would surface unclassified
        # and lose the id of a character Flow already holds. The call log's END names why
        # the action waited (disabled, intercepted by …), so its head and tail are kept.
        log.warning(
            "migrated.character_orphaned",
            entity_id=entity_id,
            project_id=project_id,
            playwright=_head_tail(scrub_secrets(str(exc))),
        )
        drift = _editor_drift(exc, "after the create")
        drift.remediation_hint = _orphan_hint(entity_id, name, project_id)
        raise drift from exc
    except GFlowError as exc:
        exc.remediation_hint = (
            f"{_orphan_hint(entity_id, name, project_id)} {exc.remediation_hint or ''}"
        ).strip()
        log.warning("migrated.character_orphaned", entity_id=entity_id, project_id=project_id)
        raise


__all__ = [
    "CREATE_RPC",
    "PATCH_RPC",
    "PROJECT_READ_RPC",
    "UPLOAD_RPC",
    "CreatedCharacter",
    "ProjectRead",
    "bound_upload",
    "create_from_files",
    "created_entity",
    "parse_project_read",
    "patch_character",
    "read_project",
    "verify_patch",
]
