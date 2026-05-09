"""Profile inventory + default-profile persistence.

Single source of truth for: which Google sessions exist, which one to use
when no `--profile` flag is given, and how to set/clear that default.

Storage layout under $FLOW_CLI_HOME (default: see flow_cli.auth.default_profile_root):
    ./profile_<name>/        ← Chromium persistent context per profile
    ./config.toml            ← `default_profile = "<name>"`

Resolution precedence (highest first):
    1. Explicit CLI --profile flag
    2. FLOW_CLI_PROFILE env var
    3. config.toml's default_profile
    4. Auto-select if exactly one profile exists
    5. Raise NoDefaultProfileError with the list of available profiles
"""
from __future__ import annotations

import os
import shutil
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flow_cli.auth import default_profile_root, profile_dir, status

CONFIG_FILENAME = "config.toml"
PROFILE_DIR_PREFIX = "profile_"


@dataclass(frozen=True)
class ProfileMeta:
    """Snapshot of one profile on disk."""
    name: str
    profile_dir: Path
    cookies_present: bool
    last_used_at: Optional[datetime]
    is_default: bool


class NoDefaultProfileError(RuntimeError):
    """Raised when profile resolution can't pick exactly one profile."""

    def __init__(self, available: list[str]):
        self.available = available
        msg = (
            "Cannot pick a default profile.\n"
            f"Available: {', '.join(available) if available else '(none)'}\n"
            "Run `gflow auth use <name>`, set FLOW_CLI_PROFILE, or pass --profile."
        )
        super().__init__(msg)


class NoProfilesError(RuntimeError):
    """Raised when no profiles exist at all (caller should trigger login)."""


def config_path() -> Path:
    """Path to the user-level config.toml (under $FLOW_CLI_HOME)."""
    return default_profile_root() / CONFIG_FILENAME


def list_profiles() -> list[ProfileMeta]:
    """Discover every `profile_*` directory under $FLOW_CLI_HOME.

    Returns them sorted by name. Each entry includes whether it has a Chromium
    cookies file (a coarse "has session" probe — actual validity is only known
    by hitting the live API).
    """
    root = default_profile_root()
    if not root.exists():
        return []
    default_name = _read_default_profile_name()
    out: list[ProfileMeta] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or not entry.name.startswith(PROFILE_DIR_PREFIX):
            continue
        name = entry.name[len(PROFILE_DIR_PREFIX) :]
        s = status(name)
        last_used = _last_modified(entry)
        out.append(
            ProfileMeta(
                name=name,
                profile_dir=entry,
                cookies_present=bool(s["cookies_present"]),
                last_used_at=last_used,
                is_default=(name == default_name),
            )
        )
    return out


def has_any_profiles() -> bool:
    return len(list_profiles()) > 0


def get_default_profile() -> Optional[str]:
    """Resolved default profile name, or None if no rule applies.

    Order:
      1. config.toml `default_profile`
      2. Auto: if exactly one profile exists, that one is the de-facto default.
      3. None.
    """
    explicit = _read_default_profile_name()
    if explicit:
        return explicit
    profiles = list_profiles()
    if len(profiles) == 1:
        return profiles[0].name
    return None


def set_default_profile(name: str) -> Path:
    """Persist `name` as the default profile in config.toml. Returns config path.

    Validates the profile dir exists; raises FileNotFoundError otherwise so
    typos don't silently set an unusable default.
    """
    pdir = profile_dir(name)
    if not pdir.exists():
        raise FileNotFoundError(
            f"Profile dir not found: {pdir}\n"
            f"Run `gflow auth login --profile {name}` first."
        )
    cfg = config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    # Single-key file — keep it minimal so future keys can be added cleanly.
    existing = _load_config()
    existing["default_profile"] = name
    cfg.write_text(_dump_config(existing), encoding="utf-8")
    return cfg


def clear_default_profile() -> None:
    """Remove the default_profile key. Other config keys (future) preserved."""
    cfg = config_path()
    if not cfg.exists():
        return
    existing = _load_config()
    existing.pop("default_profile", None)
    if existing:
        cfg.write_text(_dump_config(existing), encoding="utf-8")
    else:
        cfg.unlink()


def delete_profile(name: str) -> Path:
    """Hard-delete the profile dir. Clears it as default if it was set."""
    pdir = profile_dir(name)
    if not pdir.exists():
        raise FileNotFoundError(f"Profile dir not found: {pdir}")
    shutil.rmtree(pdir, ignore_errors=False)
    if _read_default_profile_name() == name:
        clear_default_profile()
    return pdir


def resolve_profile(cli_flag: Optional[str]) -> str:
    """Apply the full precedence chain. Raises if no profile can be picked."""
    if cli_flag:
        return cli_flag
    env = os.environ.get("FLOW_CLI_PROFILE")
    if env:
        return env
    default = get_default_profile()
    if default:
        return default
    profiles = list_profiles()
    if not profiles:
        raise NoProfilesError(
            "No profiles found. Run `gflow auth login` to create one."
        )
    raise NoDefaultProfileError([p.name for p in profiles])


# --- internals --------------------------------------------------------------


def _read_default_profile_name() -> Optional[str]:
    cfg = _load_config()
    val = cfg.get("default_profile")
    return val if isinstance(val, str) and val else None


def _load_config() -> dict[str, object]:
    cfg = config_path()
    if not cfg.exists():
        return {}
    try:
        with cfg.open("rb") as f:
            return dict(tomllib.load(f))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def _dump_config(data: dict[str, object]) -> str:
    """Tiny TOML serialiser — only handles flat string keys (sufficient for now).

    Avoids adding `tomli-w` as a dependency. Switch to it if config grows
    nested tables or non-string values.
    """
    lines: list[str] = []
    for key, value in sorted(data.items()):
        if not isinstance(value, str):
            raise TypeError(
                f"Only string values are supported in config.toml; "
                f"got {type(value).__name__} for key {key!r}."
            )
        # Escape backslashes and double-quotes in TOML basic string.
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key} = "{escaped}"')
    return "\n".join(lines) + "\n"


def _last_modified(path: Path) -> Optional[datetime]:
    try:
        # Best-effort: latest mtime among the cookies file or the dir itself.
        candidates = [path]
        for sub in (path / "Default" / "Cookies", path / "Cookies"):
            if sub.exists():
                candidates.append(sub)
        ts = max(p.stat().st_mtime for p in candidates)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except OSError:
        return None
