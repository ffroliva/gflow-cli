"""Windows DACL hardening for secret-bearing directories and files (#472, #1437).

POSIX mode bits (0700/0600) cover Unix; on Windows ``chmod`` is a no-op, so a
profile created under a world-readable ``GFLOW_CLI_HOME`` inherits that
visibility — with the live Google session cookies inside. Everything here is
best-effort — callers must never fail because hardening could not be applied —
and a no-op off Windows.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

logger = structlog.get_logger(__name__)

#: Marker recording that a directory was hardened once, so the (potentially
#: seconds-long) recursive ACL reset never repeats on later opens.
ACL_MARKER = ".gflow_acl_v1"

_SID_RE = re.compile(rb"S-1-\d+(?:-\d+)+")

#: A process without a console (an MCP server started by a GUI) would flash a window.
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _system32(executable: str) -> str:
    """Absolute path under System32 — a security control must not depend on a
    PATH lookup that can be shadowed or broken."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", executable)


def _current_user_sid() -> str:
    """The current user's SID from ``whoami /user``.

    Parsed from RAW BYTES: console tools emit the OEM codepage, so a
    non-ASCII account name would break text-mode decoding — while the SID
    cell itself is always ASCII.
    """
    out = subprocess.run(  # noqa: S603 - fixed System32 binary, argv list, no shell
        [_system32("whoami.exe"), "/user"],
        check=True,
        capture_output=True,
        timeout=10,
        creationflags=_NO_WINDOW,
    )
    match = _SID_RE.search(out.stdout)
    if match is None:
        msg = "whoami /user produced no SID"
        raise ValueError(msg)
    return match.group(0).decode("ascii")


def restrict_dir_to_current_user(path: Path) -> bool:
    """Best-effort Windows DACL hardening: strip inherited ACEs and grant only
    the current user, recursively (issue #472).

    Two steps, verified empirically: applying the inheritance-flagged grant
    directly to files via ``/t`` leaves them WITHOUT effective access
    (PermissionError on read). Harden the top dir, then ``/reset`` children so
    they INHERIT the single owner-only ACE. Never raises — a hardening
    failure must not break the caller. Returns True only when applied.
    """
    if sys.platform != "win32":
        return False
    icacls = _system32("icacls.exe")
    try:
        sid = _current_user_sid()
        subprocess.run(  # noqa: S603 - fixed System32 binary, argv list, no shell
            [icacls, str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F", "/q"],
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(  # noqa: S603 - fixed System32 binary, argv list, no shell
            [icacls, str(path / "*"), "/reset", "/t", "/q"],
            check=True,
            capture_output=True,
            timeout=120,  # /t rewrites every file in a Chromium profile
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, caller must proceed
        stderr = getattr(exc, "stderr", b"") or b""
        detail = (
            stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr)
        )
        logger.warning(
            "auth_profile_acl_failed",
            error=type(exc).__name__,
            returncode=getattr(exc, "returncode", None),
            stderr=detail[:200],
        )
        return False
    return True


def ensure_profile_hardened(path: Path) -> bool:
    """Marker-gated hardening sweep — also covers profiles created before
    #472 shipped (they stay world-readable until some command opens them).

    One ``stat`` when already hardened; otherwise applies the DACL and drops
    ``ACL_MARKER`` inside the directory. Returns True only when hardening was
    applied in this call.
    """
    if sys.platform != "win32" or not path.is_dir():
        return False
    marker = path / ACL_MARKER
    if marker.exists():
        return False
    if not restrict_dir_to_current_user(path):
        return False
    try:
        marker.write_bytes(b"")
    except OSError:
        logger.warning("auth_profile_acl_marker_failed")
    return True


_FileIdentity = tuple[int, int, int]  # (st_dev, st_ino, birth_ns)

#: Last-hardened file identity per exact path: path -> (identity, ok).
#: Bounded to one entry per distinct path; including birth_ns alongside
#: (st_dev, st_ino) distinguishes recreated sidecars even if an inode/file ID
#: is reused on deletion.
_file_acl_lock = threading.Lock()
_file_acl_cache: dict[str, tuple[_FileIdentity, bool]] = {}


@dataclass(frozen=True)
class FileAclResult:
    """Outcome of :func:`harden_files_once` for one batch of files."""

    ok: bool
    sid: str | None = None
    sid_failed: bool = False


def forget_file_acl(path: str) -> None:
    """Drop any cached ACL outcome for *path* (called when gflow creates a fresh file)."""
    with _file_acl_lock:
        _file_acl_cache.pop(path, None)


def _regular_file_identity(path: str) -> _FileIdentity | None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("file_acl_failed", error=type(exc).__name__)
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    # On Windows st_birthtime_ns (3.12+) or st_ctime_ns (3.11) is file creation time.
    birth_ns = int(getattr(st, "st_birthtime_ns", st.st_ctime_ns))
    return (st.st_dev, st.st_ino, birth_ns)


def _apply_file_acl(path: str, sid: str) -> bool | None:
    try:
        # Fixed System32 binary, argv list, no shell.
        subprocess.run(  # noqa: S603
            [_system32("icacls.exe"), path, "/inheritance:r", "/grant:r", f"*{sid}:F", "/q"],
            check=True,
            capture_output=True,
            timeout=10,
            creationflags=_NO_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001
        if not os.path.lexists(path):
            # Sidecar vanished before icacls ran (e.g. concurrent connection close).
            return None
        # Best-effort: the caller must proceed. No stderr: icacls echoes the path.
        logger.warning(
            "file_acl_failed",
            error=type(exc).__name__,
            returncode=getattr(exc, "returncode", None),
        )
        return False
    return True


def _partition_pending_files(
    paths: Iterable[str],
) -> tuple[list[tuple[str, _FileIdentity]], bool]:
    """Return uncached regular files in *paths* and whether cached entries succeeded."""
    all_ok = True
    pending: list[tuple[str, _FileIdentity]] = []
    seen_paths: set[str] = set()
    seen_ids: set[_FileIdentity] = set()
    for path in paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        identity = _regular_file_identity(path)
        if identity is None or (identity[1] != 0 and identity in seen_ids):
            continue
        if identity[1] != 0:
            seen_ids.add(identity)
        with _file_acl_lock:
            cached = _file_acl_cache.get(path)
        if cached is not None and cached[0] == identity:
            all_ok = all_ok and cached[1]
            continue
        pending.append((path, identity))
    return pending, all_ok


def harden_files_once(paths: Iterable[str], *, sid: str | None = None) -> FileAclResult:
    """Grant only the current user on each existing regular file in *paths*.

    Never touches the containing directory, which may not be ours (issue #1437).
    Cached per ``(path, st_dev, st_ino, birth_ns)`` so unchanged repeated opens hit
    memory only, while recreated sidecars or replaced files are hardened again.
    Resolves ``whoami /user`` at most once per call.
    """
    if sys.platform != "win32":
        return FileAclResult(ok=False)

    pending, all_ok = _partition_pending_files(paths)
    if not pending:
        return FileAclResult(ok=all_ok, sid=sid, sid_failed=False)

    if sid is None:
        try:
            sid = _current_user_sid()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "file_acl_failed",
                error=type(exc).__name__,
                returncode=getattr(exc, "returncode", None),
            )
            with _file_acl_lock:
                for path, identity in pending:
                    _file_acl_cache[path] = (identity, False)
            return FileAclResult(ok=False, sid=None, sid_failed=True)

    for path, identity in pending:
        outcome = _apply_file_acl(path, sid)
        if outcome is None:
            continue
        with _file_acl_lock:
            _file_acl_cache[path] = (identity, outcome)
        all_ok = all_ok and outcome

    return FileAclResult(ok=all_ok, sid=sid, sid_failed=False)
