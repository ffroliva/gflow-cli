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
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog
from playwright.async_api import Error as PlaywrightError

from gflow_cli.api.character import Character, Voice, voice_from_wire, voice_to_wire
from gflow_cli.api.transports.batchexecute import discovery_head
from gflow_cli.data.redaction import scrub_secrets
from gflow_cli.errors import TransportTimeoutError, WireFormatError
from gflow_cli.redaction import redact_sensitive_text

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)

PROJECT_READ_RPC = "Zzl0ze"
#: The editor's field write: ``[[project, entity, null, [1, name|null, [_, voices, notes]]],
#: [[<mask>]]]`` → the whole entity, echoed. One rpc per committed field.
PATCH_RPC = "rzMKMb"
#: Creates the entity — fired by the FIRST image a draft character receives:
#: ``[[project, null, null, [1, "<default name>", []]]]`` → ``[[project, entity, …]]``.
CREATE_RPC = "C4BZMd"
#: The upload; bound to a character when its request ends ``[null, null, [entity, [slot]]]``.
UPLOAD_RPC = "maseQ"

#: Editor anchors, measured 2026-10-16 on a `fr` account (spike Addendum 1). Structural only:
#: component tags, classes and ligatures — never a label.
CHARACTER_URL = "https://flow.google.com/project/{project_id}/character/{entity_id}"
#: The draft page: no name field until the first image creates the entity.
DRAFT_URL = "https://flow.google.com/project/{project_id}/character"
#: The draft footer's Upload — the create gesture (slot 0, the portrait).
FOOTER_UPLOAD = "flow-create-character-footer button:has(mat-icon:text-is('upload'))"
#: Slot chips in the preview panel: nth 0 portrait, nth 1 body.
SLOT_CHIP = "flow-slot-chip-button button"
#: The selected empty slot's own Upload (binds into that slot). NOT the prompt box's
#: add-menu Upload, which sends no binding and only adds project media.
SLOT_UPLOAD = "flow-character-image-preview button:has(mat-icon:text-is('upload'))"
#: Reads lag writes by seconds (measured); a decision-driving re-read polls this long.
READ_SETTLE_S = 60.0
NAME_INPUT = "flow-character-form input.name-input"
PERSONALITY_INPUT = "flow-character-form textarea.personality-textarea"
#: With no voice yet the component renders one plain button; `voice-card-start-btn` once set.
VOICE_BUTTON = "flow-character-voice-button button"
VOICE_DIALOG = "mat-dialog-container"
VOICE_SEARCH = "input.search-input"
VOICE_OPTION = "[role='option']"
VOICE_CONFIRM = "button[class*='detail-add']"

_CHARACTERS, _WORKFLOWS, _MEDIA, _VOICES = 5, 1, 2, 3


@dataclass(frozen=True)
class CharacterImage:
    """One character image as the project read lists it.

    ``download_url`` is a capability link (``lh3.googleusercontent.com/asb/…``): it
    grants access by its path alone and expires. Use it at once, never persist or log
    it. Append ``=s0`` for the original — bare, it serves a 288x512 preview."""

    workflow_id: str
    media_id: str | None
    download_url: str | None = field(repr=False)


_IMAGE_HOST = "lh3.googleusercontent.com"


def original_image_url(image: CharacterImage) -> str:
    """The full-size download link for a character image, checked before any fetch.

    Exact host and path, because the link comes straight off the wire: https, host
    ``lh3.googleusercontent.com``, path under ``/asb/`` (measured 2026-10-16). The bare
    link serves a 288x512 preview; ``=s0`` asks for the original."""
    from urllib.parse import urlsplit  # noqa: PLC0415

    url = image.download_url or ""
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != _IMAGE_HOST
        or not parts.path.startswith("/asb/")
    ):
        raise _drift_rpc(
            None,
            PROJECT_READ_RPC,
            f"image {image.workflow_id} has no download link on {_IMAGE_HOST}/asb/",
        )
    return url + "=s0"


@dataclass(frozen=True)
class ProjectRead:
    characters: tuple[Character, ...]
    voices: tuple[Voice, ...]
    images: dict[str, CharacterImage]


def _drift(payload: object, what: str) -> WireFormatError:
    return WireFormatError(
        detail=f"batchexecute {PROJECT_READ_RPC}: {what}",
        route=f"batchexecute:{PROJECT_READ_RPC}",
        discovery={"rpcid": PROJECT_READ_RPC, "payload_head": discovery_head(payload)},
    )


def _list(node: object) -> list[Any] | None:
    return cast("list[Any]", node) if isinstance(node, list) else None


def _str(node: object) -> str | None:
    return node if isinstance(node, str) and node else None


def _first_url(node: object) -> str | None:
    """The first ``https://`` string anywhere in ``node`` — every link a media row
    carries was measured to be the same one, at four different positions."""
    if isinstance(node, str):
        return node if node.startswith("https://") else None
    for item in _list(node) or []:
        found = _first_url(item)
        if found:
            return found
    return None


def _character(row: object, payload: object) -> Character:
    cells = _list(row) or []
    info = _list(cells[3]) if len(cells) > 3 else None
    project = _str(cells[0]) if cells else None
    entity = _str(cells[1]) if len(cells) > 1 else None
    if info is None or project is None or entity is None or len(info) < 2:
        raise _drift(payload, "a character row has no [project, entity, _, [kind, name, …]]")
    parts = _list(info[2]) if len(info) > 2 else None
    parts = parts or []
    images = _list(parts[0]) if parts else None
    workflow_ids = tuple(
        wf for ref in images or [] if (wf := _str((_list(ref) or [None])[0])) is not None
    )
    voice_refs = _list(parts[1]) if len(parts) > 1 else None
    voice_id = _str((_list(voice_refs[0]) or [None, None])[1]) if voice_refs else None
    personality = _str(parts[2]) if len(parts) > 2 else None
    return Character(
        entity_id=entity,
        display_name=_str(info[1]) or "",
        project_id=project,
        workflow_ids=workflow_ids,
        voice=voice_from_wire(voice_id) if voice_id else None,
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


def parse_project_read(payload: object) -> ProjectRead:
    """Parse a decoded ``Zzl0ze`` reply into characters, live voices and image media.

    A slot that is null is an empty project (no characters yet), not drift; a slot
    whose rows do not have the measured shape raises :class:`WireFormatError` rather
    than being skipped, so a changed wire never reads as "no characters"."""
    top = _list(payload)
    if top is None or len(top) <= _CHARACTERS:
        raise _drift(payload, "the reply is not the measured 8-slot project read")
    rows = top[_CHARACTERS]
    if rows is not None and _list(rows) is None:
        raise _drift(payload, "slot 5 (characters) is not a list")
    characters = tuple(_character(row, payload) for row in _list(rows) or [])
    voices = tuple(v for e in _list(top[_VOICES]) or [] if (v := _voice(e)) is not None)
    media_by_workflow: dict[str, list[Any]] = {}
    for m in _list(top[_MEDIA]) or []:
        cells = _list(m)
        wf = _str(cells[2]) if cells and len(cells) > 2 else None
        if cells and wf:
            media_by_workflow[wf] = cells
    images: dict[str, CharacterImage] = {}
    for char in characters:
        for wf in char.workflow_ids:
            media = media_by_workflow.get(wf)
            images[wf] = CharacterImage(
                workflow_id=wf,
                media_id=_str(media[0]) if media else None,
                download_url=_first_url(media) if media else None,
            )
    return ProjectRead(characters=characters, voices=voices, images=images)


async def read_project(page: Page, project_id: str, *, timeout_s: float = 45.0) -> ProjectRead:
    """Navigate ``page`` to the project and parse the app's own ``Zzl0ze`` reply.

    Nothing is replayed: the reply observed is the one the frontend asks for on load.
    The listener is attached before the navigation starts, so a fast reply cannot be
    missed, and no reply at all is a ``TransportTimeoutError`` — never an empty read.
    """
    from gflow_cli.api.transports.migrated_composer import (  # noqa: PLC0415 - heavy module
        MIGRATED_PROJECT_URL,
        rpc_reply,
    )

    url = MIGRATED_PROJECT_URL.format(project_id=project_id)

    async def navigate() -> None:
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)

    payload = await rpc_reply(page, PROJECT_READ_RPC, navigate, timeout_s=timeout_s)
    read = parse_project_read(payload)
    log.info(
        "migrated.project_read",
        project_id=project_id,
        characters=len(read.characters),
        voices=len(read.voices),
    )
    return read


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
    from the DOM — a field can show the typed text while the write was dropped."""
    rows = _list(payload)
    if not rows:
        raise _drift_rpc(payload, PATCH_RPC, "the reply carries no entity")
    char = _character(rows[0], payload)
    asked = {"name": name, "voice": voice, "personality": personality}
    got = {"name": char.display_name, "voice": char.voice, "personality": char.personality}
    # Only the voice id changes case on the wire (canonical `Aoede`, wire `aoede`); a name
    # or personality must come back exactly, or a dropped case-only edit reads as applied.
    wrong = [
        f"{field} is {got[field]!r}, not {want!r}"
        for field, want in asked.items()
        if want is not None
        and (
            (got[field] or "").casefold() != want.casefold()
            if field == "voice"
            else got[field] != want
        )
    ]
    if char.entity_id != entity_id:
        wrong.insert(0, f"it names entity {char.entity_id}, not {entity_id}")
    if wrong:
        raise _drift_rpc(payload, PATCH_RPC, "the write was not applied: " + "; ".join(wrong))
    return char


def _drift_rpc(payload: object, rpcid: str, what: str) -> WireFormatError:
    return WireFormatError(
        detail=f"batchexecute {rpcid}: {what}",
        route=f"batchexecute:{rpcid}",
        discovery={"rpcid": rpcid, "payload_head": discovery_head(payload)},
    )


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
    ``rzMKMb`` echo. A field already showing the requested value is left alone: the
    editor sends nothing for an unchanged field, so waiting for a reply would time out.
    """
    from gflow_cli.api.transports.migrated_composer import is_at, rpc_reply  # noqa: PLC0415

    url = CHARACTER_URL.format(project_id=project_id, entity_id=entity_id)
    if not is_at(getattr(page, "url", ""), url):
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await _settle(page)
    await _clear_overlays(page)
    await _wait_form_loaded(page)

    for selector, value, field_name in (
        (NAME_INPUT, name, "name"),
        (PERSONALITY_INPUT, personality, "personality"),
    ):
        if value is None:
            continue
        for attempt in (1, 2):
            box = page.locator(selector).first
            if await box.input_value() == value:
                break  # already so — after a reload, also how a lost-reply edit shows

            async def commit(box: Any = box, value: str = value) -> None:
                await box.fill(value)
                await box.press("Tab")

            try:
                reply = await rpc_reply(page, PATCH_RPC, commit, timeout_s=timeout_s)
            except TransportTimeoutError:
                # Measured on a cold profile (2026-10-17): the first edit after a create
                # can go unanswered although the form shows the character. Reload and redo
                # the field once; an edit that did land reads back as already set.
                if attempt == 2:
                    raise
                log.warning("migrated.character_patch_retry", field=field_name)
                await _open_editor(page, project_id, entity_id)
                continue
            verify_patch(reply, entity_id=entity_id, **{field_name: value})
            log.info("migrated.character_patched", field=field_name, entity_id=entity_id)
            break

    if voice is not None:
        await _pick_voice(page, entity_id, voice, timeout_s=timeout_s)


async def _settle(page: Page, *, timeout_s: float = 10.0) -> None:
    """Let a freshly loaded editor finish loading before the first interaction.

    A cold load is still fetching fonts, voice samples and the character's image while
    the app wires its handlers; edits sent then went unanswered (2026-10-17). Network idle,
    capped and best-effort — a page that never idles is driven anyway."""
    try:
        await page.wait_for_load_state("networkidle", timeout=int(timeout_s * 1000))
    except Exception:  # noqa: BLE001 - settling is advisory
        log.info("migrated.character_settle_capped", seconds=timeout_s)


async def _clear_overlays(page: Page) -> None:
    """Dismiss what Flow lays over a freshly loaded page: the first-visit "Get started"
    changelog dialog and Google's cookie-consent bar. Every other flow.google.com path does
    this after each navigation (#593); the character flows did not, and the first run of a
    session failed while every later one passed — a one-time overlay over the controls
    (2026-10-17). Best-effort, like the composer's own."""
    from gflow_cli.api.transports.migrated_composer import clear_overlays  # noqa: PLC0415

    await clear_overlays(page)


async def _wait_form_loaded(page: Page, *, timeout_s: float = 15.0) -> None:
    """Wait until the editor has loaded the character into its form.

    Measured 2026-10-16: the name input is visible but EMPTY for ~1.5 s after load, then
    the app fills it. A value typed before that is overwritten and nothing commits. Every
    character has a name (Flow gives a new one a default), so a non-empty name is the
    loaded signal; the personality may legitimately be empty and cannot be one."""
    from gflow_cli.errors import UiSelectorDriftError  # noqa: PLC0415

    box = page.locator(NAME_INPUT).first
    await box.wait_for(state="visible", timeout=30_000)
    for _ in range(int(timeout_s / 0.25)):
        if await box.input_value():
            return
        await page.wait_for_timeout(250)
    raise UiSelectorDriftError(
        detail=(
            f"the character editor showed its name field but never loaded a name into it "
            f"within {timeout_s:.0f}s (host=migrated)"
        ),
    )


async def _voice_hits(
    dialog: Any, voice: str, *, timeout_s: float = 10.0
) -> tuple[Any, list[int], int]:
    """Poll the searched list until exactly one option's name LINE equals ``voice``.

    The name is an untranslated proper noun ("Aoede"); the description under it is
    translated, so the whole option text is never matched. Polled, not slept: a fixed
    wait read a slow-rendering list as "0 options" and blamed the user."""
    options = dialog.locator(VOICE_OPTION)
    hits: list[int] = []
    count = 0
    for _ in range(int(timeout_s / 0.25)):
        count = await options.count()
        texts = [await options.nth(i).inner_text() for i in range(count)]
        hits = [
            i
            for i, text in enumerate(texts)
            if voice.casefold() in (line.strip().casefold() for line in text.splitlines())
        ]
        if len(hits) == 1:
            break
        await asyncio.sleep(0.25)
    return options, hits, count


VOICE_SHOWN_WAIT_S = 5.0


async def _voice_shown(page: Page, voice: str) -> bool:
    """Whether the editor's voice button names ``voice`` (as a line), within a few seconds."""
    for _ in range(int(VOICE_SHOWN_WAIT_S / 0.25)):
        try:
            text = await page.locator(VOICE_BUTTON).first.inner_text()
        except PlaywrightError:
            text = ""
        if voice.casefold() in (line.strip().casefold() for line in text.splitlines()):
            return True
        await asyncio.sleep(0.25)
    return False


async def _confirm_enabled(button: Any, *, timeout_s: float = 10.0) -> bool:
    for _ in range(int(timeout_s / 0.25)):
        if await button.is_enabled():
            return True
        await asyncio.sleep(0.25)
    return False


async def _pick_voice(page: Page, entity_id: str, voice: str, *, timeout_s: float) -> None:
    """Search the voice picker for ``voice``, pick its one option, confirm it.

    Measured 2026-10-17 on a cold profile (1 run in 4): the option click did not take, and
    the confirm button never became actionable — a 30 s click timeout after the character
    already existed. So the confirm is clicked only once it is ENABLED, and a pick that never
    enables is redone once from a freshly opened picker."""
    from gflow_cli.api.transports.migrated_composer import rpc_reply  # noqa: PLC0415
    from gflow_cli.errors import ReferenceNotFoundError  # noqa: PLC0415

    current = await page.locator(VOICE_BUTTON).first.inner_text()
    if voice.casefold() in (line.strip().casefold() for line in current.splitlines()):
        return
    for attempt in (1, 2):
        await page.locator(VOICE_BUTTON).first.click()
        dialog = page.locator(VOICE_DIALOG).last
        await dialog.locator(VOICE_SEARCH).fill(voice)
        options, hits, count = await _voice_hits(dialog, voice)
        if len(hits) != 1:
            await page.keyboard.press("Escape")
            raise ReferenceNotFoundError(
                detail=(
                    f"the voice picker offered {len(hits)} option(s) named {voice!r} "
                    f"(of {count} after searching); nothing was changed"
                ),
            )
        await options.nth(hits[0]).click()
        button = dialog.locator(VOICE_CONFIRM).first
        if not await _confirm_enabled(button):
            log.warning("migrated.character_voice_retry", attempt=attempt, entity_id=entity_id)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
            continue

        async def confirm(button: Any = button) -> None:
            await button.click(timeout=15_000)

        try:
            reply = await rpc_reply(page, PATCH_RPC, confirm, timeout_s=timeout_s)
        except (PlaywrightError, TransportTimeoutError):
            # Measured 2026-10-17 (MCP import e2e): Flow set the voice and closed the
            # picker while the click still waited, so neither the click nor its reply
            # completed. The editor's voice button is Flow's own state: if it shows the
            # voice, it is set; otherwise the original failure stands.
            if not await _voice_shown(page, voice):
                raise
            log.warning("migrated.character_voice_set_without_reply", entity_id=entity_id)
            return
        verify_patch(reply, entity_id=entity_id, voice=voice_from_wire(voice_to_wire(voice)))
        log.info("migrated.character_patched", field="voice", entity_id=entity_id)
        return
    raise ReferenceNotFoundError(
        detail=f"the voice picker never accepted {voice!r} (2 attempts); the voice was not set"
    )


@dataclass(frozen=True)
class CreatedCharacter:
    """What a file-built create leaves behind: the entity and its bound images, in slot
    order (portrait first)."""

    entity_id: str
    workflow_ids: tuple[str, ...]
    media_ids: tuple[str, ...]


def created_entity(payload: object, *, project_id: str) -> str:
    """The new entity id from a ``C4BZMd`` reply, for the project we created it in."""
    rows = _list(payload) or []
    row = _list(rows[0]) if rows else None
    project = _str(row[0]) if row else None
    entity = _str(row[1]) if row and len(row) > 1 else None
    if entity is None or project != project_id:
        raise _drift_rpc(payload, CREATE_RPC, f"the reply names no new entity in {project_id}")
    return entity


def bound_upload(payload: object, *, entity_id: str) -> tuple[str, str]:
    """``(workflow_id, media_id)`` from a ``maseQ`` reply, refusing an upload that did not
    land in ``entity_id`` — the add-menu Upload binds nothing, and an image bound
    elsewhere is not this character's."""
    rows = _list(payload) or []
    media = _list(rows[0]) if rows else None
    workflow = _list(rows[1]) if len(rows) > 1 else None
    media_id = _str(media[0]) if media else None
    workflow_id = _str(workflow[0]) if workflow else None
    bound = _str(workflow[5]) if workflow and len(workflow) > 5 else None
    if media_id is None or workflow_id is None:
        raise _drift_rpc(payload, UPLOAD_RPC, "the reply names no media and workflow")
    if bound != entity_id:
        raise _drift_rpc(
            payload, UPLOAD_RPC, f"the upload was not bound to {entity_id} (bound to {bound})"
        )
    return workflow_id, media_id


async def _rpc_replies(
    page: Page,
    rpcids: tuple[str, ...],
    action: Any,
    *,
    timeout_s: float,
    grace_s: float = 15.0,
) -> dict[str, Any]:
    """Run ``action`` and collect the first reply of each rpcid, for up to ``timeout_s``,
    and for at most ``grace_s`` more once any one of them has arrived.

    Returns whatever arrived; a missing rpcid is absent from the dict, never raised here —
    for an upload, a lost reply is a question for a re-read, not a failure (spike: a
    ``maseQ`` reply dropped after the server got it was still bound). The grace exists
    because a user run waited the full 90 s for a `C4BZMd` that never came, although
    `maseQ` — which says what was created — had answered long before (2026-10-17)."""
    from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: PLC0415

    got: dict[str, Any] = {}
    done = asyncio.Event()
    first = asyncio.Event()

    async def on_response(response: Any) -> None:
        if "batchexecute" not in str(getattr(response, "url", "")):
            return
        try:
            text = await response.text()
        except Exception:  # noqa: BLE001 - an aborted body is not our frame
            return
        for rid, frame in parse_frames(text):
            if rid in rpcids and rid not in got:
                got[rid] = frame
                first.set()
        if all(r in got for r in rpcids):
            done.set()

    async def settle() -> None:
        await first.wait()
        try:
            await asyncio.wait_for(done.wait(), timeout=grace_s)
        except TimeoutError:
            return

    page.on("response", on_response)
    try:
        await action()
        try:
            await asyncio.wait_for(settle(), timeout=timeout_s)
        except TimeoutError:
            pass
        if not done.is_set():
            log.warning(
                "migrated.character_reply_missing", missing=[r for r in rpcids if r not in got]
            )
    finally:
        page.remove_listener("response", on_response)
    return got


async def _choose_file(page: Page, selector: str, path: Path) -> None:
    async with page.expect_file_chooser(timeout=15_000) as chooser:
        await page.locator(selector).first.click()
    await (await chooser.value).set_files(str(path))


async def _settled_refs(page: Page, project_id: str, entity_id: str, want: int) -> tuple[str, ...]:
    """Re-read the project until the entity lists ``want`` images or the settle window ends;
    return what the last read showed. Reads lag writes, so one early read decides nothing."""
    refs: tuple[str, ...] = ()
    deadline = asyncio.get_running_loop().time() + READ_SETTLE_S
    while True:
        read = await read_project(page, project_id)
        char = next((c for c in read.characters if c.entity_id == entity_id), None)
        refs = char.workflow_ids if char else ()
        if len(refs) >= want or asyncio.get_running_loop().time() >= deadline:
            return refs
        await asyncio.sleep(8)


async def _open_editor(page: Page, project_id: str, entity_id: str) -> None:
    await page.goto(
        CHARACTER_URL.format(project_id=project_id, entity_id=entity_id),
        wait_until="domcontentloaded",
        timeout=45_000,
    )
    await _settle(page)
    await _clear_overlays(page)
    await _wait_form_loaded(page)


async def _upload_body(
    page: Page, project_id: str, entity_id: str, body: Path
) -> tuple[str, str | None]:
    """Upload ``body`` into slot 1. A lost reply is settled by a re-read — bound means done
    (a blind retry would bind a second copy); unbound earns exactly one retry."""
    for attempt in (1, 2):
        await page.locator(SLOT_CHIP).nth(1).click()
        await page.locator(SLOT_UPLOAD).first.wait_for(state="visible", timeout=15_000)

        async def upload() -> None:
            await _choose_file(page, SLOT_UPLOAD, body)

        got = await _rpc_replies(page, (UPLOAD_RPC,), upload, timeout_s=90)
        if UPLOAD_RPC in got:
            return bound_upload(got[UPLOAD_RPC], entity_id=entity_id)
        refs = await _settled_refs(page, project_id, entity_id, want=2)
        if len(refs) >= 2:
            log.info("migrated.character_upload_bound_without_reply", slot=1)
            return refs[1], None  # bound, but the lost reply took its media id with it
        log.warning("migrated.character_upload_unbound", slot=1, attempt=attempt)
        await _open_editor(page, project_id, entity_id)
    from gflow_cli.errors import MediaUploadRejectedError  # noqa: PLC0415

    raise MediaUploadRejectedError(
        detail=f"flow.google.com never bound {body.name} as the body image (2 attempts)"
    )


_CHARACTER_ROUTE = re.compile(
    r"/project/(?P<project>[0-9a-f-]{36})/character/(?P<entity>[0-9a-f-]{36})"
)


def _recover_created(got: dict[str, Any], url: object, project_id: str) -> str | None:
    """The created character's id when its `C4BZMd` reply was lost, or ``None``.

    The upload reply says which character the image went into, and Flow moves the page to
    the new character's editor; either proves the create. Neither means nothing was
    created (an unbound upload is plain project media)."""
    if UPLOAD_RPC in got:
        rows = _list(got[UPLOAD_RPC]) or []
        workflow = _list(rows[1]) if len(rows) > 1 else None
        bound = _str(workflow[5]) if workflow and len(workflow) > 5 else None
        if bound is not None:
            log.warning("migrated.character_create_reply_lost", source="upload_reply")
            return bound
    match = _CHARACTER_ROUTE.search(str(url or ""))
    if match and match["project"] == project_id:
        log.warning("migrated.character_create_reply_lost", source="page_url")
        return match["entity"]
    return None


def _head_tail(text: str, *, head: int = 300, tail: int = 1500) -> str:
    return text if len(text) <= head + tail else f"{text[:head]} … {text[-tail:]}"


def _orphan_hint(entity_id: str, name: str, project_id: str) -> str:
    return (
        f"Flow already created this character (entity {entity_id}, named {name!r} if the "
        f"name step ran) in project {project_id}. gflow does not delete it: finish it in "
        f"Flow, or delete it there before re-running."
    )


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
    Nothing here deletes — a failure after the create names the entity in the error."""

    from gflow_cli.api.transports._common import flow_host_kind  # noqa: PLC0415
    from gflow_cli.errors import (  # noqa: PLC0415
        ConfigurationError,
        GFlowError,
        UiSelectorDriftError,
    )

    got: dict[str, Any] = {}
    entity_id: str | None = None
    for attempt in (1, 2):
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
        await _clear_overlays(page)
        await page.locator(FOOTER_UPLOAD).first.wait_for(state="visible", timeout=30_000)

        async def create() -> None:
            await _choose_file(page, FOOTER_UPLOAD, portrait)

        got = await _rpc_replies(page, (CREATE_RPC, UPLOAD_RPC), create, timeout_s=90)
        if CREATE_RPC in got:
            entity_id = created_entity(got[CREATE_RPC], project_id=project_id)
        else:
            entity_id = _recover_created(got, getattr(page, "url", ""), project_id)
        if entity_id is not None:
            break
        log.warning("migrated.character_create_retry", attempt=attempt, project_id=project_id)
    if entity_id is None:
        raise _drift_rpc(
            None, CREATE_RPC, "uploading the portrait created no character (2 attempts)"
        )
    log.info("migrated.character_created", entity_id=entity_id, project_id=project_id)
    try:
        workflows: list[str] = []
        media: list[str | None] = []
        if UPLOAD_RPC in got:
            wf, mid = bound_upload(got[UPLOAD_RPC], entity_id=entity_id)
            workflows.append(wf)
            media.append(mid)
        await page.wait_for_url(f"**/character/{entity_id}*", timeout=30_000)
        # Always reopen the editor by direct navigation. The in-app draft->editor switch
        # after a create needed a reload in 4 of 5 runs (name never loaded in 15 s), and
        # once loaded the name yet never answered the edit (rzMKMb timeout, 1 run);
        # a direct load fills the form in ~2 s (measured 2026-10-16).
        await _open_editor(page, project_id, entity_id)
        await patch_character(page, project_id, entity_id, name=name, personality=personality)
        if not workflows:
            refs = await _settled_refs(page, project_id, entity_id, want=1)
            if not refs:
                from gflow_cli.errors import MediaUploadRejectedError  # noqa: PLC0415

                raise MediaUploadRejectedError(
                    detail=f"flow.google.com created the character but bound no {portrait.name}"
                )
            workflows.append(refs[0])
            await _open_editor(page, project_id, entity_id)
        if body is not None:
            wf, mid = await _upload_body(page, project_id, entity_id, body)
            workflows.append(wf)
            media.append(mid)
        if voice is not None:
            await patch_character(page, project_id, entity_id, voice=voice)
    except PlaywrightError as exc:
        # A browser timeout is not a GFlowError, so on its own it would surface as an
        # unclassified error and lose the id of a character Flow already holds. The full
        # call log names WHY the action waited (disabled, intercepted by …): keep it.
        log.warning(
            "migrated.character_orphaned",
            entity_id=entity_id,
            project_id=project_id,
            # Head and tail: the reason ("not enabled", "intercepts") is at the END.
            playwright=_head_tail(scrub_secrets(str(exc))),
        )
        first_line = redact_sensitive_text(str(exc).splitlines()[0] if str(exc) else "")[:300]
        raise UiSelectorDriftError(
            detail=(
                f"the character editor did not respond after the create "
                f"({type(exc).__name__}: {first_line}) (host=migrated)"
            ),
            remediation_hint=_orphan_hint(entity_id, name, project_id),
        ) from exc
    except GFlowError as exc:
        exc.remediation_hint = (
            f"{_orphan_hint(entity_id, name, project_id)} {exc.remediation_hint or ''}"
        ).strip()
        log.warning("migrated.character_orphaned", entity_id=entity_id, project_id=project_id)
        raise
    return CreatedCharacter(
        entity_id=entity_id,
        workflow_ids=tuple(workflows),
        media_ids=tuple(m for m in media if m is not None),
    )
