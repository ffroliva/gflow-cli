"""Versioned worker-queue payload codec (Task C2, design spec §3).

``generation_queue.payload_json`` stores a caller-supplied dict of top-level
fields. This module is the single place that:

- decodes a payload dict into the existing typed ``GenerateImageRequest`` /
  ``GenerateVideoRequest`` DTOs, validating task discriminator, required
  fields, enums, and bounded counts BEFORE any browser is launched; and
- encodes a decoded payload back to wire form, stamped at the codec's
  current schema version.

Versioning contract: ``schema_version`` is an ADDITIVE top-level key, never
a wrapper — a payload with no ``schema_version`` key is legacy V0 and
decodes through the same field lookups as V1 (the shape is otherwise
identical). Any other version is unknown and raises :class:`QueueSchemaError`
rather than being interpreted optimistically.

The typed-request builders below (``build_image_request`` /
``build_video_request``) are moved here from ``worker/daemon.py`` — the
single mapping from a queue payload dict to the existing request DTOs,
shared by the codec's pre-flight validation and the worker's execution path
so the two can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from gflow_cli.api.image import AgentInstruction, GenerateImageRequest, ImageRef
from gflow_cli.api.image import Aspect as ImageAspect
from gflow_cli.api.image import Model as ImageModel
from gflow_cli.api.video import Aspect as VideoAspect
from gflow_cli.api.video import GenerateVideoRequest, VideoModel
from gflow_cli.api.video import Mode as VideoMode
from gflow_cli.api.video import Tier as VideoTier
from gflow_cli.config import UiMode
from gflow_cli.data.redaction import redact_error_detail
from gflow_cli.errors import QueueSchemaError

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "DecodedPayload",
    "build_image_request",
    "build_video_request",
    "decode_payload",
    "encode_payload",
]

_IMAGE_TASK_TYPES = frozenset({"t2i", "i2i"})
_VIDEO_TASK_TYPES = frozenset({"t2v", "i2v", "r2v"})
_KNOWN_TASK_TYPES = _IMAGE_TASK_TYPES | _VIDEO_TASK_TYPES

# The only version this codec writes. 0 (an absent key) is read-only legacy
# compatibility — nothing ever deliberately encodes as V0.
CURRENT_SCHEMA_VERSION = 1
_KNOWN_SCHEMA_VERSIONS = (0, CURRENT_SCHEMA_VERSION)

# RFC 9457 `route` stamped on every QueueSchemaError raised from this codec.
_CODEC_ROUTE = "worker.queue.codec"


@dataclass(frozen=True)
class DecodedPayload:
    """A validated queue payload, mapped onto its typed request DTO.

    ``schema_version`` is the version AS READ (0 for legacy/missing, 1 for
    explicit) — informational, not prescriptive: :func:`encode_payload`
    always writes the codec's current version. ``fields`` is the original
    top-level payload with ``schema_version`` stripped, kept verbatim so
    :func:`encode_payload` can round-trip without reconstructing a minimal
    dict from a fully-defaulted typed request.
    """

    schema_version: int
    task_type: str
    request: GenerateImageRequest | GenerateVideoRequest
    fields: dict[str, Any]


def decode_payload(task_type: str, payload: dict[str, Any]) -> DecodedPayload:
    """Decode + validate one queue payload BEFORE Playwright starts.

    Raises :class:`QueueSchemaError` for an unknown ``schema_version``, an
    unknown ``task_type`` discriminator, or a payload that fails structural
    validation (missing required field, invalid enum, out-of-range count,
    malformed path) when mapped onto the typed request DTO. The error detail
    is redacted and never echoes prompt text.
    """
    version = payload.get("schema_version", 0)
    if version not in _KNOWN_SCHEMA_VERSIONS:
        raise QueueSchemaError(
            f"unknown schema_version {version!r} (known: {_KNOWN_SCHEMA_VERSIONS})",
            route=_CODEC_ROUTE,
        )
    if task_type not in _KNOWN_TASK_TYPES:
        raise QueueSchemaError(
            f"unknown task_type {task_type!r} (known: {sorted(_KNOWN_TASK_TYPES)})",
            route=_CODEC_ROUTE,
        )

    fields = {k: v for k, v in payload.items() if k != "schema_version"}

    try:
        request: GenerateImageRequest | GenerateVideoRequest = (
            build_image_request(fields)
            if task_type in _IMAGE_TASK_TYPES
            else build_video_request(fields)
        )
    except (KeyError, ValueError, TypeError) as exc:
        detail = redact_error_detail(f"{task_type} payload rejected: {exc}")
        raise QueueSchemaError(detail, route=_CODEC_ROUTE) from exc

    return DecodedPayload(
        schema_version=version, task_type=task_type, request=request, fields=fields
    )


def encode_payload(task_type: str, decoded: DecodedPayload) -> dict[str, Any]:
    """Serialize a decoded payload back to wire form at the CURRENT schema
    version.

    Every freshly encoded payload uses the codec's current version,
    regardless of the version it was decoded from — a V0 legacy payload
    upgrades to V1 on encode.
    """
    del task_type  # kept for symmetry with decode_payload's signature
    return {**decoded.fields, "schema_version": CURRENT_SCHEMA_VERSION}


# ---------------------------------------------------------------------------
# Typed-request builders
# ---------------------------------------------------------------------------


def _instruction_from_dict(item: dict[str, object]) -> AgentInstruction:
    """Build one AgentInstruction from a queue-payload dict item."""
    enabled_val = item.get("enabled")
    return AgentInstruction(
        text=str(item.get("text") or ""),
        enabled=bool(enabled_val) if enabled_val is not None else True,
        image_media_ids=tuple(
            str(m) for m in cast(list[object], item.get("image_media_ids") or [])
        ),
        character_ids=tuple(str(c) for c in cast(list[object], item.get("character_ids") or [])),
        title=str(item.get("title") or ""),
    )


def _parse_agent_instructions(
    instructions_val: object,
) -> tuple[AgentInstruction, ...] | None:
    """Parse queue-payload ``instructions`` into ``AgentInstruction`` objects.

    Accepts a list of plain strings (ephemeral enabled cards) or dicts
    (``text``/``enabled``/``image_media_ids``/``character_ids``/``title``).
    Returns ``None`` when absent or not a list/tuple.
    """
    if not isinstance(instructions_val, (list, tuple)):
        return None
    insts: list[AgentInstruction] = []
    for item in cast(list[object], instructions_val):
        if isinstance(item, str):
            insts.append(AgentInstruction(text=item, enabled=True))
        elif isinstance(item, dict):
            insts.append(_instruction_from_dict(cast(dict[str, object], item)))
    return tuple(insts)


def build_image_request(payload: dict[str, Any]) -> GenerateImageRequest:
    """Map a t2i/i2i queue payload dict onto ``GenerateImageRequest``.

    Raises ``KeyError``/``ValueError``/``TypeError`` on malformed input.
    Callers needing a stable typed failure use :func:`decode_payload`, which
    wraps these into ``QueueSchemaError``.
    """
    prompt = payload["prompt"]

    aspect_val = payload.get("aspect")
    aspect = ImageAspect.from_cli(aspect_val) if aspect_val else ImageAspect.PORTRAIT

    model_val = payload.get("model")
    model = ImageModel.from_cli(model_val) if model_val else ImageModel.NARWHAL

    # ref_meta (set by the MCP layer's _enrich_image_refs) carries the
    # display_name + on-disk local_path per media-id ref, so the transport
    # can select the EXISTING asset in the picker (preferred, no duplicate)
    # and fall back to uploading local_path only if it can't be located.
    ref_meta: dict[str, dict[str, str]] = payload.get("ref_meta", {})
    refs = tuple(
        ImageRef(
            r,
            display_name=ref_meta.get(r, {}).get("display_name", ""),
            local_path=ref_meta.get(r, {}).get("local_path", ""),
        )
        for r in payload.get("refs", [])
    )
    ref_paths = tuple(Path(p) for p in payload.get("ref_paths", []))
    # NOTE: the payload may carry "ref_names" (the MCP layer resolves them
    # for the video request); the image transport attaches remote refs by
    # media id, so GenerateImageRequest has no ref_names field and must
    # not receive one.
    reference_entities = tuple(payload.get("reference_entities", []))
    reference_entity_names = tuple(payload.get("reference_entity_names", []))
    count = payload.get("count", 1)

    return GenerateImageRequest(
        prompt=prompt,
        aspect=aspect,
        model=model,
        refs=refs,
        ref_paths=ref_paths,
        reference_entities=reference_entities,
        reference_entity_names=reference_entity_names,
        count=count,
        instructions=_parse_agent_instructions(payload.get("instructions")),
        ui_mode=UiMode(payload["ui_mode"]) if payload.get("ui_mode") else None,
    )


def build_video_request(payload: dict[str, Any]) -> GenerateVideoRequest:
    """Map a t2v/i2v/r2v queue payload dict onto ``GenerateVideoRequest``.

    Raises ``KeyError``/``ValueError``/``TypeError`` on malformed input.
    Callers needing a stable typed failure use :func:`decode_payload`, which
    wraps these into ``QueueSchemaError``.
    """
    prompt = payload["prompt"]

    mode_val = payload.get("mode")
    mode = VideoMode(mode_val) if mode_val else VideoMode.T2V

    aspect_val = payload.get("aspect")
    aspect = VideoAspect.from_cli(aspect_val) if aspect_val else VideoAspect.PORTRAIT

    tier_val = payload.get("tier")
    tier = VideoTier(tier_val) if tier_val else VideoTier.FAST

    model_val = payload.get("model")
    model = VideoModel.from_cli(model_val) if model_val else None

    duration = payload.get("duration")
    count = payload.get("count", 1)
    seed = payload.get("seed")

    start_image = Path(payload["start_image"]) if payload.get("start_image") else None
    start_image_ref_name = payload.get("start_image_ref_name")
    end_image = Path(payload["end_image"]) if payload.get("end_image") else None
    end_image_ref_name = payload.get("end_image_ref_name")
    reference_images = tuple(Path(p) for p in payload.get("reference_images", []))
    ref_names = tuple(payload.get("ref_names", []))
    reference_entities = tuple(payload.get("reference_entities", []))
    reference_entity_names = tuple(payload.get("reference_entity_names", []))
    reference_audio = payload.get("reference_audio")

    return GenerateVideoRequest(
        prompt=prompt,
        mode=mode,
        aspect=aspect,
        tier=tier,
        model=model,
        duration=duration,
        count=count,
        seed=seed,
        start_image=start_image,
        start_image_ref_name=start_image_ref_name,
        end_image=end_image,
        end_image_ref_name=end_image_ref_name,
        reference_images=reference_images,
        ref_names=ref_names,
        reference_entities=reference_entities,
        reference_entity_names=reference_entity_names,
        reference_audio=reference_audio,
    )
