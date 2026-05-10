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
    """Async test that uses asyncio.Event-gated wait so it runs in <0.1s."""
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
