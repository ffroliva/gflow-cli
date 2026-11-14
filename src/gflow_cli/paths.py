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
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from platformdirs import user_data_dir, user_downloads_dir

if TYPE_CHECKING:
    from gflow_cli.api.video import VideoResult

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
    raise FileNotFoundError(f"No Chrome Cookies file found under {pdir}")


def config_file(home: Path) -> Path:
    """Where the per-user config TOML lives."""
    return home / "config.toml"


def database_path(home: Path) -> Path:
    """SQLite DB path under GFLOW_CLI_HOME."""
    return home / "gflow.db"


def user_tools_dir(home: Path) -> Path:
    """Where user-authored ("My Tools") tool TOMLs live, under GFLOW_CLI_HOME."""
    return home / "tools"


def locks_dir(home: Path) -> Path:
    """Where cross-process profile-lease lock files live, under GFLOW_CLI_HOME.

    See `gflow_cli.profile_lease.ProfileLease` — one file per canonical
    resolved profile directory, holding a kernel advisory lock plus
    diagnostic-only metadata. Never deleted while (or after) held.
    """
    return home / "locks"


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


def extend_output_path(output_dir: Path, *, media_id: str) -> Path:
    """`<output_dir>/videos/<YYYY-MM-DD>/<media_id>_extend.mp4`, tagged if it already exists.

    An in-place extend keeps the source id (#1184), so the name repeats for every extend of
    that clip; the tag is the one ``_unique_display_name`` gives a re-uploaded file (#792).
    shortcut: checks the local file only, so a cloud ``storage_uri`` key is still reused.
    """
    path = output_dir / "videos" / date.today().isoformat() / f"{media_id}_extend.mp4"
    return path.with_name(f"{path.stem}-{uuid4().hex[:8]}.mp4") if path.exists() else path


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


def looks_like_video(head: bytes) -> bool:
    """True when ``head``'s magic bytes are a known video container.

    Positive detection only — an unrecognised or short buffer returns ``False``
    so callers can fail loud on *definitely* a video without misclassifying
    arbitrary or novel image bytes. Recognises ISO-BMFF (MP4/MOV: a size box
    followed by the ``ftyp`` box at offset 4) and Matroska/WebM (EBML header
    ``1A 45 DF A3``).

    Used to guard the image-download path: an agentic ``gflow_generate_image``
    can have Flow's conversational agent produce a *video*, whose bytes would
    otherwise be saved with an image extension (a silent corruption that then
    fails far downstream when used as an i2v frame).
    """
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return True
    return len(head) >= 4 and head[:4] == b"\x1a\x45\xdf\xa3"


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


def numbered_path(path: Path, number: int) -> Path:
    """File ``number`` (1-based) of several written from one ``--out-file PATH``.

    The one naming rule for every command that fans one ``--out-file`` out to several
    files (``hero.png`` -> ``hero_1.png``, ``hero_2.png``; #1158, #1293).
    """
    return path.parent / f"{path.stem}_{number}{path.suffix}"


def relocate_video(item: Any, target: Path) -> Any:
    """Move ``item.local_path`` to ``target``; ``item`` unchanged when there is no file."""
    local_p = cast("Path | None", getattr(item, "local_path", None))
    if local_p is None or not local_p.exists():
        return item
    if local_p != target:
        # shutil.move, not Path.replace: a rename across mounts raises EXDEV (#1114).
        shutil.move(local_p, target)
        return replace(item, local_path=target)
    return item


def relocate_video_output(result: Any, output_file: Path | None) -> Any:
    """Apply ``--out-file`` to one result or a list: one file keeps PATH, several are numbered."""
    if output_file is None:
        return result
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(result, (list, tuple)):
        items = cast("tuple[Any, ...] | list[Any]", result)
        relocated: list[Any] = []
        is_single = len(items) == 1
        for i, item in enumerate(items, start=1):
            target = output_file if is_single else numbered_path(output_file, i)
            relocated.append(relocate_video(item, target))
        return relocated

    return relocate_video(result, output_file)


def chain_clip_relocator(
    output_file: Path | None,
) -> Callable[[int, VideoResult], VideoResult] | None:
    """``--out-file`` for a chain (#1293): link ``index`` goes to ``<stem>_<index + 1><suffix>``.

    ``index`` counts the whole manifest (#1300), so a resumed run names its links as a full
    run would. Always numbered, even for one link, so a resume never renames a clip. Raises
    instead of replacing an existing target; ``run_chain`` then keeps the clip where it is.
    """
    if output_file is None:
        return None
    output_file.parent.mkdir(parents=True, exist_ok=True)

    def relocate(index: int, result: VideoResult) -> VideoResult:
        target = numbered_path(output_file, index + 1)
        if target.exists():  # an earlier run's clip, or a folder: never replace it
            raise FileExistsError(f"{target} already exists")
        try:
            return relocate_video(result, target)
        except OSError:
            target.unlink(missing_ok=True)  # a partial copy: the check above saw no file here
            raise

    return relocate
