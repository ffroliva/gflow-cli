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


def test_contention_remediation_names_live_owner_not_indefinite_wait(
    tmp_path: Path,
) -> None:
    """#370: a blocking lock always has a *live* owner (the kernel releases an
    advisory lock the instant its holder dies, so a leftover file never blocks).
    The remediation must not tell a stuck operator to 'wait for it to finish'
    (implies an unbounded wait) — it must name the live-owner reality and the
    'just retry if nothing is running' escape. Guards the honest wording."""
    first = ProfileLease(tmp_path / "profile").acquire()
    canonical = profile_lease._canonicalize(tmp_path / "profile")
    profile_lease._registry.pop(canonical, None)
    try:
        with pytest.raises(ProfileLockedError) as exc_info:
            ProfileLease(tmp_path / "profile").acquire()
    finally:
        first.release()
    hint = exc_info.value.remediation_hint
    assert "Wait for it to finish" not in hint
    assert "live process" in hint
    assert "just retry" in hint


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


# --- offset-1 owner metadata + private contention evidence (S07-S09) --------


def test_lock_file_layout_byte0_sentinel_metadata_at_offset1(tmp_path: Path) -> None:
    """Byte 0 is a reserved sentinel (the ONLY locked byte); versioned JSON
    metadata begins at offset 1 so a Windows contender can read it while the
    kernel lock is held (S08)."""
    import json

    lease = ProfileLease(tmp_path / "profile").acquire()
    lease.release()
    # Read AFTER release: while held, even byte 0 is unreadable from another
    # fd on Windows — which is precisely why metadata must start at offset 1
    # (the while-held read path is proven by the cross-process tests below).
    raw = lease.lock_path.read_bytes()
    assert raw[0:1] == b"\0"
    metadata = json.loads(raw[1:].decode("utf-8"))
    assert metadata["version"] == 1
    assert metadata["pid"] == os.getpid()
    assert set(metadata) == {
        "version",
        "pid",
        "process_start_time",
        "profile_name",
        "owner_token",
    }


def test_same_process_contention_uses_registry_metadata(tmp_path: Path) -> None:
    """S07: in-process contention reports validated evidence from the
    registered owner's in-memory metadata — HMAC identities, never raw."""
    first = ProfileLease(tmp_path / "profile").acquire()
    try:
        with pytest.raises(ProfileLockedError) as excinfo:
            ProfileLease(tmp_path / "profile").acquire()
        evidence = excinfo.value.owner_evidence
        assert evidence is not None
        assert evidence.pid == os.getpid()
        raw_token = first.owner_metadata["owner_token"]
        assert raw_token not in evidence.owner_token_identity
        assert evidence.profile_identity != first.owner_metadata["profile_name"]
    finally:
        first.release()


def test_cross_process_offset1_read_with_kernel_lock_held(tmp_path: Path) -> None:
    """S08 (in-process simulation with a REAL kernel lock on byte 0): when the
    registry is bypassed, the contender reads metadata from offset 1 of the
    still-locked file. On Windows this only works because byte 0 is reserved."""
    first = ProfileLease(tmp_path / "profile").acquire()
    canonical = profile_lease._canonicalize(tmp_path / "profile")
    profile_lease._registry.pop(canonical, None)
    try:
        with pytest.raises(ProfileLockedError) as excinfo:
            ProfileLease(tmp_path / "profile").acquire()
        evidence = excinfo.value.owner_evidence
        assert evidence is not None
        assert evidence.pid == os.getpid()
    finally:
        profile_lease._registry[canonical] = first
        first.release()


def test_legacy_byte0_metadata_reports_unavailable(tmp_path: Path) -> None:
    """Pre-v1 files wrote JSON at byte 0 (inside the locked region): the
    contender degrades to evidence=None — never a capture failure (S08)."""
    import json

    lease = ProfileLease(tmp_path / "profile")
    lease.lock_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = json.dumps(
        {"pid": 12345, "process_start_time": 1.0, "profile_name": "p", "owner_token": "t"}
    ).encode()
    fd = os.open(lease.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, legacy)
        os.lseek(fd, 0, os.SEEK_SET)
        profile_lease._lock_nonblocking(fd)
        try:
            with pytest.raises(ProfileLockedError) as excinfo:
                ProfileLease(tmp_path / "profile").acquire()
            assert excinfo.value.owner_evidence is None
        finally:
            profile_lease._unlock(fd)
    finally:
        os.close(fd)


def test_stale_metadata_never_triggers_reclaim_or_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S09: hostile/stale metadata while the kernel lock rejects acquisition —
    no os.kill, no unlink, no rename; the kernel lock stays authoritative."""
    import json

    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("metadata must never authorize destructive action")

    monkeypatch.setattr(os, "kill", _forbidden)
    monkeypatch.setattr(os, "unlink", _forbidden)
    monkeypatch.setattr(os, "rename", _forbidden)

    lease = ProfileLease(tmp_path / "profile")
    lease.lock_path.parent.mkdir(parents=True, exist_ok=True)
    hostile = (
        b"\0"
        + json.dumps(
            {
                "version": 1,
                "pid": 99999999,
                "process_start_time": -1.0,
                "profile_name": "x" * 300,
                "owner_token": "junk",
            }
        ).encode()
    )
    fd = os.open(lease.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, hostile)
        os.lseek(fd, 0, os.SEEK_SET)
        profile_lease._lock_nonblocking(fd)
        try:
            with pytest.raises(ProfileLockedError):
                ProfileLease(tmp_path / "profile").acquire()
            assert lease.lock_path.exists()
        finally:
            profile_lease._unlock(fd)
    finally:
        os.close(fd)


def test_owner_evidence_absent_from_problem_details_and_payloads(tmp_path: Path) -> None:
    """§6.4: evidence rides a private attribute only — RFC 9457 problem
    details, CLI JSON, and str() never carry owner values, lock paths, or
    profile paths."""
    import json

    from gflow_cli.json_output import error_payload

    first = ProfileLease(tmp_path / "profile").acquire()
    try:
        with pytest.raises(ProfileLockedError) as excinfo:
            ProfileLease(tmp_path / "profile").acquire()
        exc = excinfo.value
        assert exc.owner_evidence is not None
        problem_blob = json.dumps(exc.to_problem_details())
        payload_blob = json.dumps(error_payload(exc))
        raw_token = first.owner_metadata["owner_token"]
        evidence = exc.owner_evidence
        for blob in (problem_blob, payload_blob):
            assert raw_token not in blob
            assert evidence.owner_token_identity not in blob
            assert evidence.profile_identity not in blob
            assert str(first.lock_path) not in blob
    finally:
        first.release()


# ---------------------------------------------------------------------------
# #478: opt-in bounded wait (cooperative handoff for per-task holders)
# ---------------------------------------------------------------------------
#
# Triage evidence: every gflow holder (CLI command, daemon queue task —
# worker/daemon.py opens FlowApiClient per task) releases the lease at its
# natural end. The holder's "safe point" therefore coincides with release, so
# the cooperative handoff for THIS architecture is waiter-side: an opt-in
# bounded wait (GFLOW_CLI_LEASE_WAIT_SECONDS, default 0 = fail fast). A
# release-request channel / minimum-hold window would have no consumer:
# holders are never asked to release early, which also satisfies the issue's
# no-release-while-a-call-is-in-flight requirement by construction.


class TestLeaseBoundedWait:
    def _simulate_other_process_holder(self, profile: Path) -> ProfileLease:
        """Acquire and drop the registry entry so the holder looks like another
        OS process (kernel fd stays open) — same trick as the tests above."""
        holder = ProfileLease(profile).acquire()
        canonical = profile_lease._canonicalize(profile)
        profile_lease._registry.pop(canonical, None)
        return holder

    def test_default_stays_fail_fast(self, tmp_path: Path) -> None:
        """No env opt-in: kernel contention raises immediately (no polling)."""
        import time as _time

        holder = self._simulate_other_process_holder(tmp_path / "profile")
        try:
            start = _time.monotonic()
            with pytest.raises(ProfileLockedError):
                ProfileLease(tmp_path / "profile").acquire()
            assert _time.monotonic() - start < 0.4
        finally:
            holder.release()

    def test_waiter_acquires_after_holder_releases(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Opt-in wait: the waiter polls and takes over once the (simulated)
        other-process holder releases inside the window."""
        import threading
        import time as _time

        from gflow_cli.config import reset_settings

        monkeypatch.setenv("GFLOW_CLI_LEASE_WAIT_SECONDS", "10")
        reset_settings()
        holder = self._simulate_other_process_holder(tmp_path / "profile")
        timer = threading.Timer(0.6, holder.release)
        timer.start()
        try:
            start = _time.monotonic()
            waiter = ProfileLease(tmp_path / "profile").acquire()
            elapsed = _time.monotonic() - start
            waiter.release()
            assert elapsed >= 0.4  # actually waited
            assert elapsed < 8  # took over well before the deadline
        finally:
            timer.cancel()
            holder.release()  # idempotent if the timer already released

    def test_wait_times_out_with_profile_locked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Holder outlives the window -> same ProfileLockedError as fail-fast."""
        import time as _time

        from gflow_cli.config import reset_settings

        monkeypatch.setenv("GFLOW_CLI_LEASE_WAIT_SECONDS", "0.9")
        reset_settings()
        holder = self._simulate_other_process_holder(tmp_path / "profile")
        try:
            start = _time.monotonic()
            with pytest.raises(ProfileLockedError):
                ProfileLease(tmp_path / "profile").acquire()
            assert _time.monotonic() - start >= 0.8
        finally:
            holder.release()

    def test_same_process_contention_never_waits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Registry (same-process) contention stays fail-fast even with the
        wait enabled — waiting on a lease this process holds would deadlock."""
        import time as _time

        from gflow_cli.config import reset_settings

        monkeypatch.setenv("GFLOW_CLI_LEASE_WAIT_SECONDS", "10")
        reset_settings()
        with ProfileLease(tmp_path / "profile"):
            start = _time.monotonic()
            with pytest.raises(ProfileLockedError):
                ProfileLease(tmp_path / "profile").acquire()
            assert _time.monotonic() - start < 0.4

    @pytest.mark.asyncio
    async def test_async_wait_does_not_block_event_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """aacquire must poll with asyncio.sleep: the holder here is released
        by a `call_later` callback on the SAME event loop, so the waiter can
        only ever acquire if the loop keeps running during the wait — a
        blocking time.sleep poll would deadlock into the timeout instead."""
        import asyncio

        from gflow_cli.config import reset_settings

        monkeypatch.setenv("GFLOW_CLI_LEASE_WAIT_SECONDS", "5")
        reset_settings()
        holder = self._simulate_other_process_holder(tmp_path / "profile")
        loop = asyncio.get_running_loop()
        handle = loop.call_later(0.4, holder.release)
        try:
            waiter = await ProfileLease(tmp_path / "profile").aacquire()
            waiter.release()
        finally:
            handle.cancel()
            holder.release()
