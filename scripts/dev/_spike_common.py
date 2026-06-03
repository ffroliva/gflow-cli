"""Shared bootstrap helpers for Phase-2 live-spike scripts (T-A/T-B/T-D).

Factored out so each spike imports one function instead of duplicating auth
/ sys.path / output-dir logic.

NOT imported by the gflow_cli package — scripts/dev/ only.

Usage example (inside a spike):

    from _spike_common import build_client, default_out_path, step

    async with build_client(profile_dir) as client:
        ...
    out = default_out_path("spike_char_editor_dom", ".json")
"""

from __future__ import annotations

import sys
import tempfile
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

# ---------------------------------------------------------------------------
# sys.path bootstrap — make this worktree's src/ importable regardless of
# whether the spike is invoked from the repo root or scripts/dev/.
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# ---------------------------------------------------------------------------
# gflow_cli imports (available only after sys.path bootstrap above).
# ---------------------------------------------------------------------------
from gflow_cli import auth as _auth_mod  # noqa: E402
from gflow_cli.api.client import FlowApiClient  # noqa: E402

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Spike output directory — gitignored, never written into tracked source.
# ---------------------------------------------------------------------------
# Primary location: <worktree>/scripts/dev/_spike_out/ (gitignored via
# scripts/dev/_spike_out/ entry in .gitignore).
# Fallback: OS temp dir (always safe).
_DEFAULT_OUT_DIR = _ROOT / "scripts" / "dev" / "_spike_out"


def default_out_path(stem: str, suffix: str = ".json") -> Path:
    """Return a timestamped path inside the spike output dir (or OS tmp)."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    try:
        _DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
        return _DEFAULT_OUT_DIR / f"{stem}_{ts}{suffix}"
    except OSError:
        return Path(tempfile.gettempdir()) / f"{stem}_{ts}{suffix}"


# ---------------------------------------------------------------------------
# Profile validation — real-browser auth is mandatory (memory: real-browser-
# auth-mandatory). Fail fast if the profile doesn't exist.
# ---------------------------------------------------------------------------


def resolve_profile_dir(profile: str) -> Path:
    """Return the Playwright profile dir for *profile*; exit with code 2 if absent."""
    profile_dir = _auth_mod.profile_dir(profile)
    if not profile_dir.exists():
        print(
            f"[spike] ERROR: no session for profile '{profile}'. "
            "Run `gflow auth login --profile <name>` first.",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(2)
    return profile_dir


# ---------------------------------------------------------------------------
# Client context manager — thin wrapper so spikes can write:
#     async with build_client(profile_dir) as client: ...
# ---------------------------------------------------------------------------


@asynccontextmanager
async def build_client(
    profile_dir: Path,
    *,
    headless: bool = False,
) -> AsyncGenerator[FlowApiClient, None]:
    """Async context manager that yields an authenticated FlowApiClient.

    headless=False is the safe default — reCAPTCHA scores headed sessions
    higher (memory: real-browser-auth-mandatory).
    """
    async with FlowApiClient(profile_dir=profile_dir, headless=headless) as client:
        yield client


# ---------------------------------------------------------------------------
# Pretty step printer
# ---------------------------------------------------------------------------


def step(tag: str, msg: str, *, prefix: str = "spike") -> None:
    print(f"[{prefix}] {tag}  {msg}", flush=True)
