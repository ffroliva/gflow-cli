"""`gflow mcp setup` — write the gflow server entry into an MCP client config.

Non-destructive by construction (issue #475): existing config content is
merged, never replaced; a pre-existing file is backed up next to itself
before the first write; a corrupt config fails loud (ConfigurationError,
exit 11) and is never touched.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

from gflow_cli.errors import ConfigurationError

#: The server entry every target receives (vscode adds ``"type": "stdio"``).
SERVER_ENTRY: dict[str, Any] = {"command": "gflow", "args": ["mcp", "run"]}

#: Keys accepted as "already ours" — docs/MCP.md's manual blocks use
#: ``gflow-cli``; setup converges an existing entry instead of duplicating it.
_OUR_KEYS = ("gflow", "gflow-cli")

#: target -> (root key, needs stdio type marker)
_TARGET_SHAPE: dict[str, tuple[str, bool]] = {
    "claude-desktop": ("mcpServers", False),
    "cursor": ("mcpServers", False),
    "vscode": ("servers", True),
}


def _app_config_root() -> Path:
    """Per-OS root for application config files."""
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path.home() / ".config"


def config_path_for(target: str) -> Path:
    """The target client's MCP config file location on this OS."""
    if target == "claude-desktop":
        return _app_config_root() / "Claude" / "claude_desktop_config.json"
    if target == "cursor":
        # Cursor uses a home dotfile on every OS.
        return Path.home() / ".cursor" / "mcp.json"
    if target == "vscode":
        return _app_config_root() / "Code" / "User" / "mcp.json"
    msg = f"Unknown MCP setup target: {target!r}"
    raise ConfigurationError(msg)


def merge_server_entry(existing_text: str | None, *, target: str) -> str:
    """Merge the gflow server entry into ``existing_text`` (None = new file).

    Returns the new JSON document. Raises ConfigurationError on a corrupt or
    non-object config so the caller never clobbers a file it cannot parse.
    """
    root_key, stdio_type = _TARGET_SHAPE[target]
    if existing_text is None or not existing_text.strip():
        config: dict[str, Any] = {}
    else:
        try:
            parsed: Any = json.loads(existing_text)
        except ValueError as exc:
            msg = f"Existing config is not valid JSON: {exc}"
            raise ConfigurationError(msg) from exc
        if not isinstance(parsed, dict):
            msg = "Existing config root is not a JSON object."
            raise ConfigurationError(msg)
        config = cast("dict[str, Any]", parsed)

    servers = config.setdefault(root_key, {})
    if not isinstance(servers, dict):
        msg = f"Existing config {root_key!r} is not a JSON object."
        raise ConfigurationError(msg)
    servers = cast("dict[str, Any]", servers)

    entry: dict[str, Any] = {"type": "stdio", **SERVER_ENTRY} if stdio_type else dict(SERVER_ENTRY)
    key = next((k for k in _OUR_KEYS if k in servers), "gflow")
    servers[key] = entry
    return json.dumps(config, indent=2) + "\n"


def apply(target: str) -> Path:
    """Write/merge the server entry into the target's config; return its path.

    A pre-existing file is copied to ``<name>.gflow-backup`` before the write.
    """
    path = config_path_for(target)
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    merged = merge_server_entry(existing, target=target)
    if existing is not None:
        path.with_name(path.name + ".gflow-backup").write_text(existing, encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merged, encoding="utf-8")
    return path
