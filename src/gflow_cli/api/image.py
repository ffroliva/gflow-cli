"""Pure value objects and body builders for image generation.

No I/O lives in this module — `FlowApiClient.generate_images()` (added in a
later task) calls `_build_batch_generate_images_body()` and POSTs the result.

Wire format discovered from the captured samples:
- `samples/captured/06_batchGenerateImages.json` (T2I baseline, 9:16, NARWHAL)
- `samples/captured/07_batchGenerateImages_seeded.json` (I2I, 4:3, NARWHAL)

Key wire-format observations:
- `clientContext` is duplicated at the top level AND inside `requests[0]`,
  with the SAME recaptcha token in both places.
- `useNewMedia: true` is set at the top level.
- `imageInputs` is always present in `requests[0]` — empty list for T2I,
  populated for I2I. Never missing, never null.
- Multi-image quantity is encoded by the caller as N parallel POSTs sharing
  one `batchId` but each with its own seed and reCAPTCHA token. This module
  only builds ONE request body at a time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from gflow_cli.tools.invocation import AppliedTool

__all__ = [
    "Aspect",
    "GenerateImageRequest",
    "ImageRef",
    "AgentInstruction",
    "Model",
    "build_agent_brief_cards",
    "_build_batch_generate_images_body",
]

# Wire-format constants confirmed from samples 06 and 07.
_CLIENT_TOOL = "PINHOLE"
_RECAPTCHA_APP_TYPE = "RECAPTCHA_APPLICATION_TYPE_WEB"
_IMAGE_INPUT_TYPE_REFERENCE = "IMAGE_INPUT_TYPE_REFERENCE"


class Aspect(StrEnum):
    """Image aspect ratio — wire format `IMAGE_ASPECT_RATIO_*`.

    PORTRAIT (9:16) and LANDSCAPE_FOUR_THREE (4:3) and SQUARE (1:1) confirmed
    by samples. LANDSCAPE (16:9) and PORTRAIT_THREE_FOUR (3:4) inferred from
    naming pattern; verify on first capture.
    """

    PORTRAIT = "IMAGE_ASPECT_RATIO_PORTRAIT"
    # inferred by naming pattern; verify on first capture
    LANDSCAPE = "IMAGE_ASPECT_RATIO_LANDSCAPE"
    SQUARE = "IMAGE_ASPECT_RATIO_SQUARE"
    LANDSCAPE_FOUR_THREE = "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE"
    # inferred by naming pattern; verify on first capture
    PORTRAIT_THREE_FOUR = "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR"

    @classmethod
    def from_cli(cls, cli: str | None) -> Aspect:
        """Map a friendly CLI string (e.g. `"9:16"`) to the enum.

        Defaults to PORTRAIT when ``cli`` is ``None`` to mirror the captured
        sample's default. Raises ``ValueError`` for any unknown ratio.
        """
        if cli is None:
            return cls.PORTRAIT
        if cli not in _ASPECT_FROM_CLI:
            msg = f"Unsupported image aspect ratio {cli!r}; choose from {sorted(_ASPECT_FROM_CLI)}"
            raise ValueError(
                msg,
            )
        return _ASPECT_FROM_CLI[cli]


class Model(StrEnum):
    """Image model — wire string sent as `imageModelName`.

    Confirmed values from sample 06/07: ``NARWHAL`` (a.k.a. Nano Banana 2).
    Other values are inferred from Flow's UI labels and will be verified on
    first capture.
    """

    NARWHAL = "NARWHAL"
    GEM_PIX_2 = "GEM_PIX_2"
    IMAGEN_3_5 = "IMAGEN_3_5"

    @classmethod
    def from_cli(cls, cli: str | None) -> Model:
        """Map a friendly CLI alias to the wire enum.

        Defaults to NARWHAL when ``cli`` is ``None``. Aliases are matched
        case-insensitively after stripping whitespace.
        """
        if cli is None:
            return cls.NARWHAL
        key = cli.strip().lower()
        if key not in _MODEL_FROM_CLI:
            msg = (
                f"Unknown image model {cli!r}; choose from "
                f"{sorted({m.value for m in cls})} or aliases "
                f"{sorted(_MODEL_FROM_CLI)}"
            )
            raise ValueError(
                msg,
            )
        return _MODEL_FROM_CLI[key]


# Module-level immutable alias maps — built once, not per-call.
_ASPECT_FROM_CLI: Mapping[str, Aspect] = MappingProxyType(
    {
        "9:16": Aspect.PORTRAIT,
        "16:9": Aspect.LANDSCAPE,
        "1:1": Aspect.SQUARE,
        "4:3": Aspect.LANDSCAPE_FOUR_THREE,
        "3:4": Aspect.PORTRAIT_THREE_FOUR,
    },
)

_MODEL_FROM_CLI: Mapping[str, Model] = MappingProxyType(
    {
        # NARWHAL — Nano Banana 2
        "narwhal": Model.NARWHAL,
        "nano2": Model.NARWHAL,
        "nano-banana-2": Model.NARWHAL,
        "nano_banana_2": Model.NARWHAL,
        "nanobanana2": Model.NARWHAL,
        # GEM_PIX_2 — Nano Pro
        "gem_pix_2": Model.GEM_PIX_2,
        "gem-pix-2": Model.GEM_PIX_2,
        "nano-pro": Model.GEM_PIX_2,
        "nano_pro": Model.GEM_PIX_2,
        "nanopro": Model.GEM_PIX_2,
        # IMAGEN_3_5 — Imagen 4 family alias
        "imagen_3_5": Model.IMAGEN_3_5,
        "imagen-3-5": Model.IMAGEN_3_5,
        "image4": Model.IMAGEN_3_5,
        "imagen4": Model.IMAGEN_3_5,
    },
)

# Per-model I2I reference-image cap (live-observed). Flow silently keeps only
# the first N references when more are attached, so the request is rejected up
# front rather than letting the caller believe every ref was used. NARWHAL
# (Nano Banana 2) and GEM_PIX_2 (Nano Pro) accept 10; IMAGEN_3_5 (Imagen 4)
# accepts 3. MAX_IMAGE_REFERENCES is the ceiling for any (incl. future) model.
MAX_IMAGE_REFERENCES = 10
_IMAGE_REFERENCE_CAP: Mapping[Model, int] = MappingProxyType(
    {
        Model.NARWHAL: 10,
        Model.GEM_PIX_2: 10,
        Model.IMAGEN_3_5: 3,
    },
)


def reference_cap_for(model: Model) -> int:
    """Maximum number of I2I reference images *model* accepts.

    Unknown/future models fall back to :data:`MAX_IMAGE_REFERENCES` rather than
    raising, so adding a `Model` member without a cap entry degrades to the
    ceiling instead of a `KeyError` at request-build time.
    """
    return _IMAGE_REFERENCE_CAP.get(model, MAX_IMAGE_REFERENCES)


def model_aliases(model: Model) -> list[str]:
    """Sorted CLI aliases that resolve to *model* (for `gflow models`)."""
    return sorted(alias for alias, m in _MODEL_FROM_CLI.items() if m is model)


def aspect_choices() -> dict[str, str]:
    """Map each accepted CLI aspect ratio to its wire value."""
    return {ratio: aspect.value for ratio, aspect in _ASPECT_FROM_CLI.items()}


@dataclass(frozen=True)
class ImageRef:
    """A reference image for I2I, identified by the upload's media UUID.

    The wire shape is the dict returned by :meth:`to_wire`. The UUID comes
    from a prior ``/v1/flow/uploadImage`` call OR a generated image's media id
    (see ``samples/captured/01_upload_image.json``).

    ``display_name`` and ``local_path`` are UI-automation-only hints (ignored by
    :meth:`to_wire`): they let the transport attach the ref by **selecting the
    already-existing Flow asset in the picker** — located by ``name`` (the media
    UUID) in the tile's image URL, surfaced by ``display_name`` when a search is
    needed — and preferred over re-uploading a duplicate. ``local_path`` is the
    fallback file to upload only when the asset can't be located in place.
    """

    name: str
    display_name: str = ""
    local_path: str = ""

    def __post_init__(self) -> None:
        # Reject empty, whitespace-only, AND whitespace-padded UUIDs.
        # Padded values would emit garbage on the wire; we don't auto-strip
        # because frozen dataclasses can't mutate, and explicit reject is
        # clearer than silently accepting malformed input.
        if not self.name or self.name != self.name.strip():
            msg = (
                "ImageRef.name must be a non-empty UUID string with no "
                "leading or trailing whitespace"
            )
            raise ValueError(
                msg,
            )

    def to_wire(self) -> dict[str, str]:
        return {"imageInputType": _IMAGE_INPUT_TYPE_REFERENCE, "name": self.name}


@dataclass(frozen=True)
class AgentInstruction:
    """A custom instruction card for Google Flow's Agent Mode.

    ``title`` is the card's human-readable label. Flow's ``agentInfo`` PATCH
    **persists distinct per-card titles** (confirmed by live capture 2026-07-08),
    and Task 7's ``gflow instructions`` CRUD matches cards by title — so every
    card must carry its own title, never a shared constant. When ``title`` is
    left blank (e.g. an ephemeral ``-i "text"`` card) :meth:`resolved_title`
    derives a distinct label from the text so cards stay distinguishable in the
    project brief.
    """

    text: str
    enabled: bool = True
    image_media_ids: tuple[str, ...] = ()
    character_ids: tuple[str, ...] = ()
    title: str = ""

    def __post_init__(self) -> None:
        if not self.text or not self.text.strip():
            msg = "AgentInstruction.text must be a non-empty string"
            raise ValueError(msg)

    def resolved_title(self) -> str:
        """Return the card's title, deriving one from ``text`` when blank.

        The derivation takes the first line of ``text`` and caps it so a bare
        ``-i "…"`` card still gets a distinct, human-readable label instead of
        the old hardcoded ``"Instruction title"`` that collapsed every card to
        one name (breaking title-based lookup).
        """
        title = self.title.strip()
        if title:
            return title
        first_line = self.text.strip().splitlines()[0].strip()
        return f"{first_line[:57]}…" if len(first_line) > 58 else first_line  # noqa: PLR2004


def build_agent_brief_cards(
    instructions: tuple[AgentInstruction, ...], *, project_id: str
) -> list[dict[str, Any]]:
    """Serialize instruction cards for the ``agentInfo`` ``projectBrief.cards`` PATCH.

    Single source of truth shared by ``FlowApiClient.patch_agent_info`` and the
    agentic driver's reconcile path so the wire shape — and the per-card
    ``title`` (see :meth:`AgentInstruction.resolved_title`) — can never drift
    between the two. Character ids are expanded to the project-scoped entity
    resource names Flow expects.
    """
    cards: list[dict[str, Any]] = []
    for inst in instructions:
        card: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "title": inst.resolved_title(),
            "description": inst.text,
            "enabled": inst.enabled,
        }
        if inst.image_media_ids:
            card["imageReferenceMediaIds"] = list(inst.image_media_ids)
        if inst.character_ids:
            card["characterReferenceEntityNames"] = [
                f"projects/{project_id}/entities/{cid}" for cid in inst.character_ids
            ]
        cards.append(card)
    return cards


@dataclass(frozen=True)
class GenerateImageRequest:
    """Inputs for ONE image generation.

    T2I when ``refs`` is empty, I2I otherwise. Quantity > 1 is the caller's
    responsibility — fan out into N parallel calls sharing a batch_id.

    ``recaptcha_token`` is populated by the caller right before send via
    ``dataclasses.replace(req, recaptcha_token=minted_token)``. The empty
    string default means "unminted" — the caller is responsible for minting
    a fresh token and attaching it; the expiry contract is visible at the
    call site rather than buried inside the body builder.
    """

    prompt: str
    aspect: Aspect = Aspect.PORTRAIT
    model: Model = Model.NARWHAL
    refs: tuple[ImageRef, ...] = ()
    # Local reference-image files for I2I — attached through the editor's media
    # dialog by the ui_automation transport (the REST uploadImage path 401s).
    # The common i2i case; `refs` (pre-uploaded UUIDs) would need library-select.
    ref_paths: tuple[Path, ...] = ()
    # Flow CHARACTER entity ids to reference (project-scoped, attached via the
    # Add-Media → Personagens picker by the ui_automation transport). Entities
    # keep generated subjects consistent with a locked character. They count
    # toward the same per-model reference cap as image refs.
    reference_entities: tuple[str, ...] = ()
    # Optional display names paired with reference_entities (used by the UI
    # picker when an id-keyed tile can't be located by id alone).
    reference_entity_names: tuple[str, ...] = ()
    recaptcha_token: str = ""  # populated by caller right before send; "" means unminted
    # number of images to generate (1–4); UI transport uses this to set Flow's count tab
    count: int = 1
    # Custom instructions for Flow's Agent Mode (only applicable when agentic UI cohort is active)
    instructions: tuple[AgentInstruction, ...] | None = None
    # Tool provenance (recorded, never sent on the wire). ``original_prompt`` is
    # the user's pre-tool text when a ``--tool`` rewrote ``prompt``; ``tool`` is
    # the applied-tool snapshot for ``operations.metadata_json.tool``. Both are
    # ignored by the body builders. (PR2 §8)
    original_prompt: str | None = None
    tool: AppliedTool | None = None

    def __post_init__(self) -> None:
        if not self.prompt or not self.prompt.strip():
            msg = "GenerateImageRequest.prompt must be non-empty"
            raise ValueError(msg)
        if not 1 <= self.count <= 4:
            msg = f"GenerateImageRequest.count must be 1–4, got {self.count}"
            raise ValueError(msg)
        n_refs = len(self.refs) + len(self.ref_paths) + len(self.reference_entities)
        cap = reference_cap_for(self.model)
        if n_refs > cap:
            msg = f"{self.model.value} allows at most {cap} reference(s); got {n_refs}"
            raise ValueError(
                msg,
            )


def _client_context(*, project_id: str, recaptcha_token: str, session_id: str) -> dict[str, Any]:
    """Build the `clientContext` block — used both at root and per-request."""
    return {
        "recaptchaContext": {
            "token": recaptcha_token,
            "applicationType": _RECAPTCHA_APP_TYPE,
        },
        "projectId": project_id,
        "tool": _CLIENT_TOOL,
        "sessionId": session_id,
    }


def _build_batch_generate_images_body(
    req: GenerateImageRequest,
    *,
    project_id: str,
    batch_id: str,
    seed: int,
    session_id: str,
) -> dict[str, Any]:
    """Build the JSON body for `POST /v1/projects/{id}/flowMedia:batchGenerateImages`.

    Shape mirrors ``samples/captured/06_batchGenerateImages.json`` and
    ``07_batchGenerateImages_seeded.json``. Every field present in those
    samples is required; nothing extra is added.

    The reCAPTCHA token is read from ``req.recaptcha_token``. Callers MUST
    attach a freshly minted token via ``dataclasses.replace(req,
    recaptcha_token=token)`` before calling this function.
    """
    cc = _client_context(
        project_id=project_id,
        recaptcha_token=req.recaptcha_token,
        session_id=session_id,
    )
    request: dict[str, Any] = {
        # `clientContext` is duplicated inside the request — same shape, same token.
        "clientContext": _client_context(
            project_id=project_id,
            recaptcha_token=req.recaptcha_token,
            session_id=session_id,
        ),
        "imageModelName": req.model.value,
        "imageAspectRatio": req.aspect.value,
        "structuredPrompt": {"parts": [{"text": req.prompt}]},
        "seed": seed,
        # Always present — empty list for T2I, populated for I2I.
        "imageInputs": [r.to_wire() for r in req.refs],
    }
    # Character entity references. Shape confirmed by live capture (2026-06-08):
    # the image submit carries `referenceEntities: [{"entityId": <id>}]`. Added
    # only when present so plain t2i/i2i bodies stay byte-identical to the
    # captured samples. The ui_automation transport attaches entities via the
    # Personagens picker and lets Flow's JS build this; non-UI transports rely
    # on this serialization.
    if req.reference_entities:
        request["referenceEntities"] = [{"entityId": e} for e in req.reference_entities]
    return {
        "clientContext": cc,
        "mediaGenerationContext": {"batchId": batch_id},
        "useNewMedia": True,
        "requests": [request],
    }
