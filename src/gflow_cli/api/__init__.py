"""Low-level REST client for Flow's private aisandbox-pa API."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gflow_cli.api.client import FlowApiClient, FlowApiError
    from gflow_cli.api.dto import AssetInfo, ProjectInfo
    from gflow_cli.api.video import Aspect, GenerateVideoRequest, Mode, Tier

__all__ = [
    "Aspect",
    "AssetInfo",
    "FlowApiClient",
    "FlowApiError",
    "GenerateVideoRequest",
    "Mode",
    "ProjectInfo",
    "Tier",
]

_VIDEO = "gflow_cli.api.video"

_DEFERRED_IMPORTS: dict[str, tuple[str, str]] = {
    "FlowApiClient": ("gflow_cli.api.client", "FlowApiClient"),
    "FlowApiError": ("gflow_cli.api.client", "FlowApiError"),
    "AssetInfo": ("gflow_cli.api.dto", "AssetInfo"),
    "ProjectInfo": ("gflow_cli.api.dto", "ProjectInfo"),
    "Aspect": (_VIDEO, "Aspect"),
    "GenerateVideoRequest": (_VIDEO, "GenerateVideoRequest"),
    "Mode": (_VIDEO, "Mode"),
    "Tier": (_VIDEO, "Tier"),
}


def __getattr__(name: str) -> Any:
    if name in _DEFERRED_IMPORTS:
        module_name, attr_name = _DEFERRED_IMPORTS[name]
        module = importlib.import_module(module_name)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
