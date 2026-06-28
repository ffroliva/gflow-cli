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
