"""Test doubles shared across the suite."""

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock


class _Absent(int):
    """What an unconfigured await yields: zero, false, empty and iterable."""

    def __len__(self) -> int:
        return 0

    def __iter__(self) -> Any:
        return iter(())

    def get(self, _key: object, default: Any = None) -> Any:
        return default


class AwaitableMock(MagicMock):
    """A MagicMock whose unconfigured calls can be awaited, and report nothing found.

    Best-effort probes wrap Playwright in ``except Exception``. Awaiting a plain MagicMock
    raises TypeError there, so the probe never ran and the test passed anyway.
    """

    def __await__(self) -> Generator[Any, None, _Absent]:
        async def absent() -> _Absent:
            return _Absent()

        return absent().__await__()
