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


def get_cookies_path(pdir: Path) -> Path:
    for candidate in (
        pdir / "Default" / "Network" / "Cookies",  # Chrome 130+ (new location)
        pdir / "Default" / "Cookies",  # Chrome < 130 / legacy
        pdir / "Cookies",  # Playwright bundled Chromium
    ):
        if candidate.exists():
            return candidate
    raise FileNotFoundError


def config_file(home: Path) -> Path:
    """Where the per-user config TOML lives."""
    return home / "config.toml"


def database_path(home: Path) -> Path:
    """SQLite DB path under GFLOW_CLI_HOME."""
    return home / "gflow.db"


def validate_job_id(job_id: str) -> str:
    if not _SAFE_ID_RE.match(job_id):
        msg = f"Unsafe job_id returned by API: {job_id!r}"
        raise ValueError(msg)
    return job_id


def resolve_batch_output_dir(
    *,
    cli_override: Path | None,
    config_value: str | None = None,
    output_root: Path,
    kind: str = "images",
) -> Path:
    """CLI flag > config value > default (``<output_root>/<kind>/<YYYY-MM-DD>/``).

    All path inputs are passed through ``.expanduser()`` so users can use
    ``~/`` in their config files or CLI flags without it being interpreted
    as a literal directory name.
    """
    if cli_override is not None:
        return cli_override.expanduser()
    if config_value is not None:
        return Path(config_value).expanduser()
    return output_root.expanduser() / kind / date.today().isoformat()


def video_output_path(
    output_dir: Path,
    *,
    job_id: str,
    on: date | None = None,
) -> Path:
    """`<output_dir>/videos/<YYYY-MM-DD>/<job_id>.mp4`."""
    on = on or date.today()
    return output_dir / "videos" / on.isoformat() / f"{validate_job_id(job_id)}.mp4"


def character_output_path(
    output_dir: Path,
    *,
    entity_id: str,
    slot: int,
    on: date | None = None,
) -> Path:
    """`<output_dir>/characters/<YYYY-MM-DD>/character_<entity_id>_slot<slot>.png`.

    The ``.png`` suffix is an initial guess — Flow's fife CDN may return JPEG or
    WebP bytes.  Callers download bytes to this path (the extension is corrected
    in-flight by :func:`adjust_key_extension`).  ``entity_id`` is validated as a
    safe id so a tampered/unexpected value cannot escape the output directory.
    """
    on = on or date.today()
    safe_entity = validate_job_id(entity_id)
    return (
        output_dir / "characters" / on.isoformat() / f"character_{safe_entity}_slot{int(slot)}.png"
    )


def image_output_path(
    output_dir: Path,
    *,
    job_id: str,
    index: int = 1,
    on: date | None = None,
) -> Path:
    """`<output_dir>/images/<YYYY-MM-DD>/<job_id>_<index>.png`.

    The ``.png`` suffix is an initial guess — Flow's fife CDN may return
    JPEG or WebP bytes for the same content type. Callers should write
    bytes to this path, then pass the result through
    :func:`correct_image_extension` to rename when the actual format
    differs. See issue #96.
    """
    on = on or date.today()
    return output_dir / "images" / on.isoformat() / f"{validate_job_id(job_id)}_{index}.png"


# Magic-byte signatures for the image formats Flow's fife CDN is known to
# return. Order matters only insofar as it lets the simpler signatures
# short-circuit; checks are independent. ``RIFF/WEBP`` is special-cased
# below because RIFF is also used by .wav/.avi.
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", ".jpg"),  # JPEG: FF D8 FF (E0/E1/DB/E2/...)
    (b"\x89PNG\r\n\x1a\n", ".png"),  # PNG: 89 50 4E 47 0D 0A 1A 0A
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)

# Suffixes treated as equivalent to ``.jpg`` for the no-op decision —
# ``.jpeg`` is a documented alias and ``.jpe`` shows up on some Windows
# media tooling. Lowercase comparison.
_JPEG_ALIASES: frozenset[str] = frozenset({".jpg", ".jpeg", ".jpe"})


def extension_from_magic(head: bytes) -> str | None:
    """Return the canonical extension for ``head``'s magic bytes.

    Returns ``".jpg"`` / ``".png"`` / ``".webp"`` / ``".gif"`` for recognised
    formats, or ``None`` when the buffer is empty, too short, or doesn't
    match any known signature.

    Only inspects the first ~12 bytes — pass a longer buffer if you have
    it (no harm), or just the first 12 if you've already sliced.
    """
    if len(head) < 3:
        return None
    for sig, ext in _MAGIC_SIGNATURES:
        if head.startswith(sig):
            return ext
    # WebP needs both halves: RIFF<4-byte size>WEBP.
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def adjust_key_extension(path: Path, data: bytes) -> Path:
    """Return *path* with its suffix corrected to match *data*'s magic bytes.

    In-memory variant of :func:`correct_image_extension` — operates on raw
    bytes before writing so it works for both local ``Path`` and cloud
    ``UPath`` targets (cloud storage has no atomic rename).

    No-ops when the format is unrecognised or the suffix already matches.
    ``UPath`` subclasses ``Path`` so this function accepts both.
    """
    head = data[:12]
    actual = extension_from_magic(head)
    if actual is None:
        return path
    current = path.suffix.lower()
    if current == actual:
        return path
    if actual == ".jpg" and current in _JPEG_ALIASES:
        return path
    return path.with_suffix(actual)


def correct_image_extension(path: Path) -> Path:
    """Rename ``path`` to match the format detected in its first bytes.

    The fix for issue #96 — Flow's fife CDN sometimes serves JPEG bytes
    for a t2i / i2i request whose downstream filename is ``.png``. After
    writing the bytes to disk, call this to rename the file in-place to
    the correct extension.

    No-ops in any of these cases:

    * The first 12 bytes don't match any known image signature.
    * The current extension already matches the detected format
      (case-insensitive).
    * The current extension is a ``.jpg`` alias (``.jpeg`` / ``.jpe``)
      and the detected format is ``.jpg``.
    * The target name already exists — avoids clobbering data on a
      retry race; caller keeps the misnamed original.

    Returns the (possibly renamed) :class:`Path`.
    """
    head = path.read_bytes()[:12]
    actual = extension_from_magic(head)
    if actual is None:
        return path
    current = path.suffix.lower()
    if current == actual:
        return path
    if actual == ".jpg" and current in _JPEG_ALIASES:
        return path
    target = path.with_suffix(actual)
    if target.exists():
        # Collision: keep the original name rather than silently overwrite.
        return path
    path.rename(target)
    return target
