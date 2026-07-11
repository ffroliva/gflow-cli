"""Pure value objects and body builder for image upscaling (issue #171).

No I/O lives here — ``FlowApiClient.upsample_image()`` mints a reCAPTCHA token,
calls :func:`build_upsample_image_body`, POSTs to ``/v1/flow/upsampleImage``, and
decodes the ``{"encodedImage": <base64>}`` response.

Wire facts (reverse-engineered, see ``docs/IMAGE_UPSCALE_RECON.md``)::

    POST https://aisandbox-pa.googleapis.com/v1/flow/upsampleImage
    {
      "mediaId": "<source-image-uuid>",
      "targetResolution": "UPSAMPLE_IMAGE_RESOLUTION_2K",   # or _4K
      "clientContext": {"recaptchaContext": {"token": "<reCAPTCHA-Enterprise-token>"}}
    }
    -> 200 {"encodedImage": "<base64>"}   (synchronous; reCAPTCHA mandatory)

1K is the original image and needs no API call. 4K is Ultra-tier-gated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from gflow_cli.api.video import is_media_uuid

__all__ = [
    "DEFAULT_PAYGATE_TIER",
    "TargetResolution",
    "UpsampleImageRequest",
    "build_upsample_image_body",
]

# Wire constants — same values the image-generation clientContext uses.
_RECAPTCHA_APP_TYPE = "RECAPTCHA_APPLICATION_TYPE_WEB"
_CLIENT_TOOL = "PINHOLE"

# Client-reported telemetry the working UI call carries in clientContext. The
# server enforces the REAL account tier independently (a non-Ultra 4K request
# 403s regardless of this value — see UpscaleUnavailableError), so this is a
# best-effort default, NOT a security control. Observed value on a Pro account.
DEFAULT_PAYGATE_TIER = "PAYGATE_TIER_ONE"

# Strict UUID allowlist for the source mediaId and the owning projectId. Both
# are interpolated into the request body (not a URL path), but validating up
# front rejects malformed input before any reCAPTCHA mint or network call fires


class TargetResolution(StrEnum):
    """Upscale target resolution — wire string ``UPSAMPLE_IMAGE_RESOLUTION_*``.

    ``RES_2K`` confirmed live (captured 2026-06-11). ``RES_4K`` follows the same
    naming and is Ultra-tier-gated (a non-Ultra account 403s — see
    :class:`gflow_cli.errors.UpscaleUnavailableError`). 1K is the original image
    and is intentionally NOT an enum member: there is no upscale call for it.
    """

    RES_2K = "UPSAMPLE_IMAGE_RESOLUTION_2K"
    RES_4K = "UPSAMPLE_IMAGE_RESOLUTION_4K"

    @classmethod
    def from_cli(cls, cli: str) -> TargetResolution:
        """Map a friendly CLI string (``"2k"`` / ``"4k"``) to the wire enum.

        Case-insensitive after stripping whitespace. ``"1k"`` is rejected with a
        dedicated hint (it is the original, not an upscale); any other value is
        rejected as an unknown resolution.
        """
        key = cli.strip().lower()
        if key == "1k":
            msg = (
                "1k is the original image, not an upscale target; "
                "download the original instead, or choose 2k or 4k"
            )
            raise ValueError(msg)
        if key not in _RESOLUTION_FROM_CLI:
            choices = sorted(_RESOLUTION_FROM_CLI)
            msg = f"Unsupported upscale resolution {cli!r}; choose from {choices}"
            raise ValueError(msg)
        return _RESOLUTION_FROM_CLI[key]


_RESOLUTION_FROM_CLI: MappingProxyType[str, TargetResolution] = MappingProxyType(
    {
        "2k": TargetResolution.RES_2K,
        "4k": TargetResolution.RES_4K,
    }
)


@dataclass(frozen=True)
class UpsampleImageRequest:
    """Inputs for ONE image upscale.

    ``project_id`` is the project that OWNS ``media_id`` — the live wire requires
    it inside ``clientContext`` (a minimal body without it 403s even with a valid
    reCAPTCHA token; confirmed by live smoke 2026-06-11). It is resolved from the
    local catalog or an explicit ``--project`` before the request is built.

    ``recaptcha_token`` is populated by the caller right before send via
    ``dataclasses.replace(req, recaptcha_token=minted_token)`` — the empty-string
    default means "unminted", mirroring :class:`GenerateImageRequest`, so the
    expiry contract is visible at the call site rather than buried in the builder.
    """

    media_id: str
    project_id: str
    target_resolution: TargetResolution
    recaptcha_token: str = field(default="")

    def __post_init__(self) -> None:
        if not is_media_uuid(self.media_id):
            msg = (
                f"UpsampleImageRequest.media_id must be a bare UUID "
                f"(8-4-4-4-12 hex, no whitespace); got {self.media_id!r}"
            )
            raise ValueError(msg)
        if not is_media_uuid(self.project_id):
            msg = (
                f"UpsampleImageRequest.project_id must be a bare UUID "
                f"(8-4-4-4-12 hex, no whitespace); got {self.project_id!r}"
            )
            raise ValueError(msg)


def build_upsample_image_body(
    req: UpsampleImageRequest,
    *,
    session_id: str,
    user_paygate_tier: str = DEFAULT_PAYGATE_TIER,
) -> dict[str, Any]:
    """Build the JSON body for ``POST /v1/flow/upsampleImage``.

    Mirrors the captured working-call wire shape: the ``clientContext`` carries
    ``recaptchaContext`` + ``projectId`` + ``sessionId`` + ``tool`` +
    ``userPaygateTier`` (a minimal ``{recaptchaContext}`` body 403s — confirmed by
    live smoke). The caller MUST attach a freshly minted reCAPTCHA token via
    ``dataclasses.replace(req, recaptcha_token=token)`` before calling this —
    a 403 is returned without a valid token (REST-only is dead).
    """
    return {
        "mediaId": req.media_id,
        "targetResolution": req.target_resolution.value,
        "clientContext": {
            "recaptchaContext": {
                "token": req.recaptcha_token,
                "applicationType": _RECAPTCHA_APP_TYPE,
            },
            "projectId": req.project_id,
            "sessionId": session_id,
            "tool": _CLIENT_TOOL,
            "userPaygateTier": user_paygate_tier,
        },
    }
