# pyright: reportPrivateUsage=false
"""Generation records in direct replies, found by their exact id.

``batchexecute.find_record`` matches an anchored UUID, so it cannot see a record whose id
is derived (``<uuid>_upsampled``). ``jwpduf`` answers an id that does not exist yet with a
stub record (status ``4``, no media id), so a record "exists" only when it carries a media
id (spike 2026-11-27, "Parser and poll findings").
"""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlsplit

from gflow_cli.api.transports.batchexecute import (
    GenerationRecord,
    _as_list,
    _at,
    _flow_error,
    parse_frames,
)


def find_exact_record(node: object, record_id: str) -> list[Any] | None:
    """The first list, depth-first, whose slot 0 is ``record_id`` and slot 5 a list."""
    items = _as_list(node)
    if items is None:
        return None
    if len(items) > 5 and items[0] == record_id and isinstance(items[5], list):
        return items
    for child in items:
        found = find_exact_record(child, record_id)
        if found is not None:
            return found
    return None


def existing_record(text: str, rpcid: str, record_id: str) -> GenerationRecord | None:
    """The record for ``record_id`` in ``rpcid``'s frames, or ``None`` when absent or a stub."""
    for frame_rpcid, payload in parse_frames(text):
        if frame_rpcid != rpcid:
            continue
        rec = find_exact_record(payload, record_id)
        if rec is not None and isinstance(rec[2], str) and rec[2]:
            return _decode(rec)
    return None


def original_video_url(text: str, record_id: str) -> str | None:
    """The original mp4's URL in an ``as29s`` reply: an https URL at ``/video/<record_id>``.

    The host is not checked here; the download's allowlist decides. ``as29s`` sent with
    the bare workflow id names ``flow-content.google/video/<id>``; the app's own form
    names only the poster (spike 2026-11-27, "as29s read forms").
    """
    for frame_rpcid, payload in parse_frames(text):
        if frame_rpcid != "as29s":
            continue
        rec = find_exact_record(payload, record_id)
        for value in _strings(rec):
            parts = urlsplit(value)
            if parts.scheme == "https" and parts.path == f"/video/{record_id}":
                return value
    return None


def _strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    items = _as_list(node)
    return [s for item in items for s in _strings(item)] if items is not None else []


def _decode(rec: list[Any]) -> GenerationRecord:
    status_cell = _as_list(_at(rec, 5, 8))
    status: object = status_cell[0] if status_cell else None
    size: object = _at(rec, 5, 13)
    error_code, error_message = _flow_error(status_cell)
    return GenerationRecord(
        workflow_id=cast("str", rec[0]),
        project_id=rec[1] if isinstance(rec[1], str) else "",
        media_id=cast("str", rec[2]),
        status=status if isinstance(status, int) else None,
        size_bytes=size if isinstance(size, int) else None,
        error_code=error_code,
        error_message=error_message,
    )
