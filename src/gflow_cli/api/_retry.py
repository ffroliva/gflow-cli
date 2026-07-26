"""Private retry layer for FlowApiClient.

Uses ``tenacity.AsyncRetrying`` with ``reraise=True`` so the original
:class:`gflow_cli.errors.GFlowError` surfaces (no ``RetryError`` leakage).

Constants:
    MAX_ATTEMPTS = 3
    RETRY_AFTER_CAP_SECONDS = 60.0

Public API:
    post_with_retry(retry_on_5xx: bool) -> AsyncIterator yielding AttemptManager
    parse_retry_after(response) -> float | None  (cap-applied)

Internal API (test-only):
    _make_retrying(*, wait_seconds=None) -> AsyncRetrying — allows tests to inject
        a zero-wait function so the retry suite runs in <100ms instead of
        burning through 1+2+4=7s of real backoff per case.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any, cast

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
)
from tenacity.wait import wait_base

from gflow_cli.api._engine import retryable_engine_errors
from gflow_cli.errors import NetworkError, RateLimitError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

MAX_ATTEMPTS = 3
RETRY_AFTER_CAP_SECONDS = 60.0


def _get_headers_dict(obj: Any) -> dict[str, str] | None:
    if obj is None:
        return None
    headers_val: Any = getattr(obj, "headers", None)
    if headers_val is None and isinstance(obj, dict):
        d_obj: dict[Any, Any] = cast("dict[Any, Any]", obj)
        headers_val = d_obj.get("headers")
        if headers_val is None:
            headers_val = d_obj
    if headers_val is None:
        return None
    if isinstance(headers_val, dict):
        d_hdr: dict[Any, Any] = cast("dict[Any, Any]", headers_val)
        return {str(k).lower(): str(v) for k, v in d_hdr.items()}
    if hasattr(headers_val, "get"):
        get_fn: Any = headers_val.get
        val: Any = get_fn("retry-after") or get_fn("Retry-After")
        if val is not None:
            return {"retry-after": str(val)}
    return None


def parse_retry_after(response: Any) -> float | None:
    """Extract the ``Retry-After`` header (seconds form only). Caps at 60s.

    Returns ``None`` if header absent or malformed. The HTTP-date form of
    ``Retry-After`` is intentionally NOT supported — Flow's upstream emits
    integer seconds in observed captures, and parsing RFC 7231 dates would
    add a dependency without buying observable value. Accepts objects with a
    ``.headers`` attribute, dicts with a ``"headers"`` key, or direct header dicts.
    """
    headers_dict = _get_headers_dict(response)
    if not headers_dict:
        return None
    raw = headers_dict.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return min(seconds, RETRY_AFTER_CAP_SECONDS)


class _JitteredExponentialWait(wait_base):
    """1s±25% → 2s±25% → 4s±25% with Retry-After override (capped at 60s).

    If the previous attempt raised :class:`RateLimitError` with ``retry_after``
    set, that value (capped) wins over the exponential schedule for the next
    attempt's wait — honoring the server's explicit backoff hint.
    """

    def __call__(self, retry_state: RetryCallState) -> float:
        last_exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(last_exc, RateLimitError) and last_exc.retry_after is not None:
            return min(last_exc.retry_after, RETRY_AFTER_CAP_SECONDS)
        attempt = retry_state.attempt_number  # 1-indexed
        base = 2 ** (attempt - 1)  # 1, 2, 4
        # Backoff jitter only — not a security primitive; S311 is acceptable here.
        jitter = base * 0.25 * (2 * random.random() - 1)  # noqa: S311
        return max(0.0, base + jitter)


class _LambdaWait(wait_base):
    """Test override; constructor takes ``fn: Callable[[RetryCallState], float]``."""

    def __init__(self, fn: Callable[[RetryCallState], float]) -> None:
        self._fn = fn

    def __call__(self, retry_state: RetryCallState) -> float:
        return self._fn(retry_state)


def _make_retrying(
    *,
    wait_seconds: Callable[[RetryCallState], float] | None = None,
) -> AsyncRetrying:
    """Internal factory; tests override ``wait_seconds`` to skip real sleeps.

    Retry policy: on :class:`NetworkError`, :class:`RateLimitError`, AND on the
    transport-level Playwright errors (:class:`playwright.async_api.Error` and
    :class:`playwright.async_api.TimeoutError`) so a TCP reset / DNS hiccup /
    connect timeout mid-attempt is retried rather than surfaced raw to callers.
    4xx classification raises :class:`AuthExpiredError` / :class:`WireFormatError`
    /  :class:`ContentPolicyError` which fall straight through (no retry) because
    those classes are NOT in ``retry_if_exception_type``.
    """
    waiter: wait_base = (
        _LambdaWait(wait_seconds) if wait_seconds is not None else _JitteredExponentialWait()
    )
    return AsyncRetrying(
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=waiter,
        retry=retry_if_exception_type(
            # Patchright raises its OWN Error/TimeoutError (distinct classes from
            # playwright's), so retryable_engine_errors() unions both engines —
            # otherwise a patchright transport hiccup would surface raw.
            (NetworkError, RateLimitError, *retryable_engine_errors()),
        ),
        reraise=True,
    )


def post_with_retry(*, _retry_on_5xx: bool = True) -> AsyncIterator[Any]:
    """Public: returns the configured ``AsyncRetrying`` async iterator.

    Args:
        _retry_on_5xx: Reserved for future toggling — the leading underscore
            signals "intentionally unused". Currently retries on 5xx and 429
            because both surface as :class:`NetworkError` /
            :class:`RateLimitError` and the retry predicate matches them.

    Usage::

        async for retrying in post_with_retry():
            with retrying:
                resp = await page.request.post(url, ...)
                if resp.status == 429:
                    raise RateLimitError(status=429, retry_after=parse_retry_after(resp))
                if resp.status >= 500:
                    raise NetworkError(status=resp.status)
                # 4xx (non-429) falls through; classifier outside the loop turns
                # them into AuthExpiredError / WireFormatError (NOT retried).
    """
    return _make_retrying().__aiter__()
