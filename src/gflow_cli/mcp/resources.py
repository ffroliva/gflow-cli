# SPDX-License-Identifier: MIT
"""MCP resources — static and dynamic documentation endpoints.

Resources provide read-only reference material that AI agents can
consult before making tool calls. This prevents hallucinated CLI
scripting and guides agents toward the proper tool-based interface.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from gflow_cli.mcp.server import server

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Static resources
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]  # src/gflow_cli/mcp → repo root


@server.resource(
    uri="gflow://docs/mcp-guide",
    name="MCP Agent Guide",
    description=(
        "Instructions for AI agents on how to use gflow-cli tools. "
        "Read this BEFORE making any tool calls."
    ),
)
async def mcp_guide() -> str:
    """Return the MCP agent guide."""
    return """\
# gflow-cli MCP Agent Guide

You have access to the following tools for generating media via Google Flow:

## Available Tools

### gflow_generate_image
Generate images using Google's Imagen model.
- **Models:** nano2 (fastest), nano-pro (balanced), image4 (highest quality)
- **Aspects:** 1:1, 9:16, 16:9, 4:3, 3:4
- **Count:** 1-4 images per call

### gflow_generate_video
Generate videos using Google's Veo model.
- **Modes:** t2v (text-to-video), i2v (image-to-video), r2v (reference-to-video)
- **Aspects:** 9:16 (portrait), 16:9 (landscape)
- **Note:** i2v/r2v require an image_path

### gflow_list_projects
Browse the local project catalog.

### gflow_list_characters
Browse reusable Flow Character entities.

### gflow_list_tools
List available gflow prompt tools (name, title, description, category).

## Important Rules
1. **Use tools, not shell commands.** Do NOT run `gflow image t2i ...` via the terminal.
2. **One generation at a time.** The rate limiter allows bursts of 8 but throttles sustained use.
3. **Check auth first.** If a tool returns an auth error, advise the user to
   run `gflow auth login --browser chrome`.
4. **Credits cost money.** Always confirm with the user before generating,
   especially video (20 credits each).
"""


@server.resource(
    uri="gflow://docs/known-issues",
    name="Known Issues",
    description="Current known issues and workarounds for gflow-cli.",
)
async def known_issues() -> str:
    """Return KNOWN_ISSUES.md contents."""
    known_issues_path = _REPO_ROOT / "KNOWN_ISSUES.md"
    if known_issues_path.exists():
        return known_issues_path.read_text(encoding="utf-8")
    return "KNOWN_ISSUES.md not found."


@server.resource(
    uri="gflow://db/schema",
    name="Database Schema",
    description="SQLite database schema for gflow.db — operations catalog.",
)
async def db_schema() -> str:
    """Return the initial migration SQL as a schema reference."""
    migration_path = _REPO_ROOT / "src" / "gflow_cli" / "data" / "migrations" / "0001_initial.sql"
    if migration_path.exists():
        return migration_path.read_text(encoding="utf-8")
    return "Migration file not found."
