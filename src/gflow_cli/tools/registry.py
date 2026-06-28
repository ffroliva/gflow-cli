"""In-process tool registry over packaged builtin TOMLs."""

from __future__ import annotations

from functools import lru_cache

from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.loader import load_builtin_tools
from gflow_cli.tools.spec import ToolSpec


@lru_cache(maxsize=1)
def _registry() -> dict[str, ToolSpec]:
    # Built once, lazily, and memoized — mirrors ``config.get_settings``'s
    # ``@lru_cache`` discipline. ``reset_registry`` clears it for tests.
    return load_builtin_tools()


def reset_registry() -> None:
    _registry.cache_clear()


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
