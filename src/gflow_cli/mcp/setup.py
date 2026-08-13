"""`gflow mcp setup` — write the gflow server entry into an MCP client config.

Non-destructive by construction (issue #475): existing config content is
merged, never replaced; a pre-existing gflow/gflow-cli entry — however the
user wrote it (e.g. docs/MCP.md's local-clone `uv --directory` variant) — is
left entirely untouched; a pre-existing file is backed up once (the first
backup stays pristine); writes are atomic (tmp + os.replace); a corrupt
config fails loud (ConfigurationError, exit 11) and is never touched.
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

#: Keys treated as "already ours" — docs/MCP.md's manual blocks use
#: ``gflow-cli``; an existing entry under either key is preserved as-is.
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
        appdata = os.environ.get("APPDATA")
        if not appdata:
            msg = "APPDATA is not set — cannot locate the client config directory."
            raise ConfigurationError(msg)
        return Path(appdata)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


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


def merge_server_entry(existing_text: str | None, *, target: str) -> str | None:
    """Merge the gflow server entry into ``existing_text`` (None = new file).

    Returns the new JSON document, or ``None`` when the config already has a
    gflow/gflow-cli entry — the user's own entry (command, args, env) is
    never rewritten. Raises ConfigurationError on a corrupt or non-object
    config so the caller never clobbers a file it cannot parse.
    """
    root_key, stdio_type = _TARGET_SHAPE[target]
    if existing_text is None or not existing_text.strip():
        config: dict[str, Any] = {}
    else:
        try:
            parsed: Any = json.loads(existing_text)
        except ValueError as exc:
            hint = (
                " VS Code configs may contain comments/trailing commas (JSONC), "
                "which gflow cannot safely edit — add the server entry manually "
                "(see docs/MCP.md § Setup Instructions)."
                if target == "vscode"
                else ""
            )
            msg = f"Existing config is not valid JSON: {exc}.{hint}"
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

    if any(k in servers for k in _OUR_KEYS):
        return None
    entry: dict[str, Any] = {"type": "stdio", **SERVER_ENTRY} if stdio_type else dict(SERVER_ENTRY)
    servers["gflow"] = entry
    return json.dumps(config, indent=2) + "\n"


def apply(target: str) -> tuple[Path, bool]:
    """Ensure the server entry exists in the target's config.

    Returns ``(path, changed)`` — ``changed`` is False when an entry already
    existed and nothing was written. On the first write over a pre-existing
    file, a one-time pristine copy is kept as ``<name>.gflow-backup`` (never
    overwritten by later runs). The write itself is atomic (tmp +
    ``os.replace``) so a crash can never truncate the client's config.
    """
    path = config_path_for(target)
    existing: str | None = None
    if path.exists():
        try:
            # utf-8-sig: tolerate the BOM PowerShell's UTF8 encoding writes.
            existing = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            msg = f"Existing config is not UTF-8 encoded: {exc}"
            raise ConfigurationError(msg) from exc
    merged = merge_server_entry(existing, target=target)
    if merged is None:
        return path, False
    if existing is not None:
        backup = path.with_name(path.name + ".gflow-backup")
        if not backup.exists():  # the FIRST backup is the pristine one — keep it
            backup.write_text(existing, encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".gflow-tmp")
    tmp.write_text(merged, encoding="utf-8")
    os.replace(tmp, path)
    return path, True
