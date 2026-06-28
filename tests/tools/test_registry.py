from __future__ import annotations

import pytest

from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.registry import get_tool, iter_tools, reset_registry, tool_names


def setup_function() -> None:
    reset_registry()


def test_creative_director_registered() -> None:
    assert "creative-director" in tool_names()
    assert get_tool("creative-director").title == "Creative Director"
    assert [t.name for t in iter_tools()] == sorted(tool_names())


def test_unknown_tool_raises_with_valid_names() -> None:
    with pytest.raises(ConfigurationError) as exc:
        get_tool("nope")
    assert "creative-director" in str(exc.value)
