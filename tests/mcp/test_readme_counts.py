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
        ("README.zh-CN.md", r"\*\*(\d+) 个 MCP 工具\*\*"),
        ("index.html", r"serving (\d+) tools"),
        ("llms.txt", r"MCP server with (\d+) tools"),
        ("llms.txt", r"against gflow-cli's (\d+)"),
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


def test_chinese_readme_lists_the_same_model_aliases() -> None:
    alias = re.compile(r"`(omni-[\w.]+|veo-[\w]+|nano[\w-]*|imagen4)`")
    english = set(alias.findall((_ROOT / "README.md").read_text(encoding="utf-8")))
    chinese = set(alias.findall((_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")))
    assert english, "README.md must list model aliases"
    assert chinese == english, f"README.zh-CN.md model aliases differ: {chinese ^ english}"
