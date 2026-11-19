"""Published pages quote the MCP tool count; keep it equal to the server's."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("page", "pattern"),
    [
        ("README.md", r"\*\*(\d+) MCP tools\*\*"),
        ("index.html", r"serving (\d+) tools"),
        ("llms.txt", r"MCP server with (\d+) tools"),
    ],
)
def test_published_mcp_tool_count_matches_the_server(
    mcp_server: Any, page: str, pattern: str
) -> None:
    match = re.search(pattern, (_ROOT / page).read_text(encoding="utf-8"))
    assert match, f"{page} must state the MCP tool count ({pattern})"
    registered = len(mcp_server._tool_manager._tools)  # pyright: ignore[reportPrivateUsage]
    assert int(match.group(1)) == registered, (
        f"{page} says {match.group(1)} MCP tools; the server registers {registered}"
    )
