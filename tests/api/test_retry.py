"""tenacity-based retry layer + 4xx-no-retry + Retry-After cap + reraise=True."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from gflow_cli.api._retry import (
    MAX_ATTEMPTS,
    RETRY_AFTER_CAP_SECONDS,
    parse_retry_after,
    post_with_retry,
)
from gflow_cli.errors import (
    AuthExpiredError,
    NetworkError,
    RateLimitError,
    WireFormatError,
)


def _resp(status: int, headers: dict[str, str] | None = None):
    r = MagicMock()
    r.status = status
    r.headers = headers or {}
    return r


@pytest.mark.asyncio
async def test_5xx_retried_3_times_then_raises_NetworkError():  # noqa: N802 — class name in test ID
    """3 attempts on 5xx, original exception reraised (no RetryError). Zero-wait
    via `_make_retrying(wait_seconds=lambda _: 0)` so the test runs in <50ms
    instead of incurring 1+2+4=7s of real backoff."""
    from gflow_cli.api._retry import _make_retrying

    attempts = {"n": 0}

    async def attempt():
        attempts["n"] += 1
        return _resp(503)

    retrying = _make_retrying(wait_seconds=lambda _: 0)
    with pytest.raises(NetworkError) as ei:
        async for r in retrying:
            with r:
                resp = await attempt()
                if resp.status >= 500:
                    raise NetworkError(detail=f"HTTP {resp.status}", status=resp.status)
    assert attempts["n"] == MAX_ATTEMPTS
    # reraise=True: original NetworkError surfaces, NOT tenacity.RetryError.
    assert isinstance(ei.value, NetworkError)


@pytest.mark.asyncio
async def test_429_retried_then_RateLimitError():  # noqa: N802 — class name in test ID
    """Zero-wait variant — same rationale as the 5xx test above."""
    from gflow_cli.api._retry import _make_retrying

    attempts = {"n": 0}

    async def attempt():
        attempts["n"] += 1
        return _resp(429, headers={"retry-after": "2"})

    retrying = _make_retrying(wait_seconds=lambda _: 0)
    with pytest.raises(RateLimitError):
        async for r in retrying:
            with r:
                resp = await attempt()
                if resp.status == 429:
                    raise RateLimitError(
                        detail="429",
                        status=429,
                        retry_after=parse_retry_after(resp),
                    )
    assert attempts["n"] == MAX_ATTEMPTS


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_4xx_not_retried(status):
    """4xx (except 429) MUST NOT be retried."""
    attempts = {"n": 0}

    async def attempt():
        attempts["n"] += 1
        return _resp(status)

    err_cls = AuthExpiredError if status in (401, 403) else WireFormatError
    with pytest.raises(err_cls):
        async for retrying in post_with_retry():
            with retrying:
                resp = await attempt()
                if resp.status in (401, 403):
                    raise AuthExpiredError(detail=f"HTTP {status}", status=status)
                if 400 <= resp.status < 500 and resp.status != 429:
                    raise WireFormatError(detail=f"HTTP {status}", status=status)
    assert attempts["n"] == 1  # NOT retried


def test_parse_retry_after_seconds():
    assert parse_retry_after(_resp(429, headers={"retry-after": "30"})) == 30.0


def test_parse_retry_after_caps_at_60():
    assert parse_retry_after(_resp(429, headers={"retry-after": "999"})) == RETRY_AFTER_CAP_SECONDS


def test_parse_retry_after_missing_returns_none():
    assert parse_retry_after(_resp(429)) is None


@pytest.mark.asyncio
async def test_event_gated_retry_does_not_block_real_time():
    """Async test that uses asyncio.Event-gated wait so it runs in <0.1s.

    The ``asyncio.Event`` is set inside the attempt body so we can both (a)
    prove zero-wait retries complete promptly and (b) assert that the first
    attempt actually ran before the retry loop exhausts — addresses code-rev
    MEDIUM-4 about the previously unasserted Event.
    """
    started = asyncio.Event()
    attempts = {"n": 0}

    async def attempt():
        attempts["n"] += 1
        started.set()
        return _resp(503)

    # Use a custom retry config with near-zero wait for the test only.
    from gflow_cli.api._retry import _make_retrying

    retrying = _make_retrying(wait_seconds=lambda _attempt: 0)
    with pytest.raises(NetworkError):
        async for r in retrying:
            with r:
                resp = await attempt()
                if resp.status >= 500:
                    raise NetworkError(detail=f"HTTP {resp.status}", status=resp.status)
    assert attempts["n"] == MAX_ATTEMPTS
    assert started.is_set()  # at least one attempt actually executed


@pytest.mark.asyncio
async def test_playwright_error_retried():
    """Spec C2: :class:`playwright.async_api.Error` is in the retry set.

    Transport-level Playwright failures (TCP reset, DNS hiccup, connect
    timeout) need to retry rather than surface raw to the caller.
    """
    from playwright.async_api import Error as PlaywrightError

    from gflow_cli.api._retry import _make_retrying

    attempts = {"n": 0}

    async def attempt():
        attempts["n"] += 1
        raise PlaywrightError("simulated network failure")

    retrying = _make_retrying(wait_seconds=lambda _: 0)
    with pytest.raises(PlaywrightError):
        async for r in retrying:
            with r:
                await attempt()
    assert attempts["n"] == MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_playwright_timeout_error_retried():
    """Spec C2: :class:`playwright.async_api.TimeoutError` is in the retry set."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    from gflow_cli.api._retry import _make_retrying

    attempts = {"n": 0}

    async def attempt():
        attempts["n"] += 1
        raise PlaywrightTimeoutError("simulated timeout")

    retrying = _make_retrying(wait_seconds=lambda _: 0)
    with pytest.raises(PlaywrightTimeoutError):
        async for r in retrying:
            with r:
                await attempt()
    assert attempts["n"] == MAX_ATTEMPTS


def test_jittered_exponential_wait_default_schedule():
    """Exercise :class:`_JitteredExponentialWait` default exponential schedule.

    Without this test all retry tests inject ``_LambdaWait(lambda _: 0)`` and
    ``_JitteredExponentialWait.__call__`` stays 0% executed — pulling the
    module under the 80% coverage floor. Pins schedule: 1s, 2s, 4s ±25%.
    """
    from gflow_cli.api._retry import _JitteredExponentialWait

    waiter = _JitteredExponentialWait()
    # Tenacity passes a RetryCallState. We only need outcome (None → use
    # default schedule) and attempt_number (1-indexed).
    state = MagicMock()
    state.outcome = None
    state.attempt_number = 1
    wait_1 = waiter(state)
    state.attempt_number = 2
    wait_2 = waiter(state)
    state.attempt_number = 3
    wait_3 = waiter(state)
    # base = 2 ** (attempt - 1), so 1, 2, 4. Jitter is ±25% of base.
    assert 0.75 <= wait_1 <= 1.25
    assert 1.5 <= wait_2 <= 2.5
    assert 3.0 <= wait_3 <= 5.0


def test_jittered_exponential_wait_honors_retry_after_cap():
    """If previous attempt raised :class:`RateLimitError` with ``retry_after``,
    that value (capped at 60s) wins over the exponential schedule."""
    from gflow_cli.api._retry import _JitteredExponentialWait

    waiter = _JitteredExponentialWait()
    state = MagicMock()
    outcome = MagicMock()
    outcome.exception.return_value = RateLimitError(retry_after=999.0)  # way over cap
    state.outcome = outcome
    state.attempt_number = 2
    wait = waiter(state)
    assert wait == RETRY_AFTER_CAP_SECONDS  # capped at 60.0


def test_jittered_exponential_wait_honors_retry_after_uncapped():
    """RateLimitError with small ``retry_after`` (under the cap) is used directly."""
    from gflow_cli.api._retry import _JitteredExponentialWait

    waiter = _JitteredExponentialWait()
    state = MagicMock()
    outcome = MagicMock()
    outcome.exception.return_value = RateLimitError(retry_after=5.0)
    state.outcome = outcome
    state.attempt_number = 2
    assert waiter(state) == 5.0
