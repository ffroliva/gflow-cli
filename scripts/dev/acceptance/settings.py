"""How to reach a judge — resolved from the project's own ``GFLOW_CLI_LLM_*`` settings.

The provider is a detail. Which gateway, which key, which model family: all of it is
configuration, none of it belongs in a type name or an import. This module is the only
place that knows where a judge lives, so swapping providers is an env change rather than
a code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Models this project has confirmed can actually see, most capable first. A pool rather
#: than one id because a rate-limited gateway is the normal case, not the exception, and
#: rotating is cheaper than sleeping. Override with ``GFLOW_CLI_JUDGE_MODELS``.
DEFAULT_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)

#: Completion budget. Not arbitrary: a reasoning model can spend the whole allowance
#: thinking, return ``finish_reason="length"`` with half a JSON object, and a naive
#: parser then reports zero findings — a clean bill of health from a truncated answer.
#: Measured: at a 500-token budget a reasoning model spent the whole allowance thinking
#: and returned finish_reason="length" with half a JSON object. This leaves room, and
#: stays tunable for model families that need more.
DEFAULT_MAX_TOKENS = 3000


@dataclass(frozen=True, slots=True)
class JudgeSettings:
    """Where the judge lives and which models it may use.

    Validated at construction rather than at call time: a misconfigured judge should fail
    before a benchmark run starts, not silently produce verdicts nobody can trust.
    """

    base_url: str
    api_key: str
    models: tuple[str, ...] = DEFAULT_MODELS
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if not self.models:
            msg = "a judge needs at least one model"
            raise ValueError(msg)
        if any(m.strip().lower() == "auto" for m in self.models):
            # A routing gateway asked for `auto` may hand the request to a TEXT-ONLY
            # model. It will not error — it will answer the question plausibly, having
            # seen nothing. That is a blind judge returning confident verdicts, which is
            # strictly worse than no judge, and it is invisible in the output. Name the
            # models.
            msg = "'auto' is refused: a gateway may route it to a text-only model that sees nothing"
            raise ValueError(msg)
        if self.max_tokens < 512:
            msg = f"max_tokens={self.max_tokens} risks truncating the verdict"
            raise ValueError(msg)

    @classmethod
    def from_env(cls) -> JudgeSettings:
        """Build from ``GFLOW_CLI_LLM_*``, the same settings the prompt tools use.

        Raises rather than defaulting a missing key: an unauthenticated judge fails on
        the first call anyway, and failing here says why.
        """
        base_url = os.environ.get("GFLOW_CLI_LLM_BASE_URL", "").strip()
        api_key = os.environ.get("GFLOW_CLI_LLM_API_KEY", "").strip()
        if not base_url or not api_key:
            msg = (
                "set GFLOW_CLI_LLM_BASE_URL and GFLOW_CLI_LLM_API_KEY to use a judge "
                "(the same settings the prompt tools and skillopt harness use)"
            )
            raise ValueError(msg)
        raw = os.environ.get("GFLOW_CLI_JUDGE_MODELS", "").strip()
        models = tuple(m.strip() for m in raw.split(",") if m.strip()) or DEFAULT_MODELS
        return cls(
            base_url=base_url,
            api_key=api_key,
            models=models,
            max_tokens=int(os.environ.get("GFLOW_CLI_JUDGE_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        )
