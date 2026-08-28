"""Profile inventory + default-profile persistence.

Single source of truth for: which Google sessions exist, which one to use
when no `--profile` flag is given, and how to set/clear that default.

Storage layout under $GFLOW_CLI_HOME (default: see gflow_cli.auth.default_profile_root):
    ./profile_<name>/        ← Chromium persistent context per profile
    ./config.toml            ← `default_profile = "<name>"`

Resolution precedence (highest first):
    1. Explicit CLI --profile flag
    2. GFLOW_CLI_PROFILE env var
    3. config.toml's default_profile
    4. Auto-select if exactly one profile exists
    5. Raise NoDefaultProfileError with the list of available profiles
"""

from __future__ import annotations

import os
import re
import shutil
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from gflow_cli.auth import default_profile_root, profile_dir, status

if TYPE_CHECKING:
    from pathlib import Path

CONFIG_FILENAME = "config.toml"
PROFILE_DIR_PREFIX = "profile_"
ACCOUNT_FILE = ".gflow_account"
LOCALE_FILE = ".gflow_locale"

# The probe cannot tell "Flow does not redirect this account" from "the settle
# timed out this once" — `await_url_settled` returns None for both. Committing
# straight to NOT_REDIRECTED on the first no-redirect observation therefore lets a
# single slow network permanently disable the settle, silently restoring #580's
# post-goto race. So a no-redirect observation is PROVISIONAL until a second run
# agrees; the poisoned state cannot arise, and the cost of a false timeout is one
# extra probe rather than a permanent defect.
PROVISIONAL: Final = "?"
NOT_REDIRECTED: Final = ""

# A locale SEGMENT as Flow serves it under /fx/{segment}/tools/flow — bare
# lowercase ISO-639, never a full BCP-47 tag. Enforced on READ because this value
# is interpolated into a URL path: a hand-edited or corrupted file must degrade
# to "probe again", not build https://labs.google/fx/../../etc/passwd/tools/flow.
_LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2,3}$")

# Mirrors gflow_cli.paths._SAFE_ID_RE — alphanumerics, hyphens, underscores, ≤128 chars.
_SAFE_PROFILE_NAME_RE = re.compile(r"^[\w\-]{1,128}$")


@dataclass(frozen=True)
class ProfileMeta:
    """Snapshot of one profile on disk."""

    name: str
    profile_dir: Path
    cookies_present: bool
    last_used_at: datetime | None
    is_default: bool
    google_account: str | None = None


class NoDefaultProfileError(RuntimeError):
    """Raised when profile resolution can't pick exactly one profile."""

    def __init__(self, available: list[str]):
        self.available = available
        msg = (
            "Cannot pick a default profile.\n"
            f"Available: {', '.join(available) if available else '(none)'}\n"
            "Run `gflow auth use <name>`, set GFLOW_CLI_PROFILE, or pass --profile."
        )
        super().__init__(msg)


class NoProfilesError(RuntimeError):
    """Raised when no profiles exist at all (caller should trigger login)."""


def config_path() -> Path:
    """Path to the user-level config.toml (under $GFLOW_CLI_HOME)."""
    return default_profile_root() / CONFIG_FILENAME


def list_profiles() -> list[ProfileMeta]:
    """Discover every `profile_*` directory under $GFLOW_CLI_HOME.

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
        google_account = _read_account_file(entry)
        out.append(
            ProfileMeta(
                name=name,
                profile_dir=entry,
                cookies_present=bool(s["cookies_present"]),
                last_used_at=last_used,
                is_default=(name == default_name),
                google_account=google_account,
            ),
        )
    return out


def has_any_profiles() -> bool:
    return len(list_profiles()) > 0


def get_default_profile() -> str | None:
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
        msg = f"Profile dir not found: {pdir}\nRun `gflow auth login --profile {name}` first."
        raise FileNotFoundError(
            msg,
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


def rename_profile(old_name: str, new_name: str) -> Path:
    """Rename a profile directory. Returns the new profile dir path.

    Updates config.toml when old_name was the default. Raises FileNotFoundError
    if old_name doesn't exist; raises FileExistsError if new_name already exists.
    Raises ValueError if new_name contains path-traversal characters.
    """
    if not _SAFE_PROFILE_NAME_RE.match(new_name):
        msg = (
            f"Profile name {new_name!r} contains invalid characters. "
            "Use only letters, digits, hyphens, and underscores (max 128 chars)."
        )
        raise ValueError(
            msg,
        )
    old_dir = profile_dir(old_name)
    new_dir = profile_dir(new_name)
    if not old_dir.exists():
        msg = f"Profile dir not found: {old_dir}"
        raise FileNotFoundError(msg)
    if new_dir.exists():
        msg = (
            f"Profile '{new_name}' already exists: {new_dir}. "
            "Choose a different name or delete it first."
        )
        raise FileExistsError(
            msg,
        )
    old_dir.rename(new_dir)
    if _read_default_profile_name() == old_name:
        existing = _load_config()
        existing["default_profile"] = new_name
        config_path().write_text(_dump_config(existing), encoding="utf-8")
    return new_dir


def delete_profile(name: str) -> Path:
    """Hard-delete the profile dir. Clears it as default if it was set."""
    pdir = profile_dir(name)
    if not pdir.exists():
        msg = f"Profile dir not found: {pdir}"
        raise FileNotFoundError(msg)
    shutil.rmtree(pdir, ignore_errors=False)
    if _read_default_profile_name() == name:
        clear_default_profile()
    return pdir


def resolve_profile(cli_flag: str | None) -> str:
    """Apply the full precedence chain. Raises if no profile can be picked."""
    if cli_flag:
        return cli_flag
    env = os.environ.get("GFLOW_CLI_PROFILE")
    if env:
        return env
    default = get_default_profile()
    if default:
        return default
    profiles = list_profiles()
    if not profiles:
        msg = "No profiles found. Run `gflow auth login` to create one."
        raise NoProfilesError(msg)
    raise NoDefaultProfileError([p.name for p in profiles])


# --- internals --------------------------------------------------------------


def _read_default_profile_name() -> str | None:
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
            msg = (
                f"Only string values are supported in config.toml; "
                f"got {type(value).__name__} for key {key!r}."
            )
            raise TypeError(
                msg,
            )
        # Escape backslashes and double-quotes in TOML basic string.
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key} = "{escaped}"')
    return "\n".join(lines) + "\n"


def read_account_locale(profile_path: Path) -> str | None:
    """Cached account-locale probe outcome for a profile dir (#587). FOUR states.

    ``None``               — never probed; probe, then record the outcome.
    :data:`PROVISIONAL`    — ONE no-redirect observation; probe again to confirm.
    :data:`NOT_REDIRECTED` — two agreed; skip the settle, there is nothing to wait for.
    ``"pt"``               — the account's locale segment.

    Anything else reads as ``None`` so a corrupt file self-heals: the value is
    interpolated into a URL path, and ``ValueError`` covers the undecodable-bytes
    case that would otherwise crash every listing command.
    """
    try:
        raw = (profile_path / LOCALE_FILE).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    if not raw:
        return NOT_REDIRECTED
    if raw == PROVISIONAL:
        return PROVISIONAL
    return raw if _LOCALE_SEGMENT_RE.match(raw) else None


def write_account_locale(profile_path: Path, value: str | None) -> None:
    """Persist a probe outcome verbatim; ``None`` means :data:`NOT_REDIRECTED`.

    Best-effort: a failed cache write costs one extra probe, never a run.
    """
    try:
        (profile_path / LOCALE_FILE).write_text(value or NOT_REDIRECTED, encoding="utf-8")
    except OSError:
        return


def next_locale_state(cached: str | None, observed: str | None) -> str:
    """Fold a fresh probe outcome into the cached one (#587).

    A no-redirect observation only reaches :data:`NOT_REDIRECTED` by agreeing with
    a previous one, which is what makes a single transient settle timeout cost an
    extra probe instead of permanently disabling the settle. A segment is always
    taken at face value — Flow stating a locale is not ambiguous the way silence is.
    """
    if observed is not None:
        return observed
    # Silence corroborates an earlier silence; it never demotes a committed state
    # (unreachable from the client, which returns early on NOT_REDIRECTED, but the
    # function must be right on its own terms).
    return NOT_REDIRECTED if cached in (PROVISIONAL, NOT_REDIRECTED) else PROVISIONAL


def account_locale_for(profile_name: str) -> str | None:
    """Cached locale SEGMENT for a named profile, or ``None`` when unknown (#587).

    The offline entry point: catalog listings know a profile name, not a dir, and
    have no browser to ask. Only a real segment is a locale — the bookkeeping
    states must never leak into a URL, and :data:`PROVISIONAL` is truthy, so this
    checks the shape rather than truthiness. A catalog row can outlive its profile
    dir, so a missing dir is a normal ``None``.
    """
    cached = read_account_locale(profile_dir(profile_name))
    if cached is None or not _LOCALE_SEGMENT_RE.match(cached):
        return None
    return cached


def _read_account_file(profile_path: Path) -> str | None:
    """Read the Google account email from the profile's .gflow_account file."""
    account_file = profile_path / ACCOUNT_FILE
    try:
        return account_file.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _last_modified(path: Path) -> datetime | None:
    try:
        # Best-effort: latest mtime among the cookies file or the dir itself.
        candidates = [path]
        for sub in (path / "Default" / "Cookies", path / "Cookies"):
            if sub.exists():
                candidates.append(sub)
        ts = max(p.stat().st_mtime for p in candidates)
        return datetime.fromtimestamp(ts, tz=UTC)
    except OSError:
        return None
