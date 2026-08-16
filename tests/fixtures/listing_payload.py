"""Synthetic ``flow.projectInitialData`` listing payloads (#543).

Shape live-verified 2026-08-16 (PLAN.md Task S1/S2):

    {"result": {"data": {"json": {"projectContents": {
        "media": [{"name": <media-uuid>, "projectId": ..., "workflowId": ...}],
        "workflows": [{"name": <workflow-uuid>,
                       "metadata": {"displayName": <caption>,
                                    "primaryMediaId": <media-uuid>}}],
        "externalReferenceMedia": [...],
        "agentInfo": {...}}}}}}

Names come ONLY from ``workflows[].metadata.primaryMediaId -> displayName``;
presence ONLY from ``media[].name``. All ids here are synthetic ``uuid4``
values — never real data. Plain builder functions (not pytest fixtures) so
S1/S2/S3 tests import them normally without F811 shadowing.
"""

from __future__ import annotations

import uuid
from typing import Any

JsonObject = dict[str, Any]


def new_id() -> str:
    return str(uuid.uuid4())


def media_item(
    media_id: str,
    *,
    project_id: str | None = None,
    workflow_id: str | None = None,
    extra: JsonObject | None = None,
) -> JsonObject:
    item: JsonObject = {
        "name": media_id,
        "projectId": project_id or new_id(),
        "workflowId": workflow_id or new_id(),
        "mediaType": "MEDIA_TYPE_IMAGE",
    }
    if extra:
        item.update(extra)
    return item


def workflow_item(
    *,
    primary_media_id: str | None = None,
    display_name: str | None = None,
    workflow_id: str | None = None,
    project_id: str | None = None,
) -> JsonObject:
    """Workflow entry; omit ``display_name``/``primary_media_id`` to build the
    degenerate shapes the parser must skip."""
    metadata: JsonObject = {}
    if display_name is not None:
        metadata["displayName"] = display_name
    if primary_media_id is not None:
        metadata["primaryMediaId"] = primary_media_id
    return {
        "name": workflow_id or new_id(),
        "projectId": project_id or new_id(),
        "metadata": metadata,
    }


def listing_payload(
    *,
    media: tuple[JsonObject, ...] | list[JsonObject] = (),
    workflows: tuple[JsonObject, ...] | list[JsonObject] = (),
    external_reference_media: tuple[JsonObject, ...] | list[JsonObject] = (),
    project_contents_extra: JsonObject | None = None,
    json_extra: JsonObject | None = None,
) -> JsonObject:
    """Full tRPC envelope around ``projectContents``.

    ``project_contents_extra`` / ``json_extra`` inject extra keys (e.g.
    pagination markers) at the two envelope levels.
    """
    project_contents: JsonObject = {
        "media": list(media),
        "workflows": list(workflows),
        "externalReferenceMedia": list(external_reference_media),
        "agentInfo": {"agentMode": False},
    }
    if project_contents_extra:
        project_contents.update(project_contents_extra)
    json_body: JsonObject = {"projectContents": project_contents}
    if json_extra:
        json_body.update(json_extra)
    return {"result": {"data": {"json": json_body}}}


def named_pair(display_name: str = "A cozy cabin") -> tuple[str, JsonObject, JsonObject]:
    """One media item plus the workflow that names it.

    Returns ``(media_id, media_item, workflow_item)``.
    """
    media_id = new_id()
    return (
        media_id,
        media_item(media_id),
        workflow_item(primary_media_id=media_id, display_name=display_name),
    )
