"""Cross-process profile lease (production-readiness plan, slice D1).

Combines a process-local registry (same-interpreter guard, no syscalls) with
a kernel advisory lock held by an open file handle — `msvcrt.locking` on
Windows, `fcntl.flock` on POSIX — so at most one holder, in this process AND
across processes, can own a given canonical profile directory at a time.

Design: docs/superpowers/specs/2026-07-19-production-readiness-hardening-design.md §5.

PID / process-start-time / profile name / owner token are DIAGNOSTIC METADATA
ONLY, written into the lock file for a human debugging contention. The
kernel lock is authoritative — nothing here ever kills a process or deletes
the lock file based on that metadata. ``release()`` unlocks and closes the
handle; it never unlinks (deleting a held-then-freed lock file would let a
racing opener recreate it and land a second, independent kernel lock on a
brand new inode while a stale reader still believes the old path is
canonical).

Sync core with a thin async twin: every real D3 call site
(``FlowApiClient``, ``auth/real_chrome.py``, ``auth/internal_chromium.py``,
``auth/verification.py``) is ``async def``, so ``aacquire``/``__aenter__``/
``__aexit__`` exist for ergonomic ``async with`` use at those call sites. The
underlying syscalls (`LK_NBLCK` / `LOCK_NB`, a dict membership check) are
non-blocking and resolve in microseconds; the only thing that ever waits is
the opt-in #478 bounded wait between attempts, which the async twin performs
with ``asyncio.sleep`` so the event loop keeps running.

Contention behavior (#478): fail-fast by default. Setting
``GFLOW_CLI_LEASE_WAIT_SECONDS>0`` opts a waiter into polling the kernel lock
until the current holder — a CLI command or a daemon queue task, both of
which release at their natural end — finishes, then taking over. Holders are
never signalled or asked to release early (which is also why no
minimum-hold / release-request machinery exists: every holder already runs
to completion). Same-process contention always fails fast: this process
holds the lease, so waiting on it would deadlock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, TypedDict, cast

from gflow_cli.config import get_settings
from gflow_cli.diagnostics import CommandHasher, emit_owner_evidence_read
from gflow_cli.errors import OwnerEvidence, ProfileLockedError, SecurityError
from gflow_cli.paths import locks_dir

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

if TYPE_CHECKING:
    from types import TracebackType

__all__ = ["OwnerMetadata", "ProfileLease"]

# ponytail: best-effort process-start proxy (no psutil dependency). Captured
# once at first import of this module in the CURRENT process — not the true
# OS process-creation time on every platform. Diagnostic only; upgrade to
# psutil.Process().create_time() if an exact OS start time is ever needed.
_PROCESS_OBSERVED_START = time.time()

_SAFE_NAME_RE = re.compile(r"[^\w\-]")

# Process-local guard: canonical profile path -> the ProfileLease instance
# currently holding it. A plain dict (not a real asyncio.Lock) is sufficient:
# `acquire()` never awaits mid-flight, so under CPython's GIL / the single
# asyncio thread, membership-check-then-insert never yields control between
# the two steps — no other task or thread can observe it half-done.
# `_registry_guard` is cheap insurance against a genuine OS-thread caller,
# which no current gflow-cli code path exercises.
_registry: dict[Path, ProfileLease] = {}
_registry_guard = threading.Lock()


class OwnerMetadata(TypedDict):
    """Diagnostic-only fields written into the lock file. Never authoritative.

    On-disk layout (v1): byte 0 is a reserved sentinel — the ONLY byte the
    kernel lock covers on Windows — and this JSON begins at offset 1 so a
    contender can read it while the lock is held. Legacy files whose JSON
    starts at byte 0 are unreadable under a Windows lock and degrade to
    evidence-unavailable.
    """

    version: int
    pid: int
    process_start_time: float
    profile_name: str
    owner_token: str


_METADATA_VERSION = 1
_METADATA_MAX_BYTES = 4095
_METADATA_KEYS = frozenset({"version", "pid", "process_start_time", "profile_name", "owner_token"})

# Per-process HMAC identities for contention evidence: random key, in-memory
# only, never persisted (one CLI process == one command; see design §6.4).
_evidence_hasher = CommandHasher()


def _evidence_from_metadata(metadata: OwnerMetadata) -> OwnerEvidence:
    return OwnerEvidence(
        pid=metadata["pid"],
        process_start_time=metadata["process_start_time"],
        profile_identity=_evidence_hasher.identity(metadata["profile_name"]),
        owner_token_identity=_evidence_hasher.identity(metadata["owner_token"]),
    )


def _read_owner_evidence(fd: int) -> OwnerEvidence | None:
    """Best-effort offset-1 read of the owner's metadata from the already-open
    descriptor after lock contention. Validates version/schema/primitive types;
    ANY failure (legacy byte-0 files, torn writes, junk) degrades to ``None``.
    Never reads the locked byte 0, never authorizes any destructive action."""
    try:
        os.lseek(fd, 1, os.SEEK_SET)
        raw = os.read(fd, _METADATA_MAX_BYTES)
        parsed: object = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            return None
        data = cast("dict[str, object]", parsed)
        if set(data) != set(_METADATA_KEYS):
            return None
        version = data["version"]
        pid = data["pid"]
        start = data["process_start_time"]
        name = data["profile_name"]
        token = data["owner_token"]
        if version != _METADATA_VERSION:
            return None
        if not isinstance(pid, int) or isinstance(pid, bool):
            return None
        if not isinstance(start, (int, float)) or isinstance(start, bool):
            return None
        if not isinstance(name, str) or not isinstance(token, str):
            return None
        if len(name) > 256 or len(token) > 128:
            return None
        return OwnerEvidence(
            pid=pid,
            process_start_time=float(start),
            profile_identity=_evidence_hasher.identity(name),
            owner_token_identity=_evidence_hasher.identity(token),
        )
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None


def _canonicalize(profile_dir: Path | str) -> Path:
    """Resolve *profile_dir* so two spellings of one directory map to one lock."""
    return Path(profile_dir).expanduser().resolve()


def _lock_filename(canonical: Path) -> str:
    digest = hashlib.sha256(str(canonical).encode("utf-8", "surrogateescape")).hexdigest()
    safe_name = _SAFE_NAME_RE.sub("_", canonical.name) or "profile"
    return f"{safe_name}-{digest[:16]}.lock"


def _raise_locked(
    reason: str,
    canonical: Path,
    *,
    cause: BaseException | None = None,
    evidence: OwnerEvidence | None = None,
) -> NoReturn:
    err = ProfileLockedError(
        detail=f"profile {canonical} is locked ({reason})",
        remediation_hint=(
            "A live process still holds this profile — commonly a prior gflow "
            "command, a `gflow serve` daemon, or a leftover Chrome/python "
            "process (the recorded owner PID is shown with this error). Close "
            "it and retry, use a different --profile, or set "
            "GFLOW_CLI_LEASE_WAIT_SECONDS to wait for the holder to finish. "
            "If nothing is running, the lock is already released — a leftover "
            "lock file never blocks acquisition, so just retry."
        ),
    )
    err.owner_evidence = evidence  # private; never serialized (design §6.4)
    if cause is not None:
        raise err from cause
    raise err


# Kernel-lock poll cadence while waiting (#478). Coarse on purpose: the
# waiter is standing in line behind a browser session, not a mutex.
_WAIT_POLL_SECONDS = 0.5


def _retry_delay(deadline: float | None) -> float | None:
    """Seconds to sleep before the next kernel-lock attempt, or None to give
    up (fail-fast mode, or the #478 wait deadline has passed)."""
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    return min(_WAIT_POLL_SECONDS, remaining)


def _lock_nonblocking(fd: int) -> None:
    """Acquire the kernel advisory lock on byte 0; raises OSError on contention."""
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


class ProfileLease:
    """Exclusive lease on a canonical profile directory.

    ``with ProfileLease(profile_dir):`` (or ``async with`` / ``try_acquire()``)
    guarantees at most one holder — in this process AND across processes —
    for as long as the lease is held. Contention raises ``ProfileLockedError``
    (exit code 11) immediately by default; ``GFLOW_CLI_LEASE_WAIT_SECONDS>0``
    opts into a bounded wait for a holder in ANOTHER process to finish (#478).
    Same-process contention always fails fast — waiting would deadlock.
    """

    #: Documents the release() contract for callers (esp. crash-recovery code)
    #: that must never treat a released lock file as free-to-delete.
    release_does_not_unlink_lock_file: bool = True

    def __init__(self, profile_dir: Path | str) -> None:
        settings = get_settings()
        self._canonical = _canonicalize(profile_dir)
        self.lock_path: Path = locks_dir(settings.home) / _lock_filename(self._canonical)
        self._fd: int | None = None
        self._metadata: OwnerMetadata | None = None
        self._wait_seconds = float(settings.lease_wait_seconds)

    @property
    def owner_metadata(self) -> OwnerMetadata:
        if self._metadata is None:
            msg = "owner_metadata is only available on an acquired ProfileLease"
            raise RuntimeError(msg)
        return self._metadata

    # -- sync core ---------------------------------------------------------

    def acquire(self) -> ProfileLease:
        return self._acquire_with_wait(self._wait_seconds)

    def _acquire_with_wait(self, wait_seconds: float) -> ProfileLease:
        self._register_same_process_fail_fast()
        deadline = time.monotonic() + wait_seconds if wait_seconds > 0 else None
        # BaseException, not Exception: asyncio.CancelledError (via the async
        # twin) and KeyboardInterrupt (mid time.sleep) otherwise skip the
        # unregister and poison this profile for the process lifetime.
        try:
            while True:
                delay = _retry_delay(deadline)
                if self._attempt(delay):
                    return self
                assert delay is not None  # _attempt(None) either returns True or raises
                time.sleep(delay)
        except BaseException:
            self._unregister()
            raise

    def _attempt(self, delay: float | None) -> bool:
        """One kernel-lock attempt. True = acquired; False = contended but
        retryable (``delay`` set); raises on terminal contention. delay=None
        marks the terminal attempt (fail-fast mode, or the #478 deadline has
        passed): only that attempt reads/emits owner evidence — doing it on
        every poll tick spammed the log 2x/s."""
        try:
            self._acquire_kernel_lock(collect_evidence=delay is None)
        except ProfileLockedError:
            if delay is None:
                raise
            return False
        return True

    def _register_same_process_fail_fast(self) -> None:
        """Same-process contention NEVER waits (#478): the holder is this very
        process, so polling would deadlock until the timeout. The registry
        entry also makes a second same-process waiter fail fast instead of
        queueing behind the first — one waiter per profile per process."""
        with _registry_guard:
            if self._canonical in _registry:
                owner_metadata = _registry[self._canonical]._metadata  # noqa: SLF001
                _raise_locked(
                    "already held in this process",
                    self._canonical,
                    evidence=(
                        _evidence_from_metadata(owner_metadata)
                        if owner_metadata is not None
                        else None
                    ),
                )
            _registry[self._canonical] = self

    def _unregister(self) -> None:
        with _registry_guard:
            if _registry.get(self._canonical) is self:
                del _registry[self._canonical]

    def try_acquire(self) -> bool:
        """Non-blocking probe by contract: always bypasses the #478 wait —
        probe callers (e2e polling loops, crash recovery) need an immediate
        answer regardless of GFLOW_CLI_LEASE_WAIT_SECONDS."""
        try:
            self._acquire_with_wait(0.0)
        except ProfileLockedError:
            return False
        return True

    def release(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                _unlock(fd)
            finally:
                os.close(fd)
        self._metadata = None
        self._unregister()

    def __enter__(self) -> ProfileLease:
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    # -- async twin (see module docstring) -----------------------------------

    async def aacquire(self) -> ProfileLease:
        """Async twin of :meth:`acquire` for D3's async call sites. In the
        zero-wait default it delegates to :meth:`acquire` outright — the exact
        historic pass-through, which is also the seam existing tests pin. With
        the #478 wait enabled it runs its own loop whose only awaits are
        ``asyncio.sleep`` polls, so the event loop keeps running — a blocking
        sleep here would starve the very holder the waiter is waiting on when
        both share a loop (e.g. a daemon task waiting out a CLI command)."""
        if self._wait_seconds <= 0:
            return self.acquire()
        self._register_same_process_fail_fast()
        deadline = time.monotonic() + self._wait_seconds
        # BaseException: see _acquire_with_wait — CancelledError must unregister.
        try:
            while True:
                delay = _retry_delay(deadline)
                if self._attempt(delay):
                    return self
                assert delay is not None  # _attempt(None) either returns True or raises
                await asyncio.sleep(delay)
        except BaseException:
            self._unregister()
            raise

    async def __aenter__(self) -> ProfileLease:
        return await self.aacquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    # -- kernel lock ---------------------------------------------------------

    def _acquire_kernel_lock(self, *, collect_evidence: bool = True) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_path.is_symlink():
            raise SecurityError(
                f"refusing to follow a symlinked lock path: {self.lock_path}",
                remediation_hint="Remove the symlink at the lock path and retry.",
            )

        # O_NOFOLLOW (POSIX only; 0 no-op on Windows) closes the TOCTOU window
        # between the is_symlink() check above and the open below. O_BINARY
        # (Windows only; 0 no-op on POSIX) stops the CRT translating bytes.
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(self.lock_path, flags, 0o600)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.fstat(fd).st_size == 0:
                # Windows msvcrt.locking requires the locked region to exist.
                os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
            try:
                _lock_nonblocking(fd)
            except OSError as exc:
                evidence = _read_owner_evidence(fd) if collect_evidence else None
                if collect_evidence:
                    emit_owner_evidence_read(valid=evidence is not None)
                _raise_locked(
                    f"held by another process ({exc})",
                    self._canonical,
                    cause=exc,
                    evidence=evidence,
                )

            metadata: OwnerMetadata = {
                "version": _METADATA_VERSION,
                "pid": os.getpid(),
                "process_start_time": _PROCESS_OBSERVED_START,
                "profile_name": self._canonical.name,
                "owner_token": uuid.uuid4().hex,
            }
            payload = json.dumps(metadata).encode("utf-8")
            # Byte 0 stays a reserved sentinel — the only byte the kernel lock
            # covers on Windows. Metadata begins at offset 1 so a contender can
            # read it while we hold the lock (design §6.4, S08).
            os.lseek(fd, 1, os.SEEK_SET)
            os.write(fd, payload)
            os.ftruncate(fd, 1 + len(payload))
        except Exception:
            os.close(fd)
            raise
        self._fd = fd
        self._metadata = metadata
