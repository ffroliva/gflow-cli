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

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

__all__ = [
    "Aspect",
    "GenerateImageRequest",
    "ImageRef",
    "Model",
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
            raise ValueError(
                f"Unsupported image aspect ratio {cli!r}; choose from {sorted(_ASPECT_FROM_CLI)}"
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
            raise ValueError(
                f"Unknown image model {cli!r}; choose from "
                f"{sorted({m.value for m in cls})} or aliases "
                f"{sorted(_MODEL_FROM_CLI)}"
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
    }
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
    }
)


@dataclass(frozen=True)
class ImageRef:
    """A reference image for I2I, identified by the upload's media UUID.

    The wire shape is the dict returned by :meth:`to_wire`. The UUID comes
    from a prior ``/v1/flow/uploadImage`` call (see
    ``samples/captured/01_upload_image.json``).
    """

    name: str

    def __post_init__(self) -> None:
        # Reject empty, whitespace-only, AND whitespace-padded UUIDs.
        # Padded values would emit garbage on the wire; we don't auto-strip
        # because frozen dataclasses can't mutate, and explicit reject is
        # clearer than silently accepting malformed input.
        if not self.name or self.name != self.name.strip():
            raise ValueError(
                "ImageRef.name must be a non-empty UUID string with no "
                "leading or trailing whitespace"
            )

    def to_wire(self) -> dict[str, str]:
        return {"imageInputType": _IMAGE_INPUT_TYPE_REFERENCE, "name": self.name}


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
    recaptcha_token: str = ""  # populated by caller right before send; "" means unminted
    # number of images to generate (1–4); UI transport uses this to set Flow's count tab
    count: int = 1

    def __post_init__(self) -> None:
        if not self.prompt or not self.prompt.strip():
            raise ValueError("GenerateImageRequest.prompt must be non-empty")
        if not 1 <= self.count <= 4:
            raise ValueError(f"GenerateImageRequest.count must be 1–4, got {self.count}")


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
    return {
        "clientContext": cc,
        "mediaGenerationContext": {"batchId": batch_id},
        "useNewMedia": True,
        "requests": [request],
    }
