"""Pure value objects and body builders for video generation.

No I/O lives in this module — `FlowApiClient.generate_video()` calls
`build_generate_body()` and POSTs the result.

`model_key()` encodes Flow's wire format for the `videoModelKey` field
(e.g. `veo_3_1_t2v_fast_portrait`) — discovered from sample
`samples/captured/02_batchAsyncGenerateVideoText.json`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Mode(StrEnum):
    T2V = "t2v"
    I2V = "i2v"


class Tier(StrEnum):
    FAST = "fast"
    QUALITY = "quality"


class Aspect(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SQUARE = "square"

    def wire(self) -> str:
        return f"VIDEO_ASPECT_RATIO_{self.value.upper()}"

    @classmethod
    def from_cli(cls, value: str) -> Aspect:
        mapping = {"9:16": cls.PORTRAIT, "16:9": cls.LANDSCAPE, "1:1": cls.SQUARE}
        if value not in mapping:
            raise ValueError(f"Unsupported aspect ratio {value!r}; choose from {sorted(mapping)}")
        return mapping[value]


@dataclass(frozen=True)
class GenerateVideoRequest:
    """Inputs for ONE video generation. T2V if start_asset_uuid is None, else I2V."""

    prompt: str
    aspect: Aspect = Aspect.PORTRAIT
    tier: Tier = Tier.FAST
    start_asset_uuid: str | None = None

    @property
    def mode(self) -> Mode:
        return Mode.I2V if self.start_asset_uuid else Mode.T2V


# Wire-format constants discovered from samples/captured/02_batchAsyncGenerateVideoText.json
_AUDIO_FAILURE_PREF = "BLOCK_SILENCED_VIDEOS"
_CLIENT_TOOL = "PINHOLE"
_PAYGATE_TIER = "PAYGATE_TIER_ONE"
_RECAPTCHA_APP_TYPE = "RECAPTCHA_APPLICATION_TYPE_WEB"


def model_key(mode: Mode, tier: Tier, aspect: Aspect) -> str:
    """Compose Flow's `videoModelKey` wire string."""
    return f"veo_3_1_{mode.value}_{tier.value}_{aspect.value}"


def build_generate_body(
    req: GenerateVideoRequest,
    *,
    project_id: str,
    recaptcha_token: str,
    batch_id: str,
    seed: int,
    session_id: str,
) -> dict[str, Any]:
    """Build the JSON body for `POST /v1/video:batchAsyncGenerateVideoText`.

    Shape mirrors `samples/captured/02_batchAsyncGenerateVideoText.json` —
    every field there is required by the server.
    """
    image_input: dict[str, Any] = (
        {"imageInput": {"mediaId": req.start_asset_uuid}} if req.start_asset_uuid else {}
    )
    request: dict[str, Any] = {
        "aspectRatio": req.aspect.wire(),
        "textInput": {"structuredPrompt": {"parts": [{"text": req.prompt}]}},
        "videoModelKey": model_key(req.mode, req.tier, req.aspect),
        "metadata": {},
        "seed": seed,
        **image_input,
    }
    return {
        "mediaGenerationContext": {
            "batchId": batch_id,
            "audioFailurePreference": _AUDIO_FAILURE_PREF,
        },
        "clientContext": {
            "projectId": project_id,
            "tool": _CLIENT_TOOL,
            "userPaygateTier": _PAYGATE_TIER,
            "sessionId": session_id,
            "recaptchaContext": {
                "token": recaptcha_token,
                "applicationType": _RECAPTCHA_APP_TYPE,
            },
        },
        "requests": [request],
        "useV2ModelConfig": True,
    }
