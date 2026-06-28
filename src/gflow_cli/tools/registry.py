"""In-process tool registry over packaged builtin + user ("My Tools") TOMLs."""

from __future__ import annotations

from functools import lru_cache

import structlog

from gflow_cli.config import get_settings
from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.loader import load_builtin_tools, load_user_tools
from gflow_cli.tools.spec import ToolSpec

log = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _registry() -> dict[str, ToolSpec]:
    # Built once, lazily, and memoized — mirrors ``config.get_settings``'s
    # ``@lru_cache`` discipline. ``reset_registry`` clears it for tests.
    #
    # Packaged builtins first, then user-authored "My Tools" TOMLs from
    # ``<GFLOW_CLI_HOME>/tools/*.toml`` layered on top: a user tool whose name
    # matches a builtin overrides it (user customization wins), logged so the
    # shadow is never silent. A malformed user TOML fails loud
    # (``ConfigurationError``) just like a malformed builtin.
    tools = load_builtin_tools()
    for name, spec in load_user_tools(get_settings().user_tools_dir()).items():
        if name in tools:
            log.warning("tool_user_override", name=name)
        tools[name] = spec
    return tools


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
