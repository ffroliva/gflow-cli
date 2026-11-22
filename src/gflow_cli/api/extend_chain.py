"""Chained extends — the orchestration behind `--extend N`.

Kept out of `client.py` because it is policy, not transport: it decides pacing,
ordering and what happens on refusal. The client just submits and polls.

Three properties are load-bearing and each is pinned by a test:

**Serial by construction.** There is no concurrency parameter to pass. Flow's
cheapest extend model takes ~110s, which floors the submission interval at the
top of the band measured safe in issue #241 (14 submissions in ~10 min ended in
a 403; 45–120s spacing passed 4/4). Running segments in parallel would collapse
exactly that protection, so the capability simply does not exist here.

**No auto-retry.** A refusal aborts with partials preserved. Re-submitting into
a wall raises per-profile heat and never succeeds — the same rule
`UpscaleUnavailableError` documents for tier 403s.

**Record at submit, not at completion.** Flow bills when it accepts the job. A
segment recorded only after its download would be lost — paid for, invisible —
if the run were interrupted in between.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

import structlog

from gflow_cli.api.credit_cap import mark_prior_work_billed, preserve_credit_cap_partials
from gflow_cli.errors import ConfigurationError, GFlowError, WireFormatError

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence

    from gflow_cli.api.video_extend import ExtendStarted

logger = structlog.get_logger("gflow.api.extend_chain")

__all__ = ["ExtendChainResult", "run_extend_chain"]


class _ExtendCapable(Protocol):
    """The slice of FlowApiClient this orchestrator needs.

    Narrow on purpose: the chain owns policy, not transport, and a Protocol this
    small keeps it testable without a browser.
    """

    async def extend_video(
        self,
        *,
        media_id: str,
        project_id: str,
        scene_id: str,
        position: int,
        prompt: str,
        aspect: str = ...,
        seed: int | None = ...,
        max_credits: int | None = ...,
    ) -> ExtendStarted: ...

    async def poll_video_status(self, media_id: str, *, project_id: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class ExtendChainResult:
    """Outcome of a chain, complete or aborted.

    ``completed_media_ids`` is what the caller can still render: a chain that
    aborts at segment 3 leaves two real, billed, usable segments behind, and
    discarding them because the run did not finish would be throwing away
    something already paid for.
    """

    scene_id: str
    completed_media_ids: list[str] = field(default_factory=lambda: [])
    credits_spent: int = 0
    error: Exception | None = None

    @property
    def aborted(self) -> bool:
        """A run ended early exactly when something refused it."""
        return self.error is not None


def _submit_failure(exc: GFlowError | ValueError, completed: list[str]) -> GFlowError:
    if isinstance(exc, ValueError):
        error = ConfigurationError(str(exc))
        error.__cause__ = exc
        return error
    if isinstance(exc, WireFormatError):
        error = exc
        error.remediation_hint = (
            "The extend response was malformed. Check the scene on Flow for any accepted "
            "segment before continuing."
            + (" Completed segments are already billed." if completed else "")
        )
        return error
    return exc


def _poll_failure(exc: GFlowError | ValueError, *, segment: int, media_id: str) -> GFlowError:
    error: GFlowError
    if isinstance(exc, ValueError):
        error = WireFormatError(
            str(exc),
            remediation_hint=(
                "The segment's completion is unknown. Check the scene on Flow before "
                "running the chain again."
            ),
        )
        error.__cause__ = exc
    else:
        error = exc
    if not error.operation_id:
        error.operation_id = media_id
    action = error.resolved_next_action()
    if action != "reconcile" and action not in {"reauth", "fix_input"}:
        error.charged = "maybe"
        error.next_action = "reconcile"
        error.retryable = False
        error.remediation_hint = (
            f"Flow took segment {segment} ({media_id}), so it may have charged for it. "
            "Check the scene on Flow before running it again."
        )
    return error


async def run_extend_chain(  # noqa: PLR0913
    client: _ExtendCapable,
    *,
    media_id: str,
    project_id: str,
    scene_id: str,
    prompts: Sequence[str],
    segments: int | None = None,
    aspect: str = "9:16",
    seed: int | None = None,
    jitter_range: tuple[float, float] = (0.0, 0.0),
    max_credits: int | None = None,
    start_position: int = 1,
    on_submitted: Callable[[ExtendStarted], None] | None = None,
    sleep: Callable[[float], Coroutine[Any, Any, None]] | None = None,
) -> ExtendChainResult:
    """Extend *media_id* repeatedly, each segment continuing the previous one.

    ``prompts`` describes each segment; when there are fewer prompts than
    ``segments`` the last one is reused, so `--extend 4` with a single prompt
    continues the same idea four times.

    ``start_position`` is where in the scene the first new segment lands. A
    resumed run passes the count of clips already there, so previously billed
    segments are appended to rather than overwritten.

    ``on_submitted`` fires the moment Flow accepts a segment — before its poll —
    so a recorder can persist a billed segment that an interrupt would otherwise
    lose.
    """
    total = segments if segments is not None else len(prompts)
    if total < 1:
        msg = "segments must be >= 1"
        raise ValueError(msg)
    if not prompts:
        msg = "at least one prompt is required — the extend route mandates text input"
        raise ValueError(msg)

    _sleep = sleep or asyncio.sleep
    completed: list[str] = []
    spent = 0
    source = media_id

    for index in range(total):
        prompt = prompts[index] if index < len(prompts) else prompts[-1]

        # Pacing sits BETWEEN submissions, never before the first (which would
        # just be dead time) nor after the last.
        if index > 0 and jitter_range[1] > 0:
            await _sleep(random.uniform(*jitter_range))  # noqa: S311 — cadence, not crypto

        try:
            started = await client.extend_video(
                media_id=source,
                project_id=project_id,
                scene_id=scene_id,
                position=start_position + index,
                prompt=prompt,
                aspect=aspect,
                seed=seed,
                max_credits=(max_credits - spent if max_credits is not None else None),
            )
        except (GFlowError, ValueError) as exc:
            # No retry, by design. Hand back everything already paid for.
            error = _submit_failure(exc, completed)
            preserve_credit_cap_partials(error, completed)
            mark_prior_work_billed(error, bool(completed))
            logger.warning(
                "extend_chain_aborted",
                segment=index + 1,
                of=total,
                completed=len(completed),
                error_class=type(error).__name__,
            )
            return ExtendChainResult(
                scene_id=scene_id,
                completed_media_ids=completed,
                credits_spent=spent,
                error=error,
            )

        # Billed now — record before the long wait, never after.
        if on_submitted is not None:
            on_submitted(started)
        spent += started.unit_cost or 0
        logger.info(
            "extend_segment_started",
            segment=index + 1,
            of=total,
            media_id=started.media_id,
            source_media_id=source,
            model_key=started.model_key,
        )

        try:
            await client.poll_video_status(started.media_id, project_id=project_id)
        except (GFlowError, ValueError) as exc:
            error = _poll_failure(exc, segment=index + 1, media_id=started.media_id)
            # The in-flight segment's outcome is unknown (docs/ERRORS.md: maybe / reconcile);
            # only a segment that already finished proves billed work.
            mark_prior_work_billed(error, bool(completed))
            logger.warning(
                "extend_chain_aborted",
                segment=index + 1,
                of=total,
                completed=len(completed),
                error_class=type(error).__name__,
            )
            return ExtendChainResult(
                scene_id=scene_id, completed_media_ids=completed, credits_spent=spent, error=error
            )
        completed.append(started.media_id)
        logger.info("extend_segment_completed", segment=index + 1, of=total)

        # Tail-only: the server caps input at 8s, so the next segment continues
        # this one rather than the whole growing scene.
        source = started.media_id

    return ExtendChainResult(scene_id=scene_id, completed_media_ids=completed, credits_spent=spent)
