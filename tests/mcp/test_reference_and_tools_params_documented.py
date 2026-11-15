"""The ``tools`` and ``reference_entities`` contracts must live in the schema agents read.

A tool-selection eval (Claude Code as host, scripts/dev/mcpjam, 2026-10-29) showed both
gaps: with ``tools`` typed only as "array of objects", the model could not pass
``creative-director`` until it called ``gflow_list_tools``; and in 4/5 runs it sent a
character entity id with no ``reference_entity_names``, which the flow.google.com
composer refuses before submit (``migrated_composer.py``). The rule sat only in a long
tool description and a docstring ``Args:`` block, which never reaches ``tools/list``.
"""

from __future__ import annotations

from typing import Any

import pytest

_GENERATE = ("gflow_generate_image", "gflow_generate_video")


def _param(mcp_server: Any, tool: str, name: str) -> dict[str, Any]:
    return mcp_server._tool_manager._tools[tool].parameters["properties"][name]


@pytest.mark.parametrize("tool", _GENERATE)
@pytest.mark.parametrize("phrase", ['"name"', '"options"', "creative-director", "gflow_list_tools"])
def test_tools_schema_states_the_item_shape(mcp_server: Any, tool: str, phrase: str) -> None:
    description = _param(mcp_server, tool, "tools").get("description", "")
    assert phrase in description, f"{tool}: `tools` schema description lacks {phrase!r}"


@pytest.mark.parametrize("tool", _GENERATE)
@pytest.mark.parametrize(
    "phrase",
    ["reference_entity_names", "same position", "flow.google.com", "gflow_character_list"],
)
def test_reference_entities_schema_requires_paired_names(
    mcp_server: Any, tool: str, phrase: str
) -> None:
    description = _param(mcp_server, tool, "reference_entities").get("description", "")
    assert phrase in description, f"{tool}: `reference_entities` description lacks {phrase!r}"
    assert "#875" not in description


def test_video_reference_entities_names_the_r2v_requirement(mcp_server: Any) -> None:
    description = _param(mcp_server, "gflow_generate_video", "reference_entities")["description"]
    assert "r2v" in description


@pytest.mark.parametrize("tool", _GENERATE)
def test_reference_entity_names_schema_states_the_pairing(mcp_server: Any, tool: str) -> None:
    description = _param(mcp_server, tool, "reference_entity_names").get("description", "")
    assert "reference_entities" in description and "flow.google.com" in description
