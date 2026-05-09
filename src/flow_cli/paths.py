"""XDG-aware default paths for flow-cli, via `platformdirs`.

Single source of truth for where things live on disk:

  * **profiles + config** under `<user_data_dir>/flow-cli/`
    - Windows: `%LOCALAPPDATA%\\flow-cli\\`
    - macOS:   `~/Library/Application Support/flow-cli/`
    - Linux:   `$XDG_DATA_HOME/flow-cli/` (typically `~/.local/share/flow-cli/`)

  * **downloads** under `<user_downloads_dir>/flow-cli/`
    - Windows: `%USERPROFILE%\\Downloads\\flow-cli\\`
    - macOS:   `~/Downloads/flow-cli/`
    - Linux:   `$XDG_DOWNLOAD_DIR/flow-cli/` (typically `~/Downloads/flow-cli/`)

These are the defaults — overridable per-process via env vars
`FLOW_CLI_HOME` and `FLOW_CLI_OUTPUT_DIR`. Resolution lives in
`flow_cli.config.Settings`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from platformdirs import user_data_dir, user_downloads_dir

APP_NAME = "flow-cli"
APP_AUTHOR = "ffroliva"  # Windows-only; Linux/macOS ignore this.


def default_home() -> Path:
    """Default `FLOW_CLI_HOME` — profiles + config.toml live here."""
    return Path(user_data_dir(APP_NAME, APP_AUTHOR, ensure_exists=False))


def default_output_dir() -> Path:
    """Default `FLOW_CLI_OUTPUT_DIR` — generated assets land here."""
    return Path(user_downloads_dir()) / APP_NAME


def profile_subdir(home: Path, name: str) -> Path:
    """Where profile <name> lives under <home>."""
    return home / f"profile_{name}"


def config_file(home: Path) -> Path:
    """Where the per-user config TOML lives."""
    return home / "config.toml"


def video_output_path(
    output_dir: Path,
    *,
    job_id: str,
    on: date | None = None,
) -> Path:
    """`<output_dir>/videos/<YYYY-MM-DD>/<job_id>.mp4`."""
    on = on or date.today()
    return output_dir / "videos" / on.isoformat() / f"{job_id}.mp4"


def image_output_path(
    output_dir: Path,
    *,
    job_id: str,
    index: int = 1,
    on: date | None = None,
) -> Path:
    """`<output_dir>/images/<YYYY-MM-DD>/<job_id>_<index>.png`."""
    on = on or date.today()
    return output_dir / "images" / on.isoformat() / f"{job_id}_{index}.png"
