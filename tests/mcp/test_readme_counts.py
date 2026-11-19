"""The README quotes the MCP tool count; keep it equal to what the server registers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_README = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_mcp_tool_count_matches_the_server(mcp_server: Any) -> None:
    match = re.search(r"\*\*(\d+) MCP tools\*\*", _README.read_text(encoding="utf-8"))
    assert match, "README.md must state the MCP tool count as **N MCP tools**"
    registered = len(mcp_server._tool_manager._tools)  # pyright: ignore[reportPrivateUsage]
    assert int(match.group(1)) == registered, (
        f"README says {match.group(1)} MCP tools; the server registers {registered}"
    )
