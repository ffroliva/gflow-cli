"""Payload encoding, response parsing and page evaluation for media trash/restore (#1363).

Flow mutations for media items use batchexecute RPC ``pGCYOe``. Moving to Trash sets the
field mask ``metadata.archived`` with flag ``[null, null, 1]``. Restoring resets
``metadata.archived`` and ``collection_id``. Both operations echo the media record in
slot ``[0][0]``, with slot ``[0][0][3][2]`` indicating the archived state (``true`` for
trashed, ``null`` for restored).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from gflow_cli.api.flow_wire import Tokens, build_rpc_request, extract_wiz_token
from gflow_cli.api.transports._common import raise_if_known_landing
from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors
from gflow_cli.errors import AuthExpiredError, FlowApiError, WireFormatError

if TYPE_CHECKING:
    from playwright.async_api import Page

TRASH_RPC = "pGCYOe"
TRASH_FIELD_MASK = "metadata.archived"
RESTORE_FIELD_MASKS = ("metadata.archived", "collection_id")

SCRAPE_WIZ_JS = """
() => {
    return {
        at: window.WIZ_global_data?.SNlM0e || '',
        fsid: window.WIZ_global_data?.FdrFJe || '',
        bl: window.WIZ_global_data?.cfb2h || '',
    };
}
"""

EVAL_FETCH_JS = """
async ({ url, body }) => {
    const resp = await fetch(url, {
        method: 'POST',
        headers: {
            'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'x-same-domain': '1',
        },
        body: body,
    });
    return { status: resp.status, text: await resp.text() };
}
"""


def _as_list(val: object) -> list[object] | None:
    return cast("list[object]", val) if isinstance(val, list) else None


def build_mutation_payload(media_id: str, project_id: str, *, archived: bool) -> list[Any]:
    """Return inner batchexecute JSON payload for pGCYOe trash/restore mutation."""
    if archived:
        return [
            [[media_id, None, None, [None, None, 1], project_id]],
            [[TRASH_FIELD_MASK]],
        ]
    return [
        [[media_id, "", None, [], project_id]],
        [list(RESTORE_FIELD_MASKS)],
    ]


def build_trash_payload(media_id: str, project_id: str) -> list[Any]:
    """Return inner batchexecute JSON payload for pGCYOe trash mutation."""
    return build_mutation_payload(media_id, project_id, archived=True)


def build_restore_payload(media_id: str, project_id: str) -> list[Any]:
    """Return inner batchexecute JSON payload for pGCYOe restore mutation."""
    return build_mutation_payload(media_id, project_id, archived=False)


def parse_mutation_response(
    frame: object, expected_media_id: str | None = None, *, archived: bool
) -> dict[str, Any]:
    """Validate and extract trash/restore response from pGCYOe frame."""
    rows = _as_list(frame)
    if not rows or not rows[0]:
        raise WireFormatError(f"Unexpected pGCYOe response frame: {frame!r}", charged="no")
    first_row = _as_list(rows[0])
    if not first_row or not first_row[0]:
        raise WireFormatError(f"Unexpected pGCYOe response frame: {frame!r}", charged="no")
    item = _as_list(first_row[0])
    if not item or len(item) < 1:
        raise WireFormatError(f"Unexpected pGCYOe item structure: {first_row[0]!r}", charged="no")

    media_id = str(item[0]) if item[0] is not None else ""
    if expected_media_id and media_id != expected_media_id:
        raise WireFormatError(
            f"pGCYOe response media ID mismatch: expected {expected_media_id}, got {media_id}",
            charged="no",
        )

    metadata: list[object] | None = _as_list(item[3]) if len(item) > 3 else None
    is_archived: object = metadata[2] if (metadata is not None and len(metadata) > 2) else None

    if archived:
        if is_archived not in (True, 1):
            raise WireFormatError(
                f"pGCYOe response did not set archived flag to True: {is_archived!r}", charged="no"
            )
    else:
        if is_archived not in (None, False, 0):
            raise WireFormatError(
                f"pGCYOe response did not clear archived flag: {is_archived!r}", charged="no"
            )

    project_id = str(item[4]) if len(item) > 4 and item[4] is not None else ""
    workflow_id = (
        str(metadata[4])
        if (metadata is not None and len(metadata) > 4 and metadata[4] is not None)
        else ""
    )
    return {
        "media_id": media_id,
        "project_id": project_id,
        "workflow_id": workflow_id,
        "archived": True if is_archived in (True, 1) else False,
        "status": "trashed" if archived else "restored",
    }


def parse_trash_response(frame: object, expected_media_id: str | None = None) -> dict[str, Any]:
    """Validate and extract trash response from pGCYOe frame.

    Asserts slot [0][0][0] matches expected media ID and slot [0][0][3][2] is True.
    """
    return parse_mutation_response(frame, expected_media_id=expected_media_id, archived=True)


def parse_restore_response(frame: object, expected_media_id: str | None = None) -> dict[str, Any]:
    """Validate and extract restore response from pGCYOe frame.

    Asserts slot [0][0][0] matches expected media ID and slot [0][0][3][2] is None.
    """
    return parse_mutation_response(frame, expected_media_id=expected_media_id, archived=False)


async def page_set_archived(
    page: Page, media_id: str, project_id: str, *, archived: bool
) -> dict[str, Any]:
    """Execute pGCYOe trash/restore mutation inside Playwright page context."""
    await page.goto("https://flow.google.com", wait_until="domcontentloaded", timeout=45_000)
    await raise_if_known_landing(
        page, requested="https://flow.google.com", at="migrated.media_trash"
    )

    raw_eval = await page.evaluate(SCRAPE_WIZ_JS)
    tokens_res: dict[str, str] = (
        cast("dict[str, str]", raw_eval) if isinstance(raw_eval, dict) else {}
    )
    at: str = str(tokens_res.get("at", "")).strip()
    fsid: str | None = str(tokens_res.get("fsid", "")).strip() or None
    bl: str | None = str(tokens_res.get("bl", "")).strip() or None

    if not at:
        try:
            content = await page.content()
        except Exception:  # noqa: BLE001
            content = ""
        at = extract_wiz_token(content, "SNlM0e") or ""
        if not fsid:
            fsid = extract_wiz_token(content, "FdrFJe")
        if not bl:
            bl = extract_wiz_token(content, "cfb2h")

    if not at:
        raise AuthExpiredError(
            "Signed out of Flow: missing 'at' token in page",
            charged="no",
        )

    payload = build_mutation_payload(media_id, project_id, archived=archived)
    tokens = Tokens(at=at, fsid=fsid, bl=bl)
    req = build_rpc_request(TRASH_RPC, tokens, headers={}, payload=payload)

    res = cast(
        "dict[str, Any]",
        await page.evaluate(
            EVAL_FETCH_JS,
            {"url": str(req.url), "body": req.content.decode("utf-8")},
        ),
    )
    status = cast("int", res.get("status", 0))
    text = cast("str", res.get("text", ""))
    if status != 200:
        raise FlowApiError(
            f"HTTP {status} from Flow page evaluation",
            status=status,
            route=TRASH_RPC,
            charged="no",
        )
    frames = [p for r, p in parse_frames(text) if r == TRASH_RPC]
    if not frames:
        errors = rpc_errors(text)
        code = errors[0].code if errors and errors[0].code is not None else None
        action = "trash" if archived else "restore"
        raise FlowApiError(
            f"pGCYOe {action} rejected by Flow (code={code})",
            status=status,
            code=code,
            route=TRASH_RPC,
            charged="no",
        )
    return parse_mutation_response(frames[0], expected_media_id=media_id, archived=archived)


async def page_trash_media(page: Page, media_id: str, project_id: str) -> dict[str, Any]:
    """Execute pGCYOe trash mutation inside Playwright page context."""
    return await page_set_archived(page, media_id, project_id, archived=True)


async def page_restore_media(page: Page, media_id: str, project_id: str) -> dict[str, Any]:
    """Execute pGCYOe restore mutation inside Playwright page context."""
    return await page_set_archived(page, media_id, project_id, archived=False)
