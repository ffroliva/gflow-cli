"""Two-real-process contention + crash-release proof for ProfileLease (Task D2,
design spec §5). Complements tests/test_profile_lease.py's in-process suite.

D1's own report flags why in-process tests cannot prove this: the
process-local registry (a plain dict keyed by canonical path, in
gflow_cli.profile_lease._registry) lives in ONE interpreter. A second real
OS process never sees it -- only the kernel advisory lock (msvcrt on
Windows, fcntl on POSIX) can reject it. So this file launches two REAL
subprocesses sharing GFLOW_CLI_HOME and the same profile_dir argument (both
canonicalize to the same lock file under locks_dir()) and observes their
process exit codes.

Synchronization: no sleep-and-hope. The holder subprocess writes a ready
marker file (containing its lock_path) only AFTER ProfileLease.acquire()
returns; the test polls for that file with a bounded timeout before ever
starting the contender.

Exit-code contract: the contender subprocess maps a caught
ProfileLockedError to an exit code via the SAME EXIT_CODE_MAP isinstance
walk _cli_helpers._exit_code_for uses. The mapping is duplicated inline in
the child script (it cannot import test-process objects) -- the same
pattern tests/test_errors.py and tests/api/test_aisandbox_auth_error.py
already use for their own local `_exit_code_for` copies. ProfileLockedError
has no dedicated EXIT_CODE_MAP entry -- it inherits ConfigurationError's 11.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from gflow_cli.errors import EXIT_CODE_MAP, ProfileLockedError

_READY_TIMEOUT = 30.0  # Windows CI is slow; bounded, not sleep-and-hope.
_EXIT_TIMEOUT = 30.0
_CONTENDER_TIMEOUT = 60.0
_POLL_INTERVAL = 0.05


def _profile_locked_exit_code() -> int:
    """Mirror `_cli_helpers._exit_code_for`'s most-specific-first isinstance
    walk to derive ProfileLockedError's exit code from the live
    EXIT_CODE_MAP, instead of hardcoding the 11 it currently resolves to."""
    for cls, code in EXIT_CODE_MAP.items():
        if issubclass(ProfileLockedError, cls):
            return code
    return 1


_HOLDER_SCRIPT = """\
import sys
import time
from pathlib import Path

from gflow_cli.profile_lease import ProfileLease

profile_dir = Path(sys.argv[1])
ready_marker = Path(sys.argv[2])
stop_marker = Path(sys.argv[3])

lease = ProfileLease(profile_dir)
lease.acquire()
ready_marker.write_text(str(lease.lock_path), encoding="utf-8")

deadline = time.monotonic() + 60.0
while not stop_marker.exists() and time.monotonic() < deadline:
    time.sleep(0.05)

if stop_marker.exists():
    # Graceful-shutdown path (test_holder_wins_and_second_process_fails_fast):
    # release explicitly before exiting.
    lease.release()
# Otherwise (test_process_exit_releases_kernel_lock) the test kills this
# process before the deadline -- release() is deliberately never called, so
# the kernel lock is freed only by the OS reclaiming the process's handles.
"""

_CONTENDER_SCRIPT = """\
import sys
from pathlib import Path

from gflow_cli.errors import EXIT_CODE_MAP
from gflow_cli.profile_lease import ProfileLease

profile_dir = Path(sys.argv[1])
lease = ProfileLease(profile_dir)
try:
    lease.acquire()
except Exception as exc:
    code = 1
    for cls, mapped in EXIT_CODE_MAP.items():
        if isinstance(exc, cls):
            code = mapped
            break
    print(f"CONTENDER_LOCKED:{exc.__class__.__name__}", file=sys.stderr)
    sys.exit(code)
else:
    print("CONTENDER_ACQUIRED")
    lease.release()
    sys.exit(0)
"""


def _write_script(tmp_path: Path, name: str, content: str) -> Path:
    script = tmp_path / name
    script.write_text(content, encoding="utf-8")
    return script


def _subprocess_env(home: Path) -> dict[str, str]:
    """Both processes must resolve the SAME canonical lock file, so
    GFLOW_CLI_HOME is pinned explicitly here rather than relying on whatever
    the parent test process's own environment happens to resolve to."""
    env = dict(os.environ)
    env["GFLOW_CLI_HOME"] = str(home)
    return env


def _wait_for(path: Path, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(_POLL_INTERVAL)
    raise AssertionError(f"timed out after {timeout}s waiting for {path}")


def _launch_holder(
    tmp_path: Path, profile_dir: Path, env: dict[str, str]
) -> tuple[subprocess.Popen[str], Path, Path]:
    script = _write_script(tmp_path, "holder.py", _HOLDER_SCRIPT)
    ready_marker = tmp_path / "holder_ready"
    stop_marker = tmp_path / "holder_stop"
    proc = subprocess.Popen(
        [sys.executable, str(script), str(profile_dir), str(ready_marker), str(stop_marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    return proc, ready_marker, stop_marker


def _run_contender(
    tmp_path: Path, profile_dir: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    script = _write_script(tmp_path, "contender.py", _CONTENDER_SCRIPT)
    return subprocess.run(
        [sys.executable, str(script), str(profile_dir)],
        capture_output=True,
        text=True,
        timeout=_CONTENDER_TIMEOUT,
        env=env,
    )


def test_holder_wins_and_second_process_fails_fast(tmp_path: Path) -> None:
    """A live holder subprocess causes a contending subprocess to fail fast
    with ProfileLockedError's exit code -- proves the KERNEL lock (not just
    the in-process registry, which a second real process never shares)
    rejects the second opener."""
    profile_dir = tmp_path / "profile"
    env = _subprocess_env(tmp_path / "gflow_home")
    holder, ready_marker, stop_marker = _launch_holder(tmp_path, profile_dir, env)
    try:
        _wait_for(ready_marker, timeout=_READY_TIMEOUT)

        result = _run_contender(tmp_path, profile_dir, env)

        assert result.returncode == _profile_locked_exit_code(), (
            f"expected exit {_profile_locked_exit_code()}, got {result.returncode}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "CONTENDER_LOCKED:ProfileLockedError" in result.stderr
    finally:
        stop_marker.touch()
        try:
            holder.communicate(timeout=_EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.communicate(timeout=_EXIT_TIMEOUT)


def test_process_exit_releases_kernel_lock(tmp_path: Path) -> None:
    """After the holder process is KILLED (crash release -- no clean
    ProfileLease.release() call, no interpreter shutdown hooks run), a new
    acquire succeeds: the OS frees the advisory lock when the process dies,
    and the stale lock file the dead holder left behind (crash means
    release_does_not_unlink_lock_file was never even reached) does not block
    reacquisition."""
    profile_dir = tmp_path / "profile"
    env = _subprocess_env(tmp_path / "gflow_home")
    holder, ready_marker, _stop_marker = _launch_holder(tmp_path, profile_dir, env)
    try:
        _wait_for(ready_marker, timeout=_READY_TIMEOUT)
        lock_path = Path(ready_marker.read_text(encoding="utf-8"))

        holder.kill()
        holder.communicate(timeout=_EXIT_TIMEOUT)

        assert lock_path.exists(), "stale lock file must survive a crashed holder"

        result = _run_contender(tmp_path, profile_dir, env)

        assert result.returncode == 0, (
            f"expected exit 0, got {result.returncode}: "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "CONTENDER_ACQUIRED" in result.stdout
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=_EXIT_TIMEOUT)
