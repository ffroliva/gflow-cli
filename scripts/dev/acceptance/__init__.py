"""Visual clip acceptance — judging a generated clip by looking at its frames.

A bounded context with one job: decide whether a clip is acceptable ACROSS TIME, and say
why in the skill's own vocabulary. Every mechanical gate in `clip_qa.py` is a per-frame or
signal property; this covers the class those gates are blind to, and a hallucinated object
actually RAISES the motion score, so the gap is not small.

    from acceptance import ContactSheet, JudgeSettings, VisionFrameJudge

    judge = VisionFrameJudge(JudgeSettings.from_env())
    judge.prove_sight(expected_colour="red", tmp_dir=tmp)   # before believing anything
    assessment = judge.assess(sheet)

The rule the module exists to enforce: **a judge that could not look never returns
"nothing found"**. Truncation, an unparseable body, an empty body, a self-contradictory
verdict, an exhausted model pool, or a model that fails its sight proof all raise
`JudgeUnavailableError`. None of them yields a clean assessment.

Provider names appear nowhere in this package. Where a judge lives is configuration,
resolved from `GFLOW_CLI_LLM_*` in `acceptance.settings`.
"""

from acceptance.domain import (
    Assessment,
    ContactSheet,
    Finding,
    FrameJudge,
    JudgeUnavailableError,
    TemporalFailure,
    Verdict,
)
from acceptance.judge import ScriptedFrameJudge, VisionFrameJudge
from acceptance.settings import DEFAULT_MODELS, JudgeSettings

__all__ = [
    "DEFAULT_MODELS",
    "Assessment",
    "ContactSheet",
    "Finding",
    "FrameJudge",
    "JudgeSettings",
    "JudgeUnavailableError",
    "ScriptedFrameJudge",
    "TemporalFailure",
    "Verdict",
    "VisionFrameJudge",
]
