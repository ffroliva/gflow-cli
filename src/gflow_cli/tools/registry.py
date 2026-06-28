"""In-process tool registry over packaged builtin TOMLs."""

from __future__ import annotations

from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.loader import load_builtin_tools
from gflow_cli.tools.spec import ToolSpec

_REGISTRY: dict[str, ToolSpec] | None = None


def _registry() -> dict[str, ToolSpec]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_builtin_tools()
    return _REGISTRY


def reset_registry() -> None:
    global _REGISTRY
    _REGISTRY = None


def tool_names() -> tuple[str, ...]:
    return tuple(sorted(_registry()))


def iter_tools() -> tuple[ToolSpec, ...]:
    return tuple(_registry()[name] for name in tool_names())


def get_tool(name: str) -> ToolSpec:
    reg = _registry()
    if name not in reg:
        valid = ", ".join(sorted(reg))
        raise ConfigurationError(detail=f"unknown tool {name!r}. Available: {valid}")
    return reg[name]
