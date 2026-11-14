"""Token-bucket rate limiter for the MCP generation tools."""

from __future__ import annotations

import asyncio
import time

__all__ = ["_TokenBucket"]

_BUCKET_CAPACITY = 8
_BUCKET_REFILL_RATE = 1 / 20  # 1 token every 20 seconds


class _TokenBucket:
    """Simple token-bucket rate limiter for generation tools."""

    def __init__(self, capacity: int = _BUCKET_CAPACITY, refill_rate: float = _BUCKET_REFILL_RATE):
        self._capacity = capacity
        self._tokens = float(capacity)
        self._refill_rate = refill_rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """Try to acquire a token. Returns True if acquired, False if rate-limited."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
            self._last_refill = now

            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False
