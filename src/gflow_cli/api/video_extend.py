"""Veo *extend* — model resolution and request body. Pure: no I/O, no Playwright.

Extend continues an existing clip for another 8 seconds, seeded server-side from
a window of the source media, so motion and audio carry across the join. That is
the difference from `chain`, which extracts a still locally and restarts from it.

Wire shape and every constant here were captured live on 2026-08-31 and verified
by replaying the body through our own transport (HTTP 200, 10 credits):
`docs/superpowers/spikes/2026-08-31-veo-extend-route-recon.md`.

**Why the model key is resolved at runtime rather than pinned.** Flow's extend
family is tier-gated, and the account's own `flow.projectInitialData` response is
the only source of truth for what it may order:

    veo_3_1_extension_lite            LANDSCAPE+PORTRAIT   ADV 5   INT 10   ENT 10
    veo_3_1_extend_fast_{l,p}         one aspect each      ADV --   INT 20   ENT 20
    veo_3_1_extend_fast_{l,p}_ultra   one aspect each      ADV 10   INT --   ENT --
    veo_3_1_extend_{l,p}              one aspect each      ADV 100  INT 100  ENT 100

("--" is a literal `"UNAVAILABLE"` cost.) The third-party CLI that prompted this
feature pins `veo_3_1_extend_fast_*_ultra` — ADVANCED-only — so on any other tier
every one of its requests is unorderable. Hardcoding is the bug; resolving is the
fix. Note also that the UI label ("Extend (Veo 3.1 - Lite)") maps to no key at
all, so a label is never a key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, cast

from gflow_cli.errors import ExtendUnavailableError

__all__ = [
    "DEFAULT_FRAME_RANGE",
    "ExtendVideoRequest",
    "FrameRange",
    "resolve_extend_model",
]

# Wire constants — not settings. They belong beside the request they serve, in the
# same spirit as `image_upscale.DEFAULT_PAYGATE_TIER`, and are not user-tunable.
_CLIENT_TOOL = "PINHOLE"
_PAYGATE_TIER = "PAYGATE_TIER_ONE"
_RECAPTCHA_APP_TYPE = "RECAPTCHA_APPLICATION_TYPE_WEB"
_AUDIO_FAILURE_PREFERENCE = "RETURN_SILENCED_VIDEOS"
_EXTENSION_REQUIREMENT = "VIDEO_REQUIREMENT_EXTENSION"

_UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)

# Flow has no SQUARE extend model in either family, so 1:1 is rejected up front
# rather than surfacing as an opaque "nothing orderable".
_ASPECT_WIRE = {
    "16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "landscape": "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT",
    "portrait": "VIDEO_ASPECT_RATIO_PORTRAIT",
}
_ASPECT_CAPABILITY = {
    "16:9": "LANDSCAPE",
    "landscape": "LANDSCAPE",
    "9:16": "PORTRAIT",
    "portrait": "PORTRAIT",
}


@dataclass(frozen=True, slots=True)
class FrameRange:
    """The window of the source clip the extension is seeded from.

    Captured value is ``1..24``. The source renders at 24 fps, so that is exactly
    **1.0 second** — not the whole 8s (192-frame) clip. Whether index 1 is counted
    from the head or the tail is not yet established, so we send what Flow sends
    and do not expose this as a knob: an uncomprehended wire integer promoted to a
    CLI flag would be frozen into the public surface (and, per the MCP schema
    symmetry rule, into a tool schema as well).
    """

    start: int = 1
    end: int = 24

    def __post_init__(self) -> None:
        if self.start < 1 or self.end < self.start:
            msg = f"invalid frame range {self.start}..{self.end}"
            raise ValueError(msg)


DEFAULT_FRAME_RANGE = FrameRange()


def resolve_extend_model(listing: object, *, service_tier: str, aspect: str) -> str:
    """Pick the extend model key this account may actually order.

    Mirrors Flow's own choice: among models that (a) declare
    ``VIDEO_REQUIREMENT_EXTENSION``, (b) support the requested aspect, and (c)
    carry an integer cost on ``service_tier``, take the cheapest.

    ``_low_priority`` variants are excluded even though one costs 0 on ADVANCED:
    they trade queue position for price, Flow's own UI does not select them, and
    an unbounded wait is a poor default for a chained run. A `--priority` flag can
    surface them later if anyone asks.

    Raises :class:`ExtendUnavailableError` rather than falling back to a pinned
    key — a key the account cannot order 403s on every attempt.
    """
    capability = _ASPECT_CAPABILITY.get(aspect.lower())
    if capability is None:
        msg = (
            f"aspect {aspect!r} has no extend model — Flow offers extend for 16:9 "
            f"and 9:16 only (there is no square variant)"
        )
        raise ExtendUnavailableError(msg)

    # `listing` is the raw tRPC envelope — untrusted JSON, narrowed not assumed.
    raw_models = (
        cast("dict[str, Any]", listing).get("videoModels") if isinstance(listing, dict) else None
    )
    if not isinstance(raw_models, list):
        msg = "projectInitialData carried no videoModels list; cannot resolve an extend model"
        raise ExtendUnavailableError(msg)
    models = cast("list[Any]", raw_models)

    best_key = ""
    best_cost: int | None = None
    for raw_entry in models:
        if not isinstance(raw_entry, dict):
            continue
        entry = cast("dict[str, Any]", raw_entry)
        key = entry.get("key")
        if not isinstance(key, str) or key.endswith("_low_priority"):
            continue
        requirements = cast("list[Any]", entry.get("requirements") or [])
        if not any(
            isinstance(group, list) and _EXTENSION_REQUIREMENT in cast("list[Any]", group)
            for group in requirements
        ):
            continue
        aspects = cast("list[Any]", entry.get("supportedAspectRatios") or [])
        if capability not in aspects:
            continue
        mapping = cast("dict[str, Any]", entry.get("creditMapping") or {})
        tier_entry = cast("dict[str, Any]", mapping.get(service_tier) or {})
        cost = tier_entry.get("cost")
        # A tier the account cannot order reads the literal string "UNAVAILABLE".
        # `bool` is an `int` subclass, so exclude it explicitly.
        if not isinstance(cost, int) or isinstance(cost, bool):
            continue
        if best_cost is None or cost < best_cost:
            best_key, best_cost = key, cost

    if not best_key:
        msg = (
            f"no extend model is orderable for tier {service_tier!r} at aspect {aspect!r} "
            f"({len(models)} models offered)"
        )
        raise ExtendUnavailableError(msg)
    return best_key


@dataclass(frozen=True, slots=True)
class ExtendVideoRequest:
    """One extend submission. Validated on construction, before any token is minted.

    Validating first matters: a reCAPTCHA token is single-use with a ~2 minute TTL
    and minting it is itself a scored action, so a malformed id must fail before
    we spend one.
    """

    media_id: str
    project_id: str
    scene_id: str
    position: int
    prompt: str
    model_key: str
    aspect: str
    seed: int | None = None
    frame_range: FrameRange = field(default=DEFAULT_FRAME_RANGE)

    def __post_init__(self) -> None:
        for label, value in (
            ("media_id", self.media_id),
            ("project_id", self.project_id),
            ("scene_id", self.scene_id),
        ):
            if not _UUID_RE.match(value):
                msg = f"{label} must be a UUID, got {value!r}"
                raise ValueError(msg)
        if not self.prompt.strip():
            # requirements is [TEXT, EXTENSION] — the wire mandates prompt text.
            msg = "prompt must not be empty — the extend route requires text input"
            raise ValueError(msg)
        if self.position < 0:
            msg = f"position must be >= 0, got {self.position}"
            raise ValueError(msg)
        if self.aspect.lower() not in _ASPECT_WIRE:
            msg = f"unsupported aspect {self.aspect!r} for extend"
            raise ValueError(msg)

    def to_wire(self, *, session_id: str, token: str, batch_id: str) -> dict[str, Any]:
        """Build the request body, byte-shaped like the one Flow's own UI sends."""
        request: dict[str, Any] = {
            "aspectRatio": _ASPECT_WIRE[self.aspect.lower()],
            "textInput": {"structuredPrompt": {"parts": [{"text": self.prompt}]}},
            "videoModelKey": self.model_key,
            "metadata": {"sceneId": self.scene_id},
            "videoInput": {
                "mediaId": self.media_id,
                "startFrameIndex": self.frame_range.start,
                "endFrameIndex": self.frame_range.end,
            },
        }
        if self.seed is not None:
            request["seed"] = self.seed
        return {
            "mediaGenerationContext": {
                "batchId": batch_id,
                "audioFailurePreference": _AUDIO_FAILURE_PREFERENCE,
                "sceneContext": {"sceneId": self.scene_id, "position": self.position},
            },
            "clientContext": {
                "projectId": self.project_id,
                "tool": _CLIENT_TOOL,
                "userPaygateTier": _PAYGATE_TIER,
                "sessionId": session_id,
                "recaptchaContext": {"token": token, "applicationType": _RECAPTCHA_APP_TYPE},
            },
            "requests": [request],
            "useV2ModelConfig": True,
        }
