"""XDG-aware default paths for gflow-cli, via `platformdirs`.

Single source of truth for where things live on disk:

  * **profiles + config** under `<user_data_dir>/gflow-cli/`
    - Windows: `%LOCALAPPDATA%\\gflow-cli\\`
    - macOS:   `~/Library/Application Support/gflow-cli/`
    - Linux:   `$XDG_DATA_HOME/gflow-cli/` (typically `~/.local/share/gflow-cli/`)

  * **downloads** under `<user_downloads_dir>/gflow-cli/`
    - Windows: `%USERPROFILE%\\Downloads\\gflow-cli\\`
    - macOS:   `~/Downloads/gflow-cli/`
    - Linux:   `$XDG_DOWNLOAD_DIR/gflow-cli/` (typically `~/Downloads/gflow-cli/`)

These are the defaults — overridable per-process via env vars
`GFLOW_CLI_HOME` and `GFLOW_CLI_OUTPUT_DIR`. Resolution lives in
`gflow_cli.config.Settings`.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from platformdirs import user_data_dir, user_downloads_dir

# Allow only alphanumerics, hyphens, and underscores up to 128 chars.
# Prevents path traversal via API-returned job IDs.
_SAFE_ID_RE = re.compile(r"^[\w\-]{1,128}$")

APP_NAME = "gflow-cli"
APP_AUTHOR = "ffroliva"  # Windows-only; Linux/macOS ignore this.


def default_home() -> Path:
    """Default `GFLOW_CLI_HOME` — profiles + config.toml live here."""
    return Path(user_data_dir(APP_NAME, APP_AUTHOR, ensure_exists=False))


def default_output_dir() -> Path:
    """Default `GFLOW_CLI_OUTPUT_DIR` — generated assets land here."""
    return Path(user_downloads_dir()) / APP_NAME


def profile_subdir(home: Path, name: str) -> Path:
    """Where profile <name> lives under <home>."""
    return home / f"profile_{name}"


def config_file(home: Path) -> Path:
    """Where the per-user config TOML lives."""
    return home / "config.toml"


def _validate_job_id(job_id: str) -> str:
    if not _SAFE_ID_RE.match(job_id):
        raise ValueError(f"Unsafe job_id returned by API: {job_id!r}")
    return job_id


def resolve_batch_output_dir(
    *,
    cli_override: Path | None,
    config_value: str | None = None,
    output_root: Path,
    kind: str = "images",
) -> Path:
    """CLI flag > config value > default (``<output_root>/<kind>/<YYYY-MM-DD>/``)."""
    if cli_override is not None:
        return cli_override
    if config_value is not None:
        return Path(config_value)
    return output_root / kind / date.today().isoformat()


def video_output_path(
    output_dir: Path,
    *,
    job_id: str,
    on: date | None = None,
) -> Path:
    """`<output_dir>/videos/<YYYY-MM-DD>/<job_id>.mp4`."""
    on = on or date.today()
    return output_dir / "videos" / on.isoformat() / f"{_validate_job_id(job_id)}.mp4"


def image_output_path(
    output_dir: Path,
    *,
    job_id: str,
    index: int = 1,
    on: date | None = None,
) -> Path:
    """`<output_dir>/images/<YYYY-MM-DD>/<job_id>_<index>.png`."""
    on = on or date.today()
    return output_dir / "images" / on.isoformat() / f"{_validate_job_id(job_id)}_{index}.png"
