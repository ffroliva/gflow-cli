# SPDX-License-Identifier: MIT
"""The faked image tool shared by the OpenAI-compatible route tests (#1365)."""

from __future__ import annotations

from typing import Any

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeTool:
    """Stands in for ``tools.gflow_generate_image``: records each call, answers ``result``."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.result
