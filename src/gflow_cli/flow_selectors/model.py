"""Structured inventory of the Flow DOM elements gflow depends on.

A selector without its page is unfindable; a MISS without its mode is
unattributable. CROP_SELECTORS[0] legitimately misses on the agentic arm
because it IS the classic-mode indicator (factory.py:116). Context is data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gflow_cli.config import UiMode

_SEG = r"[a-z0-9]+(?:_[a-z0-9]+)*"
_KEY_RE = re.compile(rf"^{_SEG}(?:\.{_SEG})+$")
_SURFACE_KEY_RE = re.compile(rf"^{_SEG}(?:\.{_SEG})*$")

# ui_automation.py:117-124 — below this, Flow crosses its responsive breakpoint
# and the selectors drift. A probe rendering smaller reports drift that is not there.
MIN_VIEWPORT = (1920, 1080)


@dataclass(frozen=True)
class Surface:
    key: str
    url_template: str
    viewport: tuple[int, int]

    def __post_init__(self) -> None:
        if not _SURFACE_KEY_RE.match(self.key):
            msg = f"surface key must be lower_snake, optionally dotted: {self.key!r}"
            raise ValueError(msg)
        # Per-AXIS, not tuple comparison: (2560, 720) < (1920, 1080) is False
        # lexicographically, so a 720px-tall surface would slip the guard.
        if self.viewport[0] < MIN_VIEWPORT[0] or self.viewport[1] < MIN_VIEWPORT[1]:
            msg = (
                f"{self.key}: viewport {self.viewport} is below Flow's responsive "
                f"breakpoint {MIN_VIEWPORT}; selectors drift below it"
            )
            raise ValueError(msg)


@dataclass(frozen=True)
class Selector:
    key: str
    surface: str
    candidates: tuple[str, ...]
    mode: UiMode | None = None
    expect_unique: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        if not self.candidates:
            msg = f"{self.key}: needs at least one candidate"
            raise ValueError(msg)
        if not _KEY_RE.match(self.key):
            msg = f"selector key must be dotted lower_snake: {self.key!r}"
            raise ValueError(msg)
