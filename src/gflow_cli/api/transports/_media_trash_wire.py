"""Payload encoding, response parsing and page evaluation for media trash/restore (#1363).

Flow mutations for media items use batchexecute RPC ``pGCYOe``. Moving to Trash sets the
field mask ``metadata.archived`` with flag ``[null, null, 1]``. Restoring resets
``metadata.archived`` and ``collection_id``. Both operations echo the media record in
slot ``[0][0]``, with slot ``[0][0][3][2]`` indicating the archived state (``true`` for
trashed, ``null`` for restored).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors
from gflow_cli.errors import FlowApiError, WireFormatError

if TYPE_CHECKING:
    from playwright.async_api import Page

TRASH_RPC = "pGCYOe"
TRASH_FIELD_MASK = "metadata.archived"
RESTORE_FIELD_MASKS = ("metadata.archived", "collection_id")

PAGE_EVAL_MUTATION_JS = """
async ({ rpcid, innerJson }) => {
    const at = window.WIZ_global_data?.SNlM0e
        || (document.body?.innerHTML?.match(/"SNlM0e":"([^"]*)"/) || [])[1]
        || '';
    const fsid = window.WIZ_global_data?.FdrFJe
        || (document.body?.innerHTML?.match(/"FdrFJe":"([^"]*)"/) || [])[1]
        || '';
    const bl = window.WIZ_global_data?.cfb2h
        || (document.body?.innerHTML?.match(/"cfb2h":"([^"]*)"/) || [])[1]
        || '';
    const reqid = String(Math.floor(Math.random() * 900000) + 100000);
    const query = new URLSearchParams({
        rpcids: rpcid,
        'source-path': '/',
        bl: bl,
        'f.sid': fsid,
        hl: 'en',
        _reqid: reqid,
        rt: 'c',
    });
    const body = new URLSearchParams({
        'f.req': JSON.stringify([[[rpcid, innerJson, null, 'generic']]]),
        at: at,
    });
    const resp = await fetch(`/_/AiSandboxAngularFrontend/data/batchexecute?${query}`, {
        method: 'POST',
        headers: {
            'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'x-same-domain': '1',
        },
        body: body.toString(),
    });
    return { status: resp.status, text: await resp.text() };
}
"""


def _as_list(val: object) -> list[object] | None:
    return cast("list[object]", val) if isinstance(val, list) else None


def build_trash_payload(media_id: str, project_id: str) -> list[Any]:
    """Return inner batchexecute JSON payload for pGCYOe trash mutation."""
    return [
        [[media_id, None, None, [None, None, 1], project_id]],
        [[TRASH_FIELD_MASK]],
    ]


def build_restore_payload(media_id: str, project_id: str) -> list[Any]:
    """Return inner batchexecute JSON payload for pGCYOe restore mutation."""
    return [
        [[media_id, "", None, [], project_id]],
        [list(RESTORE_FIELD_MASKS)],
    ]


def parse_trash_response(frame: object, expected_media_id: str | None = None) -> dict[str, Any]:
    """Validate and extract trash response from pGCYOe frame.

    Asserts slot [0][0][0] matches expected media ID and slot [0][0][3][2] is True.
    """
    rows = _as_list(frame)
    if not rows or not rows[0]:
        raise WireFormatError(f"Unexpected pGCYOe trash response frame: {frame!r}", charged="no")
    first_row = _as_list(rows[0])
    if not first_row or not first_row[0]:
        raise WireFormatError(f"Unexpected pGCYOe trash response frame: {frame!r}", charged="no")
    item = _as_list(first_row[0])
    if not item or len(item) < 5:
        raise WireFormatError(f"Unexpected pGCYOe item structure: {first_row[0]!r}", charged="no")

    media_id = str(item[0]) if item[0] is not None else ""
    if expected_media_id and media_id != expected_media_id:
        raise WireFormatError(
            f"pGCYOe response media ID mismatch: expected {expected_media_id}, got {media_id}",
            charged="no",
        )

    metadata = _as_list(item[3])
    if not metadata or len(metadata) < 3:
        raise WireFormatError(f"pGCYOe response missing metadata array: {item!r}", charged="no")

    archived = metadata[2]
    if archived is not True:
        raise WireFormatError(
            f"pGCYOe response did not set archived flag to True: {archived!r}", charged="no"
        )

    project_id = str(item[4]) if item[4] is not None else ""
    workflow_id = str(metadata[4]) if len(metadata) > 4 and metadata[4] is not None else ""
    return {
        "media_id": media_id,
        "project_id": project_id,
        "workflow_id": workflow_id,
        "archived": True,
        "status": "trashed",
    }


def parse_restore_response(frame: object, expected_media_id: str | None = None) -> dict[str, Any]:
    """Validate and extract restore response from pGCYOe frame.

    Asserts slot [0][0][0] matches expected media ID and slot [0][0][3][2] is None.
    """
    rows = _as_list(frame)
    if not rows or not rows[0]:
        raise WireFormatError(f"Unexpected pGCYOe restore response frame: {frame!r}", charged="no")
    first_row = _as_list(rows[0])
    if not first_row or not first_row[0]:
        raise WireFormatError(f"Unexpected pGCYOe restore response frame: {frame!r}", charged="no")
    item = _as_list(first_row[0])
    if not item or len(item) < 5:
        raise WireFormatError(f"Unexpected pGCYOe item structure: {first_row[0]!r}", charged="no")

    media_id = str(item[0]) if item[0] is not None else ""
    if expected_media_id and media_id != expected_media_id:
        raise WireFormatError(
            f"pGCYOe response media ID mismatch: expected {expected_media_id}, got {media_id}",
            charged="no",
        )

    metadata = _as_list(item[3])
    if not metadata or len(metadata) < 3:
        raise WireFormatError(f"pGCYOe response missing metadata array: {item!r}", charged="no")

    archived = metadata[2]
    if archived is not None:
        raise WireFormatError(
            f"pGCYOe response did not clear archived flag: {archived!r}", charged="no"
        )

    project_id = str(item[4]) if item[4] is not None else ""
    workflow_id = str(metadata[4]) if len(metadata) > 4 and metadata[4] is not None else ""
    return {
        "media_id": media_id,
        "project_id": project_id,
        "workflow_id": workflow_id,
        "archived": False,
        "status": "restored",
    }


async def page_trash_media(page: Page, media_id: str, project_id: str) -> dict[str, Any]:
    """Execute pGCYOe trash mutation inside Playwright page context."""
    inner_json = json.dumps(build_trash_payload(media_id, project_id), separators=(",", ":"))
    res = cast(
        "dict[str, Any]",
        await page.evaluate(PAGE_EVAL_MUTATION_JS, {"rpcid": TRASH_RPC, "innerJson": inner_json}),
    )
    status = cast("int", res.get("status", 0))
    text = cast("str", res.get("text", ""))
    if status != 200:
        raise FlowApiError(
            f"HTTP {status} from Flow page evaluation", status=status, route=TRASH_RPC
        )
    frames = [p for r, p in parse_frames(text) if r == TRASH_RPC]
    if not frames:
        errors = rpc_errors(text)
        code = errors[0].code if errors and errors[0].code is not None else 3
        raise FlowApiError(
            f"pGCYOe trash rejected by Flow: {text[:200]}", status=code, route=TRASH_RPC
        )
    return parse_trash_response(frames[0], expected_media_id=media_id)


async def page_restore_media(page: Page, media_id: str, project_id: str) -> dict[str, Any]:
    """Execute pGCYOe restore mutation inside Playwright page context."""
    inner_json = json.dumps(build_restore_payload(media_id, project_id), separators=(",", ":"))
    res = cast(
        "dict[str, Any]",
        await page.evaluate(PAGE_EVAL_MUTATION_JS, {"rpcid": TRASH_RPC, "innerJson": inner_json}),
    )
    status = cast("int", res.get("status", 0))
    text = cast("str", res.get("text", ""))
    if status != 200:
        raise FlowApiError(
            f"HTTP {status} from Flow page evaluation", status=status, route=TRASH_RPC
        )
    frames = [p for r, p in parse_frames(text) if r == TRASH_RPC]
    if not frames:
        errors = rpc_errors(text)
        code = errors[0].code if errors and errors[0].code is not None else 3
        raise FlowApiError(
            f"pGCYOe restore rejected by Flow: {text[:200]}", status=code, route=TRASH_RPC
        )
    return parse_restore_response(frames[0], expected_media_id=media_id)
