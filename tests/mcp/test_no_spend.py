# SPDX-License-Identifier: MIT
"""#496: --no-spend registration-time gating of credit-spending tools.

Under no-spend the generate tools are never registered — invisible in
``tools/list`` beats refused at call time: no wasted calls, no refusal path
for prompt injection to probe, no reliance on the model honoring an error.
Both generate tools are gated: image generation is only *empirically* free,
and no-spend must be a hard guarantee.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def registered_server():
    from gflow_cli.mcp import server as server_mod

    server_mod._register_surfaces()
    yield server_mod
    # Re-add anything the policy removed so other tests see the full registry.
    from gflow_cli.mcp import tools

    mgr = server_mod.server._tool_manager
    for name in server_mod._SPEND_TOOLS:
        if mgr.get_tool(name) is None:
            server_mod.server.add_tool(getattr(tools, name))


def _tool_names(server_mod) -> set[str]:
    return set(server_mod.server._tool_manager._tools.keys())


def test_default_mode_keeps_generate_tools(
    registered_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GFLOW_MCP_NO_SPEND", raising=False)
    registered_server._apply_no_spend_policy()
    names = _tool_names(registered_server)
    assert "gflow_generate_image" in names
    assert "gflow_generate_video" in names


def test_no_spend_removes_both_generate_tools(
    registered_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GFLOW_MCP_NO_SPEND", "1")
    registered_server._apply_no_spend_policy()
    names = _tool_names(registered_server)
    assert "gflow_generate_image" not in names
    assert "gflow_generate_video" not in names
    # Read-only surfaces stay available.
    assert "gflow_list_projects" in names
    assert "gflow_list_tools" in names


@pytest.mark.parametrize("value", ["0", "false", ""])
def test_falsy_env_values_do_not_gate(
    registered_server, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("GFLOW_MCP_NO_SPEND", value)
    registered_server._apply_no_spend_policy()
    assert "gflow_generate_image" in _tool_names(registered_server)
