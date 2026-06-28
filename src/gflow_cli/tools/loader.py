"""Load + validate packaged builtin tool TOMLs and user-authored "My Tools" TOMLs."""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from gflow_cli.errors import ConfigurationError
from gflow_cli.tools.spec import ToolSpec

_BUILTIN_PACKAGE = "gflow_cli.tools.builtin"


def _parse(name: str, text: str) -> dict[str, object]:
    # Wrap BOTH failure modes as ConfigurationError so spec §4.2 holds
    # ("Invalid TOML → ConfigurationError") — syntactic (TOMLDecodeError) and
    # schema (ValidationError, in _validate). (council D2 nice-to-have)
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(detail=f"malformed TOML in tool {name!r}: {exc}") from exc


def _validate(name: str, data: dict[str, object]) -> ToolSpec:
    try:
        return ToolSpec.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(detail=f"invalid tool definition {name!r}: {exc}") from exc


def load_builtin_tools() -> dict[str, ToolSpec]:
    tools: dict[str, ToolSpec] = {}
    root = resources.files(_BUILTIN_PACKAGE)
    # Sorted for deterministic load order (stable across platforms / packagers).
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".toml"):
            label = Path(entry.name).stem  # strip .toml for error labels
            spec = _validate(label, _parse(label, entry.read_text(encoding="utf-8")))
            tools[spec.name] = spec
    return tools


def _load_dir(directory: Path) -> dict[str, ToolSpec]:
    tools: dict[str, ToolSpec] = {}
    for path in sorted(directory.glob("*.toml")):
        spec = _validate(path.stem, _parse(path.stem, path.read_text(encoding="utf-8")))
        tools[spec.name] = spec
    return tools


def load_user_tools(config_dir: Path) -> dict[str, ToolSpec]:
    """Scan a user config dir for "My Tools" tool TOMLs.

    Wired into the registry (``registry._registry`` layers these over the
    packaged builtins; a same-named user tool overrides a builtin). Returns
    ``{}`` when the dir is absent. A malformed TOML fails loud
    (``ConfigurationError``), like a malformed builtin.
    """
    if not config_dir.exists():
        return {}
    return _load_dir(config_dir)
