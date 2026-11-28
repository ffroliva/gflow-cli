"""The refusal retry loop: one direct attempt, at most one retry, and the typed error.

The generation surfaces call it once, so the CLI, MCP and the worker carry no retry code of
their own. A direct attempt that does not end ``Accepted`` raises ``DirectAttemptEnded``;
``next_step`` decides what follows:

* ``RetryDirect`` (Flow refused the token, not billed): write ``submit_refused``, relaunch
  the browser process at the setting's launch visibility, and send once more.
* ``FallbackUi`` (nothing reached Flow): relaunch when the page's visibility differs, write
  ``submit_attempted`` through the submit gate, then run the UI path. A failed relaunch here
  raises ``RelaunchFailed`` (not charged), naming any earlier refusal. The UI transport never
  receives ``on_before_submit``, so this is the one place its checkpoint is written.
* ``Stop``: a second refusal is final and not charged; past the send the error says
  reconcile; a signed-out session raises the UI path's auth error.

The relaunch runs only after the attempt has left its in-flight slot, so the transport's
``quiesce()`` can drain other jobs without waiting on this one. Each attempt records the
client's ``relaunch_generation`` when it starts: refusals in one window cost one relaunch
(a later one finds the fresh process already started and retries on it), and an attempt
that waited out another job's relaunch at the gate (``NotSent("torn_down")``) runs again
on the new transport instead of falling back to the UI.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, TypeVar

import structlog

from gflow_cli.api._flow_session import RelaunchFailed
from gflow_cli.api.dto import GenerationCheckpoint, GenerationPhase
from gflow_cli.api.refusal import waf_refusal_final
from gflow_cli.api.submit_policy import (
    DirectAttemptEnded,
    FallbackUi,
    Indeterminate,
    NotSent,
    RetryDirect,
    RetryStep,
    Stop,
    next_step,
)
from gflow_cli.errors import AuthExpiredError, GFlowError

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.api.dto import GeneratedImage, GenerationCheckpointObserver
    from gflow_cli.api.image import GenerateImageRequest, ImageRef
    from gflow_cli.api.submit_policy import BrowserVisibility, VisibilitySetting

__all__ = [
    "REFUSAL_RETRY_NOTICE",
    "BeforeSubmit",
    "DirectImages",
    "DirectUploads",
    "RetryHost",
    "run_with_retry",
]

log = structlog.get_logger(__name__)
_T = TypeVar("_T")

#: Called by the transport immediately before the billed POST.
BeforeSubmit = Callable[[], None]

#: An attempt that waited at the gate while the client relaunched (``DirectHttpTransport``).
_TORN_DOWN = NotSent("torn_down")

#: The one line a person sees before the relaunch (structlog writes it to stderr).
REFUSAL_RETRY_NOTICE = "Flow refused the token; retrying once from a fresh browser"


class RetryHost(Protocol):
    """The client the loop relaunches: its current visibility, how many relaunches it has
    completed, and ``relaunch`` (a no-op when ``unless_since`` is no longer current)."""

    visibility: BrowserVisibility
    relaunch_generation: int

    async def relaunch(
        self, visibility: BrowserVisibility, *, unless_since: int | None = None
    ) -> None: ...


class DirectImages(Protocol):
    """A transport whose image path can end ``NotSent`` and fall back to its UI path."""

    async def generate_images(
        self,
        *,
        project_id: str | None,
        request: GenerateImageRequest,
        name_resolver: Callable[[str], str | None] | None = None,
        on_before_submit: BeforeSubmit | None = None,
    ) -> list[GeneratedImage]: ...

    async def generate_images_ui(
        self,
        *,
        project_id: str | None,
        request: GenerateImageRequest,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> list[GeneratedImage]: ...


class DirectUploads(Protocol):
    """A transport whose upload can end ``NotSent`` and fall back to its UI upload."""

    async def upload_reference(self, *, project_id: str, path: Path) -> ImageRef | None: ...

    async def upload_reference_ui(self, *, project_id: str, path: Path) -> ImageRef | None: ...


async def run_with_retry(
    host: RetryHost,
    *,
    direct: Callable[[BeforeSubmit | None], Awaitable[_T]],
    ui: Callable[[], Awaitable[_T]],
    setting: VisibilitySetting,
    on_checkpoint: GenerationCheckpointObserver | None = None,
    observe: Callable[[_T, bool], _T] | None = None,
) -> _T:
    """Run ``direct`` and apply ``next_step`` until it is done, falls back, or stops.

    ``direct`` receives the ``on_before_submit`` hook (``None`` with no observer) and must
    read the client's transport when called: a relaunch replaces it. An exception the hook
    raises (a cancel from the submit gate) propagates unchanged. ``observe`` receives the
    result and whether a refusal was retried before it (``--json`` and the recorder say so).
    """

    emit = _Checkpoints(on_checkpoint)
    hook = None if on_checkpoint is None else emit.submit_attempted
    refused: str | None = None  # the reason of a refusal already retried

    def done(result: _T) -> _T:
        return result if observe is None else observe(result, refused is not None)

    while True:
        generation = host.relaunch_generation  # the process this attempt starts on
        try:
            return done(await direct(hook))
        except DirectAttemptEnded as exc:
            ended = exc
        if ended.outcome == _TORN_DOWN and host.relaunch_generation != generation:
            continue  # it waited out another job's relaunch; nothing was sent: run it again
        step = next_step(ended.outcome, setting=setting, retried=refused is not None)
        if isinstance(step, RetryDirect):
            await _relaunch_after_refusal(host, step, ended, emit, generation)
            refused = _reason(ended)
            continue
        if isinstance(step, FallbackUi):
            await _prepare_ui_fallback(host, step, ended, emit, refused)
            return done(await ui())
        raise _stop_error(step, ended, emit) from ended


class _Checkpoints:
    """Writes a checkpoint phase to the observer, when there is one."""

    def __init__(self, observer: GenerationCheckpointObserver | None) -> None:
        self._observer = observer

    def __call__(self, phase: GenerationPhase) -> None:
        if self._observer is not None:
            self._observer(GenerationCheckpoint(phase=phase))

    def submit_attempted(self) -> None:
        self("submit_attempted")


async def _relaunch_after_refusal(
    host: RetryHost,
    step: RetryDirect,
    ended: DirectAttemptEnded,
    emit: _Checkpoints,
    generation: int,
) -> None:
    """Reset the job to not charged, tell the person, and start a fresh browser process,
    unless another job's relaunch already did since this attempt started."""
    emit("submit_refused")
    log.warning(
        "mint.retried",
        detail=REFUSAL_RETRY_NOTICE,
        visibility=step.visibility.value,
        new_process=True,
    )
    try:
        await host.relaunch(step.visibility, unless_since=generation)
    except RelaunchFailed as failed:
        raise waf_refusal_final(reason=_reason(ended), attempts=1) from failed


async def _prepare_ui_fallback(
    host: RetryHost,
    step: FallbackUi,
    ended: DirectAttemptEnded,
    emit: _Checkpoints,
    refused: str | None,
) -> None:
    """Relaunch at the UI's visibility when it differs, then pass the submit gate."""
    if host.visibility != step.visibility:
        await _relaunch_for_ui(host, step.visibility, refused)
    log.info("direct.fallback_to_ui", reason=_reason(ended))
    emit.submit_attempted()


async def _relaunch_for_ui(
    host: RetryHost, visibility: BrowserVisibility, refused: str | None
) -> None:
    """Nothing is sent before a UI fallback, so a failed relaunch is raised as itself
    (not charged), naming the refusal that came before it, if any."""
    try:
        await host.relaunch(visibility)
    except RelaunchFailed as failed:
        if refused is None:
            raise
        detail = f"{failed.detail}; Flow had refused the token before it ({refused})"
        raise RelaunchFailed(detail) from failed


def _reason(ended: DirectAttemptEnded) -> str:
    outcome = ended.outcome
    if isinstance(outcome, Indeterminate):
        return outcome.cause
    return outcome.reason


def _stop_error(step: RetryStep, ended: DirectAttemptEnded, emit: _Checkpoints) -> GFlowError:
    """The final error; a final refusal first resets the job to not charged."""
    if not isinstance(step, Stop):  # pragma: no cover - Done never follows an ended attempt
        raise AssertionError(f"unexpected step {step!r} for an ended attempt")
    outcome = ended.outcome
    if step.kind == "waf_final":
        emit("submit_refused")
        return waf_refusal_final(reason=_reason(ended), attempts=2)
    if step.kind == "reauth":
        return AuthExpiredError(
            detail=(
                f"Flow's saved session for this profile is signed out ({_reason(ended)}); "
                "nothing was sent."
            ),
            route="direct_http.shell",
            charged="no",
        )
    # The transport attaches the error to every outcome past the send (``_gateway.py``).
    if ended.error is None:  # pragma: no cover - unreachable by that contract
        raise AssertionError(f"an attempt ended past the send carries its error: {outcome!r}")
    ended.error.retryable = False  # past the send it is never a retry
    if ended.error.charged == "no":
        ended.error.charged = "maybe"  # it was sent, so Flow may have billed it: reconcile
    return ended.error
