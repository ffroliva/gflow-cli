from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

# Ensure stdout/stdin are configured for UTF-8 to prevent Windows pipe encoding crashes
if sys.platform == "win32":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
    if sys.stdin and hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
    if sys.stderr and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[reportAttributeAccessIssue,reportUnknownMemberType]

# Initialize FastMCP instance
mcp = FastMCP("gflow")

# Import sub-modules to register decorated tools, prompts, and resources
from gflow_cli.mcp import prompts as prompts  # noqa: E402, F401
from gflow_cli.mcp import resources as resources  # noqa: E402, F401
from gflow_cli.mcp import tools as tools  # noqa: E402, F401
