"""Pure prompt-composition core for gflow movie (no I/O).

Holds the structured style + character model, the framing vocabulary, the
deterministic prompt composer, and the handoff-manifest projection. Imports
nothing from the Flow API or browser layers — it is `(data) -> value` and is
the reusable seam a future second consumer (e.g. remotion) can import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class StyleSpec:
    """Global guiding prompt — every field optional, reused verbatim per scene."""

    look: str | None = None
    palette: str | None = None
    environment: str | None = None
    camera: str | None = None
    lighting: str | None = None
    mood: str | None = None
    negative: str | None = None


@dataclass(frozen=True)
class Character:
    """A reusable character. Identity is text (P1) or entity (P2)."""

    name: str
    appearance: str | None = None
    identity: str = "text"  # "text" | "entity"
    voice: str | None = None  # voice resource id / preset name (P2)
    variants: Mapping[str, str] = field(default_factory=dict)
    face_prompt: str | None = None  # entity path (P2)
    body_prompt: str | None = None  # entity path (P2)
    model: str = "nano2"

    def resolve_variant(self, name: str | None) -> str:
        """Return appearance with the named variant delta merged in.

        Raises ValueError on an unknown variant name.
        """
        parts: list[str] = []
        if self.appearance:
            parts.append(self.appearance)
        if name is not None:
            if name not in self.variants:
                msg = f"unknown variant {name!r} for character {self.name!r}"
                raise ValueError(msg)
            parts.append(self.variants[name])
        return ", ".join(parts)
