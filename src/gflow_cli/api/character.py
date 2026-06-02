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
    "CHARACTER_MODELS",
    "Character",
    "CharacterCreateResult",
    "CharacterImageRequest",
    "VOICES",
    "VOICE_NAMES",
    "Voice",
    "parse_characters",
]

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Character models — the editor exposes EXACTLY two.
# Maps the friendly CLI alias to the model's UI display name (used by the
# UI-automation model picker).  Product names are not localized, so matching
# the option by display text across locales is acceptable.
# ---------------------------------------------------------------------------
CHARACTER_MODELS: dict[str, str] = {
    "nano2": "Nano Banana 2",
    "nanopro": "Nano Banana Pro",
}

# ---------------------------------------------------------------------------
# Gemini voice catalog for Character TTS.
#
# The canonical voice id is the Capitalized UI display name as shown in Flow's
# voice library (e.g. "Sulafat", "Charon").  This is the same token used in the
# sample-audio URL pattern, so the canonical id round-trips to a playable sample
# (see :attr:`Voice.sample_url`).
#
# NOTE: a prior live run sent a LOWERCASE id ("charon") and it persisted, but
# whether Flow applies the voice from a lowercase id vs the Capitalized canonical
# form is UNVERIFIED — we adopt the Capitalized form as canonical per the UI.
#
# TODO(backlog): fetch the live voice list from Flow's API; confirm wire-case for
#       presetVoiceId; support voice creation ("Criar nova voz").
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Voice:
    """A Gemini TTS voice from Flow's character voice library.

    ``name`` is the canonical voice id — the Capitalized UI display name — which
    also drives the sample-audio URL.  ``description`` is the short UI descriptor
    (gender / tone / pitch); it may be ``None`` when not captured.
    """

    name: str
    description: str | None = None

    @property
    def sample_url(self) -> str:
        """gstatic sample-audio URL for this voice (Capitalized name)."""
        return f"https://gstatic.com/aitestkitchen/voices/samples/{self.name}.wav"


VOICES: tuple[Voice, ...] = (
    Voice("Achernar", "Female, soft, high pitch"),
    Voice("Achird", "Male, friendly, mid pitch"),
    Voice("Algenib", "Male, gravelly, low pitch"),
    Voice("Algieba", "Male, easy-going, mid-low pitch"),
    Voice("Alnilam", "Male, firm, mid-low pitch"),
    Voice("Aoede", "Female, breezy, mid pitch"),
    Voice("Autonoe", "Female, bright, mid pitch"),
    Voice("Callirrhoe", "Female, easy-going, mid pitch"),
    Voice("Charon", "Male, informative, lower pitch"),
    Voice("Despina", "Female, smooth, mid pitch"),  # descriptor unverified
    Voice("Erinome", "Female, clear, mid pitch"),
    Voice("Fenrir", "Male, excitable, younger pitch"),
    Voice("Gacrux", "Female, mature, mid pitch"),
    Voice("Iapetus", "Male, clear, mid-low pitch"),
    Voice("Kore", "Female, firm, mid pitch"),
    Voice("Laomedeia", "Female, upbeat, mid-high pitch"),
    Voice("Leda", "Female, youthful, mid-high pitch"),
    Voice("Orus", "Male, firm, mid-low pitch"),
    Voice("Puck", "Male, upbeat, mid pitch"),
    Voice("Pulcherrima", "Ungendered, forward, mid-high pitch"),
    Voice("Rasalgethi", "Male, informative, mid pitch"),
    Voice("Sadachbia", "Male, lively, low pitch"),
    Voice("Sadaltager", "Male, knowledgeable, mid pitch"),
    Voice("Schedar", "Male, even, mid-low pitch"),
    Voice("Sulafat", "Female, warm, mid pitch"),
    Voice("Umbriel", "Male, smooth, lower pitch"),
    Voice("Vindemiatrix", "Female, gentle, mid pitch"),
    Voice("Zephyr", "Female, bright, mid-high pitch"),
    Voice("Zubenelgenubi", "Male, casual, mid-low pitch"),
)

# Convenience tuple of just the canonical names — for validation / back-compat.
VOICE_NAMES: tuple[str, ...] = tuple(v.name for v in VOICES)


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

    Characters have NO aspect-ratio control (the editor exposes no ratio
    picker), so this DTO carries no ``aspect`` field.  ``model`` is the friendly
    CLI alias (``"nano2"`` / ``"nanopro"``, see :data:`CHARACTER_MODELS`) kept as
    a plain string — matching how the CLI receives it from a Click option — so
    that this module stays dependency-free and importable without pulling in the
    full image pipeline.

    ``image_reference_index`` is the 0-based index of the existing character
    image reference that will be used as the style anchor (0 = first / only
    reference).
    """

    prompt: str
    model: str = "nano2"
    image_reference_index: int = 0
    face_media_id: str | None = None
    """mediaId of the face reference image for body-slot reference generation."""


@dataclass(frozen=True)
class CharacterCreateResult:
    """Result DTO returned after a successful character-creation workflow completes.

    ``image_paths`` holds the LOCAL file path of each generated reference image
    (slot 0 = face, slot 1 = body), in slot order.  An entry is ``None`` when the
    image could not be downloaded (e.g. the captured response carried no
    downloadable URL) or when the slot was reused from a prior recovered run.
    These are always local on-disk (or cloud) paths — never the signed CDN
    ``fifeUrl`` (scenario #16).
    """

    entity_id: str
    project_id: str
    workflow_ids: tuple[str, ...]
    primary_media_ids: tuple[str, ...]
    name: str
    voice: str | None = None
    image_paths: tuple[str | None, ...] = ()


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
