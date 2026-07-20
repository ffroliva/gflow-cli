"""Tests for the cross-process profile lease (production-readiness plan, slice D1).

See docs/superpowers/specs/2026-07-19-production-readiness-hardening-design.md §5.
`tests/conftest.py::_isolate_settings` (autouse) redirects GFLOW_CLI_HOME to a
per-test tmp dir, so every lease here writes its lock file under an isolated
`<tmp>/gflow_home/locks/` — no test touches the developer's real profile data.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import gflow_cli.profile_lease as profile_lease
from gflow_cli.errors import ProfileLockedError
from gflow_cli.profile_lease import ProfileLease


def test_owner_metadata_before_acquire_raises(tmp_path: Path) -> None:
    lease = ProfileLease(tmp_path / "profile")
    with pytest.raises(RuntimeError, match="acquired"):
        _ = lease.owner_metadata


def test_kernel_lock_rejects_second_opener_even_if_registry_is_bypassed(
    tmp_path: Path,
) -> None:
    """Proves the kernel advisory lock — not just the in-process registry —
    rejects a second opener. Simulates "a second process already has this
    profile" in-process by dropping the first lease's registry entry (as the
    process-local guard would never see an unrelated process) while its
    kernel lock/fd stays open. Real two-process contention is D2's job."""
    first = ProfileLease(tmp_path / "profile").acquire()
    canonical = profile_lease._canonicalize(tmp_path / "profile")
    profile_lease._registry.pop(canonical, None)
    try:
        with pytest.raises(ProfileLockedError):
            ProfileLease(tmp_path / "profile").acquire()
    finally:
        first.release()


def test_same_process_second_acquire_raises_profile_locked(tmp_path: Path) -> None:
    with ProfileLease(tmp_path / "profile"):
        with pytest.raises(ProfileLockedError):
            ProfileLease(tmp_path / "profile").acquire()


def test_release_allows_reacquire(tmp_path: Path) -> None:
    lease = ProfileLease(tmp_path / "profile")
    lease.acquire()
    lease.release()
    ProfileLease(tmp_path / "profile").acquire().release()


def test_different_profiles_can_acquire_in_parallel(tmp_path: Path) -> None:
    first = ProfileLease(tmp_path / "one").acquire()
    second = ProfileLease(tmp_path / "two").acquire()
    second.release()
    first.release()


def test_metadata_never_authorizes_kill_or_unlink(tmp_path: Path) -> None:
    lease = ProfileLease(tmp_path / "profile").acquire()
    assert lease.owner_metadata["pid"] == os.getpid()
    assert lease.release_does_not_unlink_lock_file is True
    lease.release()


def test_release_really_does_not_unlink_lock_file(tmp_path: Path) -> None:
    """Behavioral proof, not just the declared invariant flag above."""
    lease = ProfileLease(tmp_path / "profile").acquire()
    lock_path = lease.lock_path
    assert lock_path.exists()
    lease.release()
    assert lock_path.exists()


def test_canonical_path_equivalence_contends(tmp_path: Path) -> None:
    """Two spellings of the same directory resolve to one lock and contend."""
    real = tmp_path / "profile"
    real.mkdir()
    other_spelling = tmp_path / "sub" / ".." / "profile"

    with ProfileLease(real):
        with pytest.raises(ProfileLockedError):
            ProfileLease(other_spelling).acquire()


def test_try_acquire_returns_false_on_contention(tmp_path: Path) -> None:
    first = ProfileLease(tmp_path / "profile").acquire()
    second = ProfileLease(tmp_path / "profile")
    assert second.try_acquire() is False
    first.release()
    assert second.try_acquire() is True
    second.release()


def test_acquire_returns_self_for_fluent_chaining(tmp_path: Path) -> None:
    lease = ProfileLease(tmp_path / "profile")
    assert lease.acquire() is lease
    lease.release()


async def test_async_context_manager_serves_async_call_sites(tmp_path: Path) -> None:
    async with ProfileLease(tmp_path / "profile") as lease:
        assert lease.owner_metadata["pid"] == os.getpid()
        with pytest.raises(ProfileLockedError):
            await ProfileLease(tmp_path / "profile").aacquire()


def test_symlinked_lock_path_is_rejected(tmp_path: Path) -> None:
    """Never follow a symlink for the lock path itself (defense against a
    pre-planted symlink at the hash-derived lock location)."""
    lease = ProfileLease(tmp_path / "profile")
    lease.lock_path.parent.mkdir(parents=True, exist_ok=True)
    decoy_target = tmp_path / "decoy.txt"
    decoy_target.write_bytes(b"not a lock file")
    try:
        lease.lock_path.symlink_to(decoy_target)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    with pytest.raises(Exception, match="symlink"):
        lease.acquire()


def test_symlinked_lock_path_is_rejected_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same guard as above, exercised without relying on symlink privileges
    (real symlink creation needs Developer Mode/admin on Windows CI)."""
    lease = ProfileLease(tmp_path / "profile")
    monkeypatch.setattr(Path, "is_symlink", lambda self: True)
    with pytest.raises(Exception, match="symlink"):
        lease.acquire()


@pytest.mark.skipif(sys.platform != "win32", reason="msvcrt is Windows-only")
def test_lock_file_has_at_least_one_byte_for_windows(tmp_path: Path) -> None:
    lease = ProfileLease(tmp_path / "profile").acquire()
    try:
        assert lease.lock_path.stat().st_size >= 1
    finally:
        lease.release()
