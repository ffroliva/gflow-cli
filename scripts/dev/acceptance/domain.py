"""The language of visual clip acceptance: what can be wrong, and what a verdict is.

This module is deliberately free of transport, configuration and provider concerns. It
holds the vocabulary the skill already speaks, so a judge, a benchmark and a human all
score the same clip against the same named things.

The taxonomy is NOT invented here. It is the temporal pass in
``skills/video-production/SKILL.md``, and that document is the source of truth — a judge
that answers "the background looks odd" cannot be scored against a fixture whose known
answer is ``background_morph``. The enum is the contract between the skill and the
benchmark, and a test pins the two together so they cannot drift apart quietly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class TemporalFailure(Enum):
    """A way a clip can be wrong ACROSS TIME, with every individual frame fine.

    Every mechanical gate in ``clip_qa.py`` is a per-frame or signal property, so this
    whole class of defect passes them — and a hallucinated object is motion, so it
    *raises* the motion score. That is why these are named: they are what the numbers
    cannot see.
    """

    OBJECT_SCALE_DRIFT = "object_scale_drift"
    MATERIALISATION = "materialisation"
    IDENTITY_SWAP = "identity_swap"
    WARDROBE_CHANGE = "wardrobe_change"
    BACKGROUND_MORPH = "background_morph"
    EXTRA_SUBJECT = "extra_subject"
    AXIS_BREAK = "axis_break"


class Verdict(Enum):
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class Finding:
    """One observed failure, tied to where it was first visible.

    ``first_cell`` is an index into the contact sheet, not a timestamp: the sheet is the
    evidence a human can re-open, and a claim that cannot be pointed at on the sheet is
    an impression rather than a finding.
    """

    failure: TemporalFailure
    first_cell: int
    evidence: str


@dataclass(frozen=True, slots=True)
class Assessment:
    """A verdict, the findings that justify it, and who produced it.

    ``model`` is recorded because a verdict is only as good as the thing that issued it;
    a benchmark comparing two runs needs to know whether it is comparing judges or
    comparing prompts.
    """

    verdict: Verdict
    findings: tuple[Finding, ...]
    model: str


@dataclass(frozen=True, slots=True)
class ContactSheet:
    """The evidence a judge looks at: frames of one clip, in order, on one image.

    ``fps`` and ``cells`` travel with the path because the judge must be told the
    sampling rate to reason about time at all — "it grows between cells 3 and 7" means
    nothing without knowing a cell is one second.
    """

    path: Path
    fps: float
    cells: int
    clip_name: str


class JudgeUnavailableError(RuntimeError):
    """The judge could not deliver a verdict, and no verdict is being invented.

    THIS EXCEPTION IS THE POINT OF THE MODULE. Every failure mode that could plausibly
    be smoothed into "nothing found" raises this instead: a truncated completion, an
    unparseable body, an empty body, a verdict that contradicts its own findings, a model
    pool exhausted, or a judge that cannot demonstrate it can see.

    A clean bill of health from something that never looked is the failure this project
    has now met three times in one week — a 20 s timeout read as "the feature is absent",
    a swallowed click read as "the composer is video-only", and an incident bundle
    photographed after the page was blanked and read as "the DOM was gone". Silence is
    not evidence, and it must never be returned as if it were.
    """


class FrameJudge(Protocol):
    """Anything that can look at a contact sheet and report what is wrong with it."""

    def assess(self, sheet: ContactSheet) -> Assessment:
        """Return a verdict, or raise :class:`JudgeUnavailableError`. Never both."""
        ...
