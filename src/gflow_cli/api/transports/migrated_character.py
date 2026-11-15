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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import structlog
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.character import Character, Voice, voice_from_wire
from gflow_cli.api.transports._common import raise_if_known_landing, safe_page_url
from gflow_cli.api.transports.batchexecute import discovery_head
from gflow_cli.errors import TransportTimeoutError, WireFormatError

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)

PROJECT_READ_RPC = "Zzl0ze"

_CHARACTERS, _VOICES = 5, 3
_CHARACTER_AT = "migrated.character"


@dataclass(frozen=True)
class ProjectRead:
    characters: tuple[Character, ...]
    voices: tuple[Voice, ...]


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
    from gflow_cli.api.transports.migrated_composer import (  # noqa: PLC0415
        MIGRATED_PROJECT_URL,
        is_at,
        rpc_reply,
    )

    target = MIGRATED_PROJECT_URL.format(project_id=project_id)

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


__all__ = [
    "PROJECT_READ_RPC",
    "ProjectRead",
    "parse_project_read",
    "read_project",
]
