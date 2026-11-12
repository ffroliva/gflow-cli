"""Reference-entity ids in a captured image request body (#1249 ratchet: moved out of
``ui_automation.py`` unchanged, so that file can take the #546 wiring without growing)."""

from __future__ import annotations

import json
from typing import Any, cast

_JsonObj = dict[str, Any]
_AnyList = list[Any]


def entity_ids_from_one_request(req: Any) -> set[str]:
    """Extract entityId strings from a single ``requests[]`` entry."""
    if not isinstance(req, dict):
        return set()
    ents = cast(_JsonObj, req).get("referenceEntities")
    if not isinstance(ents, list):
        return set()
    out: set[str] = set()
    for ent in cast(_AnyList, ents):
        if isinstance(ent, dict):
            entity_id = cast(_JsonObj, ent).get("entityId")
            if isinstance(entity_id, str):
                out.add(entity_id)
    return out


def entity_ids_from_request_body(post_data: str | None) -> set[str]:
    """Entity ids carried by an outgoing ``batchGenerateImages`` body.

    Request shape (live-verified — issue #170 report + movie spike):
    ``requests[].referenceEntities[].entityId``. Feeds the #170 submit
    backstop: a UI attach miss must not degrade to a text-only generation
    reported as success.
    """
    if not post_data:
        return set()
    try:
        parsed: object = json.loads(post_data)
    except (ValueError, TypeError):
        return set()
    if not isinstance(parsed, dict):
        return set()
    out: set[str] = set()
    reqs = cast(_JsonObj, parsed).get("requests")
    if not isinstance(reqs, list):
        return out
    for req in cast(_AnyList, reqs):
        out |= entity_ids_from_one_request(req)
    return out
