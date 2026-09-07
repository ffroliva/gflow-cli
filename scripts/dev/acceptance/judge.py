"""Judges: things that look at a contact sheet and say what is wrong with it.

Two implementations, which is why :class:`FrameJudge` is a port rather than a class:
:class:`VisionFrameJudge` asks a real model, and :class:`ScriptedFrameJudge` returns
canned answers so the benchmark harness can be tested with no network and a known answer.

Provider names appear nowhere in this file. Where the judge lives is
:mod:`acceptance.settings`, resolved from ``GFLOW_CLI_LLM_*``.
"""

from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from acceptance.domain import (
    Assessment,
    ContactSheet,
    Finding,
    JudgeUnavailableError,
    TemporalFailure,
    Verdict,
)
from acceptance.settings import JudgeSettings

#: The instruction. It names the taxonomy explicitly because a judge free to invent its
#: own vocabulary cannot be scored against a fixture, and it demands a cell index because
#: a claim nobody can point at on the sheet is an impression rather than a finding.
_PROMPT = """You are grading ONE generated video clip for temporal defects.

The image is a contact sheet: {cells} frames of the clip "{clip}", sampled at {fps} frame
per second, in order, left to right then top to bottom. Cell 0 is the first frame.

Every frame may look fine on its own. You are looking ONLY for things that are wrong
ACROSS the sequence. Report only these failures, by these exact names:

- object_scale_drift : something grows or shrinks across cells while the camera is still
- materialisation    : an object, or a fragment of one, is absent in early cells and
                       present later, or the reverse
- identity_swap      : a face that is one person early and a different person later
- wardrobe_change    : a garment, colour or prop that changes between cells
- background_morph   : terrain, horizon or architecture that rearranges behind held subjects
- extra_subject      : the number of people or limbs changes between cells
- axis_break         : a subject crosses to the wrong side of frame mid-shot

Answer with JSON only, no prose and no code fence:

{{"verdict": "accept" | "reject",
  "findings": [{{"failure": "<one name above>", "first_cell": <int>,
                 "evidence": "<what you saw, and in which cells>"}}]}}

"accept" means you found none of the above; its findings list MUST be empty.
"reject" means you found at least one; list every one you are confident of.
Do not report ordinary camera movement, lighting change, or wind in cloth as a defect."""

#: The sight proof's question. Deliberately trivial for anything that can see, and
#: unanswerable for anything that cannot.
_SIGHT_PROMPT = 'This image is one flat colour. Answer with JSON only: {"colour": "<the colour>"}'

_DEAD_MODEL_MARKERS = ("not in the catalog", "not in catalog", "not found", "does not exist")


def _looks_dead(exc: Exception) -> bool:
    """True when the gateway rejected the MODEL ID itself, so retrying it is pointless."""
    if getattr(exc, "status_code", None) not in (400, 404):
        return False
    return any(m in str(exc).lower() for m in _DEAD_MODEL_MARKERS)


def _extract_json(text: str) -> dict[str, Any]:
    """The first JSON object in *text*, tolerating a code fence but nothing weirder.

    Raises :class:`JudgeUnavailableError` rather than returning ``{}`` — an unreadable
    answer is an absent answer, and an absent answer must never become "nothing found".
    """
    stripped = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.MULTILINE)
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    if not match:
        msg = f"the judge's answer could not be parsed as JSON: {text[:200]!r}"
        raise JudgeUnavailableError(msg)
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        msg = f"the judge's answer could not be parsed as JSON: {text[:200]!r}"
        raise JudgeUnavailableError(msg) from exc
    if not isinstance(parsed, dict):
        msg = f"the judge's answer was not a JSON object: {text[:200]!r}"
        raise JudgeUnavailableError(msg)
    return parsed


def _solid_colour_png(path: Path, rgb: tuple[int, int, int]) -> Path:
    """A tiny single-colour PNG, written with the standard library only.

    The sight proof must not depend on Pillow or ffmpeg: a judge that cannot be proven
    without installing something is a judge people will skip proving.
    """
    width = height = 32
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return path


class ScriptedFrameJudge:
    """A judge with a fixed answer, for testing the things that USE a judge."""

    def __init__(self, assessments: Sequence[Assessment]) -> None:
        self._queue = list(assessments)

    def assess(self, sheet: ContactSheet) -> Assessment:  # noqa: ARG002
        if not self._queue:
            msg = "ScriptedFrameJudge ran out of scripted assessments"
            raise JudgeUnavailableError(msg)
        return self._queue.pop(0)


class VisionFrameJudge:
    """Asks a model to look at the sheet, and refuses to guess when it cannot.

    Rotation is round-robin with IMMEDIATE failover and no backoff: the next attempt is a
    different model, often a different provider, so sleeping only burns wall-clock. A
    model the gateway says it does not know is dropped from the pool for the rest of the
    run rather than retried.
    """

    def __init__(
        self,
        settings: JudgeSettings,
        *,
        client_factory: Callable[[JudgeSettings], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._models = list(settings.models)
        self._next = 0
        self._client_factory = client_factory or _default_client_factory
        self._client: Any | None = None

    @property
    def live_models(self) -> tuple[str, ...]:
        """Models still in rotation — dead ids are removed as they are discovered."""
        return tuple(self._models)

    # -- the port ------------------------------------------------------------

    def assess(self, sheet: ContactSheet) -> Assessment:
        prompt = _PROMPT.format(cells=sheet.cells, clip=sheet.clip_name, fps=sheet.fps)
        body, model = self._ask(prompt, sheet.path)
        return _to_assessment(_extract_json(body), model)

    # -- proving the judge before believing it -------------------------------

    def prove_sight(self, *, expected_colour: str, tmp_dir: Path) -> str:
        """Show the judge a flat colour and require it to name it.

        ``clip_qa.py --selftest`` injects a known audio shift and refuses to trust the
        sync detector until it recovers it; the same discipline, applied to the eye. The
        failure this catches is specific and silent: a gateway routing to a text-only
        model answers every question plausibly and sees nothing at all.

        Call it once per run before any verdict is believed.
        """
        colours = {"red": (220, 30, 30), "green": (30, 200, 60), "blue": (40, 70, 220)}
        rgb = colours.get(expected_colour.lower())
        if rgb is None:
            msg = f"no sight-proof colour named {expected_colour!r}"
            raise ValueError(msg)
        image = _solid_colour_png(tmp_dir / f"_sight_{expected_colour}.png", rgb)
        body, model = self._ask(_SIGHT_PROMPT, image)
        seen = str(_extract_json(body).get("colour", "")).strip().lower()
        if expected_colour.lower() not in seen:
            msg = (
                f"{model} failed the sight proof: shown {expected_colour}, answered "
                f"{seen!r}. It is not looking at the image, so its verdicts mean nothing"
            )
            raise JudgeUnavailableError(msg)
        return model

    # -- transport -----------------------------------------------------------

    def _ask(self, prompt: str, image: Path) -> tuple[str, str]:
        if not self._models:
            msg = "no models left in the judge's pool"
            raise JudgeUnavailableError(msg)
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": _data_uri(image)},
            },
        ]
        errors: list[str] = []
        for _ in range(min(self._settings.max_attempts, max(len(self._models), 1))):
            if not self._models:
                break
            model = self._models[self._next % len(self._models)]
            self._next += 1
            try:
                return self._one_call(content, model), model
            except JudgeUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001 - any transport error rotates
                if _looks_dead(exc):
                    self._models = [m for m in self._models if m != model]
                    self._next = 0
                errors.append(f"{model}: {type(exc).__name__}: {str(exc)[:120]}")
        msg = "every model in the judge's pool failed: " + "; ".join(errors)
        raise JudgeUnavailableError(msg)

    def _ensure_client(self) -> Any:
        """The client, built once. Lazily, so constructing a judge costs no network and
        no optional import — the unit tests never reach here."""
        if self._client is None:
            self._client = self._client_factory(self._settings)
        return self._client

    def _one_call(self, content: list[dict[str, Any]], model: str) -> str:
        response = self._ensure_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            temperature=0.0,
            max_tokens=self._settings.max_tokens,
        )
        choice = response.choices[0]
        # A completion that ran out of budget is HALF AN ANSWER. Parsed leniently it
        # yields no findings, which reads as ACCEPT — a clean bill of health from a
        # sentence that stopped mid-word. This check is the whole reason the class exists.
        if getattr(choice, "finish_reason", None) == "length":
            msg = (
                f"{model} truncated its answer (finish_reason=length) — raise "
                f"GFLOW_CLI_JUDGE_MAX_TOKENS above {self._settings.max_tokens}"
            )
            raise JudgeUnavailableError(msg)
        body = getattr(choice.message, "content", None)
        if not body or not body.strip():
            msg = f"{model} returned an empty answer"
            raise JudgeUnavailableError(msg)
        return body


def _to_assessment(payload: dict[str, Any], model: str) -> Assessment:
    """Parse a verdict, refusing anything self-contradictory."""
    raw_verdict = str(payload.get("verdict", "")).strip().lower()
    if raw_verdict not in {"accept", "reject"}:
        msg = f"the judge's answer could not be parsed: verdict={raw_verdict!r}"
        raise JudgeUnavailableError(msg)
    verdict = Verdict(raw_verdict)
    findings = tuple(_to_findings(payload.get("findings") or []))
    if verdict is Verdict.ACCEPT and findings:
        # Accepting while listing defects is the model contradicting itself. Choosing
        # which half to believe is guessing, and guessing is what this module refuses.
        msg = (
            f"the judge contradicted itself: verdict=accept with {len(findings)} finding(s) listed"
        )
        raise JudgeUnavailableError(msg)
    if verdict is Verdict.REJECT and not findings:
        msg = "the judge rejected the clip but named no finding, so there is nothing to act on"
        raise JudgeUnavailableError(msg)
    return Assessment(verdict=verdict, findings=findings, model=model)


def _to_findings(raw: Iterable[Any]) -> Iterable[Finding]:
    for item in raw:
        if not isinstance(item, dict):
            msg = f"a finding was not an object: {item!r}"
            raise JudgeUnavailableError(msg)
        name = str(item.get("failure", "")).strip().lower()
        try:
            failure = TemporalFailure(name)
        except ValueError as exc:
            msg = (
                f"the judge named a failure outside the taxonomy: {name!r}. "
                "It must use the skill's vocabulary or it cannot be scored"
            )
            raise JudgeUnavailableError(msg) from exc
        try:
            first_cell = int(item.get("first_cell", -1))
        except (TypeError, ValueError) as exc:
            msg = f"finding {name!r} carried no usable first_cell"
            raise JudgeUnavailableError(msg) from exc
        yield Finding(
            failure=failure,
            first_cell=first_cell,
            evidence=str(item.get("evidence", "")).strip(),
        )


def _data_uri(image: Path) -> str:
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    suffix = image.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    return f"data:image/{mime};base64,{encoded}"


def _default_client_factory(settings: JudgeSettings) -> Any:
    # Optional: only a real network call needs it, so it is not a project dependency
    # and pyright cannot resolve it in this environment.
    from openai import OpenAI  # type: ignore[import-not-found]  # noqa: PLC0415

    return OpenAI(base_url=settings.base_url, api_key=settings.api_key)
