from __future__ import annotations

from gflow_cli.tools.expander import ExpansionResult, PromptExpander
from gflow_cli.tools.registry import get_tool, reset_registry
from gflow_cli.tools.runtime import apply_tool, build_instruction


def setup_function() -> None:
    reset_registry()


def test_build_instruction_appends_domain() -> None:
    cfg = get_tool("creative-director").config
    instr = build_instruction(cfg, "cinema")
    assert "cinema" in instr.lower() or "ARRI" in instr  # domain vocab injected
    base = build_instruction(cfg, None)
    assert len(instr) > len(base)


def test_apply_tool_strips_banned_from_output() -> None:
    spec = get_tool("creative-director")

    def transport(url, payload, timeout):  # noqa: ANN001
        return {"candidates": [{"content": {"parts": [{"text": "a hyperrealistic 8k cat scene"}]}}]}

    instruction = build_instruction(spec.config, None)
    expander = PromptExpander("key", transport=transport, system_instruction=instruction)
    result = apply_tool(spec, "cat", {}, expander=expander)
    assert isinstance(result, ExpansionResult)
    assert result.was_expanded
    assert "hyperrealistic" not in result.expanded.lower()
    assert "8k" not in result.expanded.lower()


def test_apply_tool_uses_per_tool_banned_keywords() -> None:
    """apply_tool must strip from spec.config.banned_keywords, not the global list.

    A spec that only bans "photorealism" must strip "photorealism" but leave
    "8k" intact (8k is in the global BANNED_KEYWORDS but not in this spec).
    """
    from gflow_cli.tools.spec import ToolConfig, ToolSpec

    spec = ToolSpec(
        name="test-tool",
        title="Test Tool",
        description="d",
        category="both",
        version="1",
        config=ToolConfig(
            system_template="expand: ",
            banned_keywords=("photorealism",),
        ),
    )

    def transport(url, payload, timeout):  # noqa: ANN001
        return {"candidates": [{"content": {"parts": [{"text": "a photorealism 8k landscape"}]}}]}

    expander = PromptExpander("key", transport=transport, system_instruction="expand: ")
    result = apply_tool(spec, "landscape", {}, expander=expander)
    assert result.was_expanded
    # "photorealism" is in spec.config.banned_keywords → must be stripped
    assert "photorealism" not in result.expanded.lower()
    # "8k" is NOT in spec.config.banned_keywords → must survive
    assert "8k" in result.expanded.lower()
