"""The frame-acceptance judge: a model that looks at a contact sheet and reports findings.

Every test here exists because of one failure on 2026-09-07: a beat whose background
monolith tripled in size and materialised top-down passed EVERY mechanical gate — bars,
stream lengths, audio, motion — and was caught by a human watching. The mechanical gates
are per-frame; that defect is temporal. The judge closes that gap, and these tests exist
to stop it closing the gap *dishonestly*.

The invariant almost every test below defends: **a judge that could not look must never
return "nothing found".** A truncated completion, an unparseable body, a model that cannot
actually see — each of those is an ERROR. Reporting them as a clean assessment is the same
"confidently empty" failure that produced blank incident bundles and a probe that declared
a feature absent because its click silently failed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "dev"))

from acceptance import (  # noqa: E402
    Assessment,
    ContactSheet,
    Finding,
    JudgeSettings,
    JudgeUnavailableError,
    ScriptedFrameJudge,
    TemporalFailure,
    Verdict,
    VisionFrameJudge,
)

# --- the language ------------------------------------------------------------


def test_the_taxonomy_matches_the_skill() -> None:
    """The judge must speak the skill's vocabulary, not invent a second one.

    `skills/video-production/SKILL.md`'s temporal pass names seven failures. A judge
    reporting `"weird background"` cannot be scored against a fixture whose known answer
    is `background_morph`, so the enum IS the contract between skill and benchmark.
    """
    assert {f.value for f in TemporalFailure} == {
        "object_scale_drift",
        "materialisation",
        "identity_swap",
        "wardrobe_change",
        "background_morph",
        "extra_subject",
        "axis_break",
    }


# --- configuration -----------------------------------------------------------


def test_auto_model_routing_is_refused() -> None:
    """`auto` lets a gateway pick a TEXT-ONLY model, which "sees" nothing and answers
    anyway — a blind judge returning a confident verdict. Refused at construction."""
    with pytest.raises(ValueError, match="auto"):
        JudgeSettings(base_url="https://x/v1", api_key="k", models=("auto",))


def test_settings_require_at_least_one_model() -> None:
    with pytest.raises(ValueError, match="at least one model"):
        JudgeSettings(base_url="https://x/v1", api_key="k", models=())


# --- the invariant: never a silent clean bill ---------------------------------


class _FakeClient:
    """Minimal stand-in for an OpenAI-compatible client."""

    def __init__(self, *replies: Any) -> None:
        self._replies = list(replies)
        self.models_called: list[str] = []
        self.chat = self  # the SDK exposes .chat.completions.create
        self.completions = self

    def create(self, *, model: str, messages: Any, **_: Any) -> Any:  # noqa: ARG002
        self.models_called.append(model)
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _completion(content: str | None, finish_reason: str = "stop") -> Any:
    message = type("M", (), {"content": content})()
    choice = type("C", (), {"message": message, "finish_reason": finish_reason})()
    return type("R", (), {"choices": [choice]})()


def _sheet(tmp_path: Path) -> ContactSheet:
    p = tmp_path / "strip.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return ContactSheet(path=p, fps=1.0, cells=8, clip_name="rb04.mp4")


def test_a_truncated_completion_is_an_error_not_an_empty_verdict(tmp_path: Path) -> None:
    """The exact failure this class of bug takes: a reasoning model eats the completion
    budget, `finish_reason=length`, the body is half a JSON object — and a naive parser
    yields zero findings, which reads as ACCEPT. Truncation is an error, never a verdict."""
    client = _FakeClient(_completion('{"verdict": "reject", "find', finish_reason="length"))
    judge = VisionFrameJudge(
        JudgeSettings(base_url="https://x/v1", api_key="k", models=("m1",)),
        client_factory=lambda _s: client,
    )
    with pytest.raises(JudgeUnavailableError, match="truncated"):
        judge.assess(_sheet(tmp_path))


def test_an_unparseable_body_is_an_error_not_an_empty_verdict(tmp_path: Path) -> None:
    client = _FakeClient(_completion("I had a look and it seems fine to me!"))
    judge = VisionFrameJudge(
        JudgeSettings(base_url="https://x/v1", api_key="k", models=("m1",)),
        client_factory=lambda _s: client,
    )
    with pytest.raises(JudgeUnavailableError, match="could not be parsed"):
        judge.assess(_sheet(tmp_path))


def test_an_empty_body_is_an_error_not_an_empty_verdict(tmp_path: Path) -> None:
    client = _FakeClient(_completion(None))
    judge = VisionFrameJudge(
        JudgeSettings(base_url="https://x/v1", api_key="k", models=("m1",)),
        client_factory=lambda _s: client,
    )
    with pytest.raises(JudgeUnavailableError):
        judge.assess(_sheet(tmp_path))


# --- the happy paths, both of them -------------------------------------------


def test_a_reject_carries_findings_with_evidence(tmp_path: Path) -> None:
    body = (
        '{"verdict": "reject", "findings": [{"failure": "object_scale_drift", '
        '"first_cell": 3, "evidence": "the rock behind them grows across cells 3-7"}]}'
    )
    client = _FakeClient(_completion(body))
    judge = VisionFrameJudge(
        JudgeSettings(base_url="https://x/v1", api_key="k", models=("m1",)),
        client_factory=lambda _s: client,
    )
    a = judge.assess(_sheet(tmp_path))
    assert a.verdict is Verdict.REJECT
    assert a.findings[0].failure is TemporalFailure.OBJECT_SCALE_DRIFT
    assert a.findings[0].first_cell == 3
    assert a.model == "m1"


def test_an_accept_must_carry_no_findings(tmp_path: Path) -> None:
    """A verdict of ACCEPT alongside findings is incoherent — the model has contradicted
    itself, and picking one half is guessing which half. Refuse instead."""
    body = (
        '{"verdict": "accept", "findings": [{"failure": "axis_break", "first_cell": 2, '
        '"evidence": "he swaps sides"}]}'
    )
    client = _FakeClient(_completion(body))
    judge = VisionFrameJudge(
        JudgeSettings(base_url="https://x/v1", api_key="k", models=("m1",)),
        client_factory=lambda _s: client,
    )
    with pytest.raises(JudgeUnavailableError, match="contradict"):
        judge.assess(_sheet(tmp_path))


# --- rotation and failover ----------------------------------------------------


def test_a_transient_failure_advances_to_the_next_model(tmp_path: Path) -> None:
    """Round-robin with IMMEDIATE failover. Sleeping between attempts buys nothing when
    the next model is a different provider."""
    good = _completion('{"verdict": "accept", "findings": []}')
    client = _FakeClient(RuntimeError("429 rate limited"), good)
    judge = VisionFrameJudge(
        JudgeSettings(base_url="https://x/v1", api_key="k", models=("m1", "m2")),
        client_factory=lambda _s: client,
    )
    a = judge.assess(_sheet(tmp_path))
    assert a.verdict is Verdict.ACCEPT
    assert client.models_called == ["m1", "m2"]


def test_a_model_the_gateway_does_not_know_is_dropped_from_the_pool(tmp_path: Path) -> None:
    """Retrying an id the gateway has removed just burns attempts."""
    dead = RuntimeError("model not in the catalog")
    dead.status_code = 400  # type: ignore[attr-defined]
    client = _FakeClient(dead, _completion('{"verdict": "accept", "findings": []}'))
    settings = JudgeSettings(base_url="https://x/v1", api_key="k", models=("gone", "m2"))
    judge = VisionFrameJudge(settings, client_factory=lambda _s: client)
    judge.assess(_sheet(tmp_path))
    assert "gone" not in judge.live_models
    assert judge.live_models == ("m2",)


def test_exhausting_every_model_raises_rather_than_returning_clean(tmp_path: Path) -> None:
    client = _FakeClient(RuntimeError("boom"), RuntimeError("boom"))
    judge = VisionFrameJudge(
        JudgeSettings(base_url="https://x/v1", api_key="k", models=("m1", "m2")),
        client_factory=lambda _s: client,
    )
    with pytest.raises(JudgeUnavailableError):
        judge.assess(_sheet(tmp_path))


# --- proving the judge can see, before believing it ---------------------------


def test_a_judge_that_cannot_see_fails_its_sight_proof(tmp_path: Path) -> None:
    """`clip_qa.py --selftest` injects a known audio shift and refuses to trust the sync
    detector until it recovers it. Same discipline, applied to the eye: show the judge a
    synthetic image whose content is known and require it to name it. A text-only model
    behind a routing gateway answers plausibly and sees nothing — this is what catches it.
    """
    client = _FakeClient(_completion('{"colour": "green"}'))
    judge = VisionFrameJudge(
        JudgeSettings(base_url="https://x/v1", api_key="k", models=("m1",)),
        client_factory=lambda _s: client,
    )
    with pytest.raises(JudgeUnavailableError, match="sight"):
        judge.prove_sight(expected_colour="red", tmp_dir=tmp_path)


def test_a_judge_that_can_see_passes_its_sight_proof(tmp_path: Path) -> None:
    client = _FakeClient(_completion('{"colour": "red"}'))
    judge = VisionFrameJudge(
        JudgeSettings(base_url="https://x/v1", api_key="k", models=("m1",)),
        client_factory=lambda _s: client,
    )
    judge.prove_sight(expected_colour="red", tmp_dir=tmp_path)


# --- the port has a second implementation, which is why it is a port ----------


def test_the_scripted_judge_satisfies_the_same_port(tmp_path: Path) -> None:
    """`ScriptedFrameJudge` is what the benchmark's own tests run against, so the harness
    can be exercised with no network and a known answer."""
    expected = Assessment(
        verdict=Verdict.REJECT,
        findings=(Finding(TemporalFailure.MATERIALISATION, 4, "a fragment appears"),),
        model="scripted",
    )
    judge: Any = ScriptedFrameJudge([expected])
    assert judge.assess(_sheet(tmp_path)) == expected
