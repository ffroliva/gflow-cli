from __future__ import annotations

from pathlib import Path

from gflow_cli.config import get_settings
from gflow_cli.data.store import DataStore
from gflow_cli.mcp.server import mcp


@mcp.resource("gflow://docs/mcp-guide")
def get_mcp_guide() -> str:
    """Guide for AI agents using gflow tools."""
    return (
        "# gflow-cli MCP Server Guide\n\n"
        "You are an AI agent connected to the Google Flow CLI via MCP.\n"
        "Please follow these guidelines:\n"
        "1. Prefer using the registered tool functions (`gflow_generate_image`, "
        "`gflow_generate_video`, etc.) directly rather than trying to construct "
        "terminal commands to run via a shell.\n"
        "2. When generating images or videos, use the correct aspect ratio codes "
        "('1:1', '16:9', '9:16').\n"
        "3. Session outputs are stored locally on the host. The tool results return "
        "the local file paths and URI schemes.\n"
        "4. Budget and credit limits are enforced at the session level. "
        "Failures are reported with clear rate limit details."
    )


@mcp.resource("gflow://docs/known-issues")
def get_known_issues() -> str:
    """Current KNOWN_ISSUES.md context and limitations."""
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    known_issues_path = project_root / "KNOWN_ISSUES.md"
    if known_issues_path.exists():
        return known_issues_path.read_text(encoding="utf-8")
    return "KNOWN_ISSUES.md not found at project root."


@mcp.resource("gflow://db/schema")
def get_db_schema() -> str:
    """Direct SQLite database table schema details."""
    settings = get_settings()
    db_path = settings.resolved_db_path()

    schemas: list[str] = []
    try:
        with DataStore.open(db_path) as store:
            cursor = store.conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index') AND sql IS NOT NULL"
            )
            for row in cursor.fetchall():
                schemas.append(f"-- {row['name']}\n{row['sql']};\n")
        return "\n".join(schemas)
    except Exception as exc:
        return f"Error retrieving database schema: {exc}"
