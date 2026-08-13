# SPDX-License-Identifier: MIT
"""MCP resources — static and dynamic documentation endpoints.

Resources provide read-only reference material that AI agents can
consult before making tool calls. This prevents hallucinated CLI
scripting and guides agents toward the proper tool-based interface.
"""

from __future__ import annotations

import re
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


# #501: KNOWN_ISSUES.md is ~70 KB and grows every release — an unbounded
# read_text() injected all of it into the agent's context on every fetch.
# The default read is a small index; one templated fetch serves a section.

_SECTION_CAP_BYTES = 16 * 1024


def _slugify(title: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", title.lower())).strip("-")


def _iter_issue_sections(text: str) -> list[tuple[str, str, str]]:
    """Yield (slug, title, body) for every ``### `` heading in the doc."""
    sections: list[tuple[str, str, str]] = []
    parts = re.split(r"^### (.+)$", text, flags=re.MULTILINE)
    # parts = [preamble, title1, body1, title2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        title = parts[i].strip()
        sections.append((_slugify(title), title, parts[i + 1]))
    return sections


def _build_known_issues_index(text: str) -> str:
    lines = [
        "# Known issues — index",
        "",
        "Full text of one issue: read `gflow://docs/known-issues/<slug>`.",
        "",
    ]
    for slug, title, body in _iter_issue_sections(text):
        status = ""
        match = re.search(r"\*\*Status:\*\*\s*(.+)", body)
        if match:
            status = " — " + re.sub(r"[*\[\]]", "", match.group(1)).split("(")[0].strip()[:80]
        lines.append(f"- `{slug}` — {title}{status}")
    return "\n".join(lines)


def _extract_known_issue_section(text: str, slug: str) -> str | None:
    for section_slug, title, body in _iter_issue_sections(text):
        if section_slug == slug:
            return f"### {title}\n{body.rstrip()}\n"
    return None


def _cap_section(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= _SECTION_CAP_BYTES:
        return text
    return (
        encoded[:_SECTION_CAP_BYTES].decode("utf-8", errors="ignore")
        + "\n\n[truncated at 16 KB — read KNOWN_ISSUES.md in the repo for the rest]"
    )


def _known_issues_text() -> str | None:
    path = _REPO_ROOT / "KNOWN_ISSUES.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


@server.resource(
    uri="gflow://docs/known-issues",
    name="Known Issues",
    description=(
        "Index of current known issues (titles + status). Read "
        "gflow://docs/known-issues/{slug} for one issue's full text."
    ),
)
async def known_issues() -> str:
    """Return a bounded index of KNOWN_ISSUES.md (#501)."""
    text = _known_issues_text()
    if text is None:
        return "KNOWN_ISSUES.md not found."
    return _build_known_issues_index(text)


@server.resource(
    uri="gflow://docs/known-issues/{slug}",
    name="Known Issue Section",
    description="Full text of a single known issue, by slug from the index.",
)
async def known_issues_section(slug: str) -> str:
    """Return one issue's full text, capped at 16 KB (#501)."""
    text = _known_issues_text()
    if text is None:
        return "KNOWN_ISSUES.md not found."
    section = _extract_known_issue_section(text, slug)
    if section is None:
        return (
            f"Unknown section slug {slug!r}. Read gflow://docs/known-issues "
            "for the index of valid slugs."
        )
    return _cap_section(section)


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
