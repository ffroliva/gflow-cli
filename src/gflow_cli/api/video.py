"""Value objects for video generation.

This module is pure — no I/O. The video transport drives Flow's editor UI;
Flow's own JavaScript builds and sends the generate request, so this module
no longer carries HTTP body builders (the 401-dead HTTP video path was
retired — see the Phase A plan).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
    """Inputs for ONE video generation. T2V if start_asset_uuid is None, else I2V.

    NOTE: replaced by an explicit-`mode`, validated value object in Phase A
    Task 6 — this shape is transitional.
    """

    prompt: str
    aspect: Aspect = Aspect.PORTRAIT
    tier: Tier = Tier.FAST
    start_asset_uuid: str | None = None

    @property
    def mode(self) -> Mode:
        return Mode.I2V if self.start_asset_uuid else Mode.T2V
