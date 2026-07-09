# SPDX-License-Identifier: MIT
"""MCP↔CLI parity contract (AGENTS.md "MCP & CLI Schema Symmetry").

Every CLI leaf command must carry an explicit MCP decision: either it maps to
a registered FastMCP tool (``CLI_TO_MCP``) or it is deliberately exempt with a
stated reason (``_MCP_EXEMPT``). A new CLI command with neither entry fails
``test_every_cli_leaf_has_an_mcp_decision`` — forcing the parity decision at
review time instead of letting the surfaces drift apart silently
(2026-07-09 skills-audit council, Task 6).

Note on ``gflow_generate_video`` and instructions: unlike the image path, the
video pipeline (``GenerateVideoRequest`` / the worker's ``_build_video_request``)
has no instructions support — agentic-video is a deliberate typed divergence
(``drivers/agentic.py`` raises ``FlowAgentUiError``). An ``instructions`` param
on the video tool would be silently dropped, so it is intentionally absent.
"""

from __future__ import annotations

from typing import Any

import click

from gflow_cli.cli import main

# CLI leaf → MCP tool that covers it. One tool may cover several leaves when a
# parameter selects the behaviour (e.g. gflow_generate_video's ``mode``).
CLI_TO_MCP: dict[str, str] = {
    "image t2i": "gflow_generate_image",
    "image i2i": "gflow_generate_image",  # reference_images param
    "video t2v": "gflow_generate_video",  # mode="t2v"
    "video i2v": "gflow_generate_video",  # mode="i2v"
    "video r2v": "gflow_generate_video",  # mode="r2v"
    "tools list": "gflow_list_tools",
    "tools show": "gflow_list_tools",  # list output carries the show detail
    "data list projects": "gflow_list_projects",
    "character list": "gflow_list_characters",
    "instructions list": "gflow_instructions_list",
    "instructions add": "gflow_instructions_add",
    "instructions enable": "gflow_instructions_set_enabled",  # enabled=True
    "instructions disable": "gflow_instructions_set_enabled",  # enabled=False
    "instructions rm": "gflow_instructions_rm",
    "instructions apply": "gflow_instructions_apply",
    "instructions toggle-mode": "gflow_instructions_toggle_mode",
}

# CLI leaf → reason it deliberately has NO MCP surface. "not yet ported"
# entries are backlog, not policy — moving one to CLI_TO_MCP is the upgrade
# path. Everything else is a considered exclusion.
_MCP_EXEMPT: dict[str, str] = {
    "auth": "interactive session management — needs a human browser login flow",
    "auth list": "interactive session management",
    "auth login": "interactive session management",
    "auth logout": "interactive session management",
    "auth status": "interactive session management",
    "auth use": "interactive session management",
    "mcp run": "the MCP server bootstrap itself",
    "mcp setup": "client-config generator for the MCP server itself",
    "serve": "HTTP/SSE service bootstrap",
    "models": "informational; models are enumerated in the generate tools' descriptions",
    "run": "chain-manifest runner — not yet ported",
    "character create": "character mutations — not yet ported",
    "character rm": "character mutations — not yet ported",
    "character show": "character mutations — not yet ported",
    "character voices": "character mutations — not yet ported",
    "data list images": "local catalog maintenance — not yet ported",
    "data list profiles": "local catalog maintenance — not yet ported",
    "data list videos": "local catalog maintenance — not yet ported",
    "data media": "local catalog maintenance — not yet ported",
    "data prune": "destructive local cleanup — deliberately CLI-only",
    "image batch": "batch pipelines — not yet ported",
    "image upload": "asset upload — covered indirectly by reference_images paths",
    "image upscale": "not yet ported",
    "video batch": "batch pipelines — not yet ported",
    "video chain": "chain pipeline — not yet ported",
    "movie run": "movie pipeline — not yet ported (skills-audit Task 7 backlog)",
    "movie template": "movie pipeline — not yet ported (skills-audit Task 7 backlog)",
    "scene create": "scene tooling — not yet ported",
    "scene show": "scene tooling — not yet ported",
    "tools run": "standalone tool run — exercised via the `tools` param on the generate tools",
}


def _cli_leaves() -> set[str]:
    """Every invokable CLI path: plain commands + invoke_without_command groups."""
    leaves: set[str] = set()

    def _walk(group: click.Group, prefix: str) -> None:
        for name, cmd in group.commands.items():
            path = f"{prefix}{name}"
            if isinstance(cmd, click.Group):
                if cmd.invoke_without_command:
                    leaves.add(path)
                _walk(cmd, f"{path} ")
            else:
                leaves.add(path)

    _walk(main, "")
    return leaves


def test_every_cli_leaf_has_an_mcp_decision() -> None:
    leaves = _cli_leaves()
    undecided = leaves - set(CLI_TO_MCP) - set(_MCP_EXEMPT)
    assert not undecided, (
        f"CLI commands with no MCP decision: {sorted(undecided)}. "
        "Add each to CLI_TO_MCP (and register the tool) or to _MCP_EXEMPT "
        "with a reason — see AGENTS.md 'MCP & CLI Schema Symmetry'."
    )


def test_no_leaf_is_both_mapped_and_exempt() -> None:
    both = set(CLI_TO_MCP) & set(_MCP_EXEMPT)
    assert not both, f"Ambiguous parity decision (mapped AND exempt): {sorted(both)}"


def test_no_stale_parity_entries() -> None:
    # A renamed/removed CLI command must not leave a dangling decision behind.
    leaves = _cli_leaves()
    stale = (set(CLI_TO_MCP) | set(_MCP_EXEMPT)) - leaves
    assert not stale, f"Parity entries for CLI commands that no longer exist: {sorted(stale)}"


def test_mapped_tools_are_registered(mcp_server: Any) -> None:
    registered = set(mcp_server._tool_manager._tools)
    missing = set(CLI_TO_MCP.values()) - registered
    assert not missing, f"CLI_TO_MCP references unregistered MCP tools: {sorted(missing)}"
