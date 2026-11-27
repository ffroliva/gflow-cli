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
    find_record,
    image_records,
    parse_frames,
    rpc_errors,
)
from gflow_cli.errors import CONTENT_SAFETY_REASONS, ContentPolicyError, WireFormatError

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Maximum concurrent direct submit attempts allowed in flight per process and profile.
MAX_IN_FLIGHT: int = 4

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


SubmitOutcome = NotSent | Accepted | RefusedNotBilled | Indeterminate


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
    errors = rpc_errors(raw_body)
    matching_errors = [err for err in errors if err.rpcid == rpcid]

    for err in matching_errors:
        safety = next((r for r in err.reasons if r in CONTENT_SAFETY_REASONS), None)
        if safety is not None:
            raise ContentPolicyError(
                detail=f"Flow refused the submit: {safety} (gRPC {err.code})",
                route=f"batchexecute:{rpcid}",
            )

    if status != 200:
        return Indeterminate(rpcid=rpcid, cause=f"HTTP {status}", status=status)

    if len(matching_errors) == 1:
        err = matching_errors[0]
        if any(r == UNUSUAL_ACTIVITY_REASON for r in err.reasons):
            if _has_generation_record(raw_body):
                return Indeterminate(
                    rpcid=rpcid,
                    cause="refusal error frame accompanied by generation record",
                    status=status,
                )
            return RefusedNotBilled(rpcid=rpcid, reason=UNUSUAL_ACTIVITY_REASON)

    if matching_errors:
        err = matching_errors[0]
        return Indeterminate(
            rpcid=rpcid,
            cause=f"rpc error: {err.reasons or [err.code]}",
            status=status,
        )

    frames = parse_frames(raw_body)
    matching_frames = [f for f in frames if f[0] == rpcid and f[1] is not None]
    if not matching_frames:
        return Indeterminate(
            rpcid=rpcid,
            cause="unparseable reply or missing rpc frame",
            status=status,
        )

    return Accepted(body=raw_body)


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
    window_s: float = 120.0,
) -> bool:
    """Return whether recorded submit start timestamps in the window exceed limit."""
    count = sum(1 for ts in history if 0.0 <= (now - ts) <= window_s)
    return count > limit


class RecaptchaTokenSource(Protocol):
    """Port for minting reCAPTCHA Enterprise action tokens."""

    async def mint(self, action: str) -> str: ...
