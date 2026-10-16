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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api.character import Character, Voice, voice_from_wire, voice_to_wire
from gflow_cli.api.transports.batchexecute import discovery_head
from gflow_cli.errors import WireFormatError

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
    wrong = [
        f"{field} is {got[field]!r}, not {want!r}"
        for field, want in asked.items()
        if want is not None
        and (got[field] or "").casefold()
        != (want.casefold() if field == "voice" else want).casefold()
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
    from gflow_cli.api.transports.migrated_composer import rpc_reply  # noqa: PLC0415

    url = CHARACTER_URL.format(project_id=project_id, entity_id=entity_id)
    if not str(getattr(page, "url", "") or "").startswith(url):
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    await _wait_form_loaded(page)

    for selector, value, field_name in (
        (NAME_INPUT, name, "name"),
        (PERSONALITY_INPUT, personality, "personality"),
    ):
        if value is None:
            continue
        box = page.locator(selector).first
        if await box.input_value() == value:
            continue

        async def commit(box: Any = box, value: str = value) -> None:
            await box.fill(value)
            await box.press("Tab")

        reply = await rpc_reply(page, PATCH_RPC, commit, timeout_s=timeout_s)
        verify_patch(reply, entity_id=entity_id, **{field_name: value})
        log.info("migrated.character_patched", field=field_name, entity_id=entity_id)

    if voice is not None:
        await _pick_voice(page, entity_id, voice, timeout_s=timeout_s)


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


async def _pick_voice(page: Page, entity_id: str, voice: str, *, timeout_s: float) -> None:
    """Search the voice picker for ``voice`` and confirm the one option whose name line
    equals it. The name is an untranslated proper noun ("Aoede"); the description under
    it is translated, so the whole option text is never matched."""
    from gflow_cli.api.transports.migrated_composer import rpc_reply  # noqa: PLC0415
    from gflow_cli.errors import ReferenceNotFoundError  # noqa: PLC0415

    current = await page.locator(VOICE_BUTTON).first.inner_text()
    if voice.casefold() in (line.strip().casefold() for line in current.splitlines()):
        return
    await page.locator(VOICE_BUTTON).first.click()
    dialog = page.locator(VOICE_DIALOG).last
    await dialog.locator(VOICE_SEARCH).fill(voice)
    await page.wait_for_timeout(1200)
    options = dialog.locator(VOICE_OPTION)
    texts = [await options.nth(i).inner_text() for i in range(await options.count())]
    hits = [
        i
        for i, text in enumerate(texts)
        if voice.casefold() in (line.strip().casefold() for line in text.splitlines())
    ]
    if len(hits) != 1:
        await page.keyboard.press("Escape")
        raise ReferenceNotFoundError(
            detail=(
                f"the voice picker offered {len(hits)} option(s) named {voice!r} "
                f"(of {len(texts)} after searching); nothing was changed"
            ),
        )
    await options.nth(hits[0]).click()

    async def confirm() -> None:
        await dialog.locator(VOICE_CONFIRM).first.click()

    reply = await rpc_reply(page, PATCH_RPC, confirm, timeout_s=timeout_s)
    verify_patch(reply, entity_id=entity_id, voice=voice_from_wire(voice_to_wire(voice)))
    log.info("migrated.character_patched", field="voice", entity_id=entity_id)


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
    page: Page, rpcids: tuple[str, ...], action: Any, *, timeout_s: float
) -> dict[str, Any]:
    """Run ``action`` and collect the first reply of each rpcid, for up to ``timeout_s``.

    Returns whatever arrived; a missing rpcid is absent from the dict, never raised here —
    for an upload, a lost reply is a question for a re-read, not a failure (spike: a
    ``maseQ`` reply dropped after the server got it was still bound)."""
    from gflow_cli.api.transports.batchexecute import parse_frames  # noqa: PLC0415

    got: dict[str, Any] = {}
    done = asyncio.Event()

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
        if all(r in got for r in rpcids):
            done.set()

    page.on("response", on_response)
    try:
        await action()
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_s)
        except TimeoutError:
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
    await _wait_form_loaded(page)


async def _upload_body(page: Page, project_id: str, entity_id: str, body: Path) -> tuple[str, str]:
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
            return refs[1], ""
        log.warning("migrated.character_upload_unbound", slot=1, attempt=attempt)
        await _open_editor(page, project_id, entity_id)
    from gflow_cli.errors import MediaUploadRejectedError  # noqa: PLC0415

    raise MediaUploadRejectedError(
        detail=f"flow.google.com never bound {body.name} as the body image (2 attempts)"
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
    from gflow_cli.errors import ConfigurationError, GFlowError  # noqa: PLC0415

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

    async def create() -> None:
        await _choose_file(page, FOOTER_UPLOAD, portrait)

    got = await _rpc_replies(page, (CREATE_RPC, UPLOAD_RPC), create, timeout_s=90)
    if CREATE_RPC not in got:
        raise _drift_rpc(None, CREATE_RPC, "uploading the portrait created no character")
    entity_id = created_entity(got[CREATE_RPC], project_id=project_id)
    log.info("migrated.character_created", entity_id=entity_id, project_id=project_id)
    try:
        workflows: list[str] = []
        media: list[str] = []
        if UPLOAD_RPC in got:
            wf, mid = bound_upload(got[UPLOAD_RPC], entity_id=entity_id)
            workflows.append(wf)
            media.append(mid)
        await page.wait_for_url(f"**/character/{entity_id}*", timeout=30_000)
        await _wait_form_loaded(page)
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
    except GFlowError as exc:
        exc.remediation_hint = (
            f"Flow already created this character (entity {entity_id}, named {name!r} if the "
            f"name step ran) in project {project_id}. gflow does not delete it: finish it in "
            f"Flow, or delete it there before re-running. {exc.remediation_hint or ''}"
        ).strip()
        log.warning("migrated.character_orphaned", entity_id=entity_id, project_id=project_id)
        raise
    return CreatedCharacter(
        entity_id=entity_id, workflow_ids=tuple(workflows), media_ids=tuple(m for m in media if m)
    )
