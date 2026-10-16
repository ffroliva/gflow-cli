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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api.character import Character, Voice, voice_from_wire
from gflow_cli.api.transports.batchexecute import discovery_head
from gflow_cli.errors import WireFormatError

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)

PROJECT_READ_RPC = "Zzl0ze"

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
