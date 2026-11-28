"""Domain submit policy: visibility, submit outcome, retry step, and re-arm rules.

This pure domain module defines the outcome types and state transitions for direct
HTTP generation submissions on flow.google.com, ensuring invalid states are
unrepresentable and retry decisions follow strict account safety constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

from gflow_cli.api.transports.batchexecute import (
    UNUSUAL_ACTIVITY_REASON,
    RpcError,
    find_record,
    image_records,
    parse_frames,
    rpc_errors,
)
from gflow_cli.errors import (
    CONTENT_SAFETY_REASONS,
    ContentPolicyError,
    GFlowError,
    WireFormatError,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Maximum concurrent direct submit attempts allowed in flight per process and profile.
MAX_IN_FLIGHT: int = 4
#: The start-rate warning's window: more than 8 starts in it is logged.
START_RATE_WINDOW_S: float = 120.0

#: Wall-clock threshold in seconds beyond which an idle page must re-arm before minting.
REARM_GAP_SECONDS: float = 600.0


class BrowserVisibility(StrEnum):
    """Target browser visibility for launches and relaunches."""

    HEADLESS = "headless"
    HEADED = "headed"


class VisibilitySetting(StrEnum):
    """User-configured visibility preference."""

    AUTO = "auto"
    HEADLESS = "headless"
    HEADED = "headed"


@dataclass(frozen=True)
class NotSent:
    """Outcome when no byte of the billed submit left the client process."""

    reason: str
    auth: bool = False


@dataclass(frozen=True)
class Accepted:
    """Outcome when the submit was accepted and processed by Flow."""

    body: str


@dataclass(frozen=True)
class RefusedNotBilled:
    """Outcome when Flow explicitly refused the submit without charging credits."""

    rpcid: str
    reason: str


@dataclass(frozen=True)
class Indeterminate:
    """Outcome when the submit status is unknown and cannot be safely resent."""

    rpcid: str
    cause: str
    status: int | None = None
    #: The ``ErrorInfo`` reasons and gRPC code of Flow's error frame(s) for ``rpcid``, if any.
    reasons: tuple[str, ...] = ()
    code: int | None = None


SubmitOutcome = NotSent | Accepted | RefusedNotBilled | Indeterminate
EndedOutcome = NotSent | RefusedNotBilled | Indeterminate


# Named for the signal it carries to the caller, not as a fault, hence no "Error" suffix.
class DirectAttemptEnded(Exception):  # noqa: N818
    """A direct attempt that did not end ``Accepted``.

    ``error`` is the user-facing error for an outcome past the send (the reconcile error, or
    the typed refusal); a ``NotSent`` outcome carries none, since nothing reached Flow.
    """

    def __init__(self, outcome: EndedOutcome, *, error: GFlowError | None = None) -> None:
        super().__init__(type(outcome).__name__)
        self.outcome = outcome
        self.error = error


def not_sent(reason: str, *, auth: bool = False) -> DirectAttemptEnded:
    """A failure before the billed write was sent: nothing reached Flow."""
    return DirectAttemptEnded(NotSent(reason, auth=auth))


@dataclass(frozen=True)
class Done:
    """Terminal state: generation completed or accepted."""


@dataclass(frozen=True)
class RetryDirect:
    """Retry the submission directly using a fresh browser context."""

    visibility: BrowserVisibility


@dataclass(frozen=True)
class FallbackUi:
    """Fall back to driving the user interface."""

    visibility: BrowserVisibility


StopKind = Literal["waf_final", "reconcile", "reauth"]


@dataclass(frozen=True)
class Stop:
    """Terminal state requiring specific operator or caller remediation."""

    kind: Literal["waf_final", "reconcile", "reauth"]


RetryStep = Done | RetryDirect | FallbackUi | Stop


def launch_visibility(setting: VisibilitySetting) -> BrowserVisibility:
    """Map a visibility setting to the concrete launch visibility for a direct retry."""
    if setting == VisibilitySetting.HEADED:
        return BrowserVisibility.HEADED
    return BrowserVisibility.HEADLESS


def _has_generation_record(body: str) -> bool:
    """Determine whether the response envelope contains any generation record."""
    frames = parse_frames(body)
    for frame_rpcid, payload in frames:
        if payload is None:
            continue
        if find_record(payload) is not None:
            return True
        try:
            if image_records(frame_rpcid, payload):
                return True
        except WireFormatError:
            pass
    return False


def classify_reply(
    rpcid: str,
    status: int | None,
    body: str | None = None,
) -> SubmitOutcome:
    """Classify an HTTP response from a batchexecute submission into a SubmitOutcome.

    RefusedNotBilled is returned only for HTTP 200 with an error frame for rpcid
    carrying UNUSUAL_ACTIVITY and no generation record in the same body. Named
    content safety reasons raise ContentPolicyError. All other non-200 statuses,
    bare statuses, unrecognized reasons, and unparseable bodies are classified as
    Indeterminate.
    """
    if status is None:
        return Indeterminate(rpcid=rpcid, cause="timeout after send", status=None)

    raw_body = body or ""
    matching_errors = [err for err in rpc_errors(raw_body) if err.rpcid == rpcid]
    _raise_content_refusal(rpcid, matching_errors)

    if status != 200:
        return Indeterminate(rpcid=rpcid, cause=f"HTTP {status}", status=status)

    if matching_errors:
        return _classify_rpc_errors(rpcid, raw_body, matching_errors)

    if not _has_rpc_frame(raw_body, rpcid):
        return Indeterminate(
            rpcid=rpcid,
            cause="unparseable reply or missing rpc frame",
            status=status,
        )

    return Accepted(body=raw_body)


def _has_rpc_frame(body: str, rpcid: str) -> bool:
    """Whether the body carries a parsed, non-null payload frame for ``rpcid``."""
    return any(f[0] == rpcid and f[1] is not None for f in parse_frames(body))


def _raise_content_refusal(rpcid: str, errors: list[RpcError]) -> None:
    """Raise ``ContentPolicyError`` for the first named content safety reason, if any."""
    for err in errors:
        safety = next((r for r in err.reasons if r in CONTENT_SAFETY_REASONS), None)
        if safety is not None:
            raise ContentPolicyError(
                detail=f"Flow refused the submit: {safety} (gRPC {err.code})",
                route=f"batchexecute:{rpcid}",
            )


def _classify_rpc_errors(rpcid: str, raw_body: str, errors: list[RpcError]) -> SubmitOutcome:
    """An HTTP 200 reply carrying error frames for ``rpcid``: refused only when unambiguous."""
    err = errors[0]
    if len(errors) == 1 and UNUSUAL_ACTIVITY_REASON in err.reasons:
        if _has_generation_record(raw_body):
            return Indeterminate(
                rpcid=rpcid,
                cause="refusal error frame accompanied by generation record",
                status=200,
            )
        return RefusedNotBilled(rpcid=rpcid, reason=UNUSUAL_ACTIVITY_REASON)
    return Indeterminate(
        rpcid=rpcid,
        cause=f"rpc error: {err.reasons or [err.code]}",
        status=200,
        reasons=tuple(r for e in errors for r in e.reasons),
        code=err.code,
    )


def next_step(
    outcome: SubmitOutcome,
    *,
    setting: VisibilitySetting,
    retried: bool,
) -> RetryStep:
    """Compute the next action to take given the current submit outcome."""
    if isinstance(outcome, Accepted):
        return Done()

    if isinstance(outcome, RefusedNotBilled):
        if not retried:
            return RetryDirect(launch_visibility(setting))
        return Stop("waf_final")

    if isinstance(outcome, Indeterminate):
        return Stop("reconcile")

    if outcome.auth:
        return Stop("reauth")

    if setting == VisibilitySetting.HEADLESS:
        return FallbackUi(BrowserVisibility.HEADLESS)
    return FallbackUi(BrowserVisibility.HEADED)


def needs_rearm(*, last_mint_wall: float | None, now_wall: float) -> bool:
    """Check if the wall-clock gap since the last mint exceeds the design threshold."""
    if last_mint_wall is None:
        return False
    return (now_wall - last_mint_wall) > REARM_GAP_SECONDS


def starts_exceed(
    history: Iterable[float],
    now: float,
    *,
    limit: int = 8,
    window_s: float = START_RATE_WINDOW_S,
) -> bool:
    """Return whether recorded submit start timestamps in the window exceed limit."""
    count = sum(1 for ts in history if 0.0 <= (now - ts) <= window_s)
    return count > limit


class RecaptchaTokenSource(Protocol):
    """Port for minting reCAPTCHA Enterprise action tokens."""

    async def mint(self, action: str) -> str: ...
