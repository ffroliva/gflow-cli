# SPDX-License-Identifier: MIT
"""gflow-cli MCP server — exposes core gflow tools via Model Context Protocol.

This package contains the MCPServer implementation, tool schemas,
prompt templates, and resource endpoints for IDE/agent integration.

Entry points:
    - ``gflow mcp run``  — stdio transport (Claude Desktop, Cursor, etc.)
    - ``gflow serve``    — HTTP/SSE transport (Gflow Studio, web clients)
"""

from __future__ import annotations
