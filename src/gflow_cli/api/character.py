"""Domain models and parser for Flow Character entities.

Characters are project-scoped entities (entityType=CHARACTER) returned inside
``projectInitialData.projectContents.entities``.  This module is pure data —
no I/O.  The parser extracts only stable ids from the wire; signed URLs
(fifeUrl, thumbnailUrl, etc.) are intentionally dropped so that nothing
persisted contains a credential-bearing URL (scenario #16).

Wire shape discovered in docs/CHARACTER_RECON.md (issue #145).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

__all__ = [
    "Character",
    "CharacterImageRequest",
    "VOICES",
    "parse_characters",
]

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Known preset voice ids for Gemini TTS.
# These are the values observed in audioReferences[].presetVoiceId on the
# wire.  Flow may expose a live-fetch endpoint in the future; until then this
# tuple serves as a validation / completion aid.
# TODO: check whether Flow exposes a /v1/voices (or equivalent) endpoint and
#       replace this static list with a live fetch if so.
# ---------------------------------------------------------------------------
VOICES: tuple[str, ...] = (
    "aoede",
    "callirrhoe",
    "charon",
    "despina",
    "enceladus",
    "fenrir",
    "gacrux",
    "iapetus",
    "kore",
    "leda",
    "orus",
    "puck",
    "rasalgethi",
    "sadachbia",
    "sadaltager",
    "schedar",
    "sulafat",
    "umbriel",
    "vindemiatrix",
    "zephyr",
)


# ---------------------------------------------------------------------------
# Output DTO — holds only stable ids, never signed URLs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Character:
    """A Flow Character entity parsed from ``projectInitialData``.

    Only stable identifiers are stored.  Signed CDN URLs (``fifeUrl``,
    ``thumbnailUrl``, …) present on the wire are intentionally excluded so
    that persisted / logged data never contains credential-bearing URLs
    (scenario #16).
    """

    entity_id: str
    display_name: str
    project_id: str
    workflow_ids: tuple[str, ...]
    voice: str | None
    personality: str | None
    thumbnail_media_id: str | None


# ---------------------------------------------------------------------------
# Input DTO — used by the CLI / saga to describe a character image generation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CharacterImageRequest:
    """Inputs for a single character reference-image generation.

    Under Option B (passive UI capture) gflow never POSTs the generation body
    directly — generation is UI-driven.  This DTO carries the parameters that
    the CLI layer needs to drive the UI automation and to record the request.

    ``aspect`` and ``model`` are kept as plain strings (matching how the CLI
    receives them from Click options) rather than the image-module enums, so
    that this module stays dependency-free and importable without pulling in
    the full image pipeline.  Conversion to wire-enum values is the caller's
    responsibility.

    ``image_reference_index`` is the 0-based index of the existing character
    image reference that will be used as the style anchor (0 = first / only
    reference).
    """

    prompt: str
    aspect: str = "9:16"
    model: str = "narwhal"
    image_reference_index: int = 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_characters(project_initial_data: dict[str, Any]) -> list[Character]:
    """Extract Character entities from a ``projectInitialData`` response.

    Only entities whose ``entityType`` is ``"CHARACTER"`` are returned.
    All other entity types (SCENE, VIDEO, …) are silently skipped.

    Signed URLs present in ``imageReferences[].fifeUrl`` and similar fields
    are intentionally not forwarded to the output DTO (scenario #16).

    Args:
        project_initial_data: The parsed JSON body of a
            ``getProjectInitialData`` response.

    Returns:
        A list of :class:`Character` instances, one per CHARACTER entity,
        in the order they appear in the response.
    """
    out: list[Character] = []
    raw_contents: dict[str, Any] = project_initial_data.get("projectContents") or {}
    raw_entities: list[dict[str, Any]] = raw_contents.get("entities") or []
    for e in raw_entities:
        info: dict[str, Any] = e.get("entityInfo") or {}
        if info.get("entityType") != "CHARACTER":
            continue
        entity_id_raw = e.get("entityId")
        project_id_raw = e.get("projectId")
        if not entity_id_raw:
            log.warning(
                "character.parse_skip_missing_entity_id",
                entity=e,
            )
            continue
        if not project_id_raw:
            log.warning(
                "character.parse_skip_missing_project_id",
                entity_id=entity_id_raw,
                entity=e,
            )
            continue
        ci: dict[str, Any] = info.get("characterInfo") or {}
        image_refs: list[dict[str, Any]] = ci.get("imageReferences") or []
        audio_refs: list[dict[str, Any]] = ci.get("audioReferences") or []
        # Extract only workflowId — never fifeUrl or other signed fields.
        workflow_ids: tuple[str, ...] = tuple(
            str(r["workflowId"]) for r in image_refs if r.get("workflowId")
        )
        voice: str | None = next(
            (str(a["presetVoiceId"]) for a in audio_refs if a.get("presetVoiceId")),
            None,
        )
        personality_raw = ci.get("personalityNotes")
        personality: str | None = str(personality_raw) if personality_raw is not None else None
        thumbnail_raw = e.get("thumbnailMediaId")
        thumbnail_media_id: str | None = str(thumbnail_raw) if thumbnail_raw is not None else None
        out.append(
            Character(
                entity_id=str(entity_id_raw),
                display_name=str(info.get("displayName") or ""),
                project_id=str(project_id_raw),
                workflow_ids=workflow_ids,
                voice=voice,
                personality=personality,
                thumbnail_media_id=thumbnail_media_id,
            )
        )
    return out
