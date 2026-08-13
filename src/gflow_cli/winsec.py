"""Windows DACL hardening for secret-bearing directories (issue #472).

POSIX mode bits (0700/0600) cover Unix; on Windows ``chmod`` is a no-op, so a
profile created under a world-readable ``GFLOW_CLI_HOME`` inherits that
visibility — with the live Google session cookies inside. Everything here is
best-effort — callers must never fail because hardening could not be applied —
and a no-op off Windows.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

#: Marker recording that a directory was hardened once, so the (potentially
#: seconds-long) recursive ACL reset never repeats on later opens.
ACL_MARKER = ".gflow_acl_v1"

_SID_RE = re.compile(rb"S-1-\d+(?:-\d+)+")


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
    out = subprocess.run(
        [_system32("whoami.exe"), "/user"],
        check=True,
        capture_output=True,
        timeout=10,
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
        subprocess.run(
            [icacls, str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F", "/q"],
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(
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
