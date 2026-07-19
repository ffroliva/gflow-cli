# Production Readiness: Browser Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans (recommended). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serialize persistent Chrome ownership safely across processes and make setup, cancellation, daemon shutdown, and auth fallback cleanup complete.

**Architecture:** A canonical-profile `ProfileLease` combines an asyncio lock with a stable OS advisory lock. The kernel lock is authoritative and fail-fast. Each component that owns a persistent context acquires/releases exactly once; callers passing an existing context do not reacquire.

**Tech Stack:** Python stdlib `msvcrt`/`fcntl`, asyncio, Playwright, FastAPI lifespan, pytest subprocess tests.

---

### Task 1: Define the lease contract

**Files:** Create `src/gflow_cli/profile_lease.py` and `tests/test_profile_lease.py`.

- [ ] **Step 1: Write failing tests**

```python
def test_same_process_second_acquire_raises_profile_locked(tmp_path):
    with ProfileLease(tmp_path / "profile"):
        with pytest.raises(ProfileLockedError):
            ProfileLease(tmp_path / "profile").acquire()

def test_release_allows_reacquire(tmp_path):
    lease = ProfileLease(tmp_path / "profile")
    lease.acquire()
    lease.release()
    ProfileLease(tmp_path / "profile").acquire().release()

def test_different_profiles_can_acquire_in_parallel(tmp_path):
    first = ProfileLease(tmp_path / "one").acquire()
    second = ProfileLease(tmp_path / "two").acquire()
    second.release()
    first.release()

def test_metadata_never_authorizes_kill_or_unlink(tmp_path):
    lease = ProfileLease(tmp_path / "profile").acquire()
    assert lease.owner_metadata["pid"] == os.getpid()
    assert lease.release_does_not_unlink_lock_file is True
    lease.release()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_profile_lease.py -q`

Expected: FAIL because the lease module does not exist.

- [ ] **Step 3: Implement the lease**

Resolve the profile path, derive a stable lock filename under the configured gflow lock directory, create one byte, and acquire nonblocking `msvcrt.locking` on Windows or `fcntl.flock` on POSIX. Store safe diagnostic metadata, never follow a symlink for the lock path, never unlink a held lock, and map contention to `ProfileLockedError`.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_profile_lease.py -q`

Expected: PASS on Windows.

- [ ] **Step 5: Commit**

```text
git add src/gflow_cli/profile_lease.py tests/test_profile_lease.py
git commit -m "feat: add cross-process profile lease"
```

### Task 2: Prove subprocess contention and crash release

**Files:** Create `tests/test_profile_lease_subprocess.py`; modify `.github/workflows/ci.yml`.

- [ ] **Step 1: Write subprocess tests**

```python
def test_holder_wins_and_second_process_fails_fast(tmp_path):
    holder = launch_holder(tmp_path / "profile")
    assert run_contender(tmp_path / "profile").exit_code == 11
    holder.terminate()

def test_process_exit_releases_kernel_lock(tmp_path):
    launch_holder(tmp_path / "profile").wait()
    assert run_contender(tmp_path / "profile").exit_code == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_profile_lease_subprocess.py -q`

Expected: FAIL until both processes use the same canonical lock file.

- [ ] **Step 3: Add an OS matrix job**

Run the focused subprocess tests on `windows-latest`, `macos-latest`, and `ubuntu-latest` while leaving the existing Ubuntu job unchanged.

- [ ] **Step 4: Run the local leg**

Run: `uv run pytest tests/test_profile_lease_subprocess.py -q`

Expected: PASS on Windows. Record remote macOS/Linux as pending until a push/PR is explicitly authorized.

- [ ] **Step 5: Commit**

```text
git add tests/test_profile_lease_subprocess.py .github/workflows/ci.yml
git commit -m "test: verify profile lease across processes"
```

### Task 3: Integrate ownership at every persistent-context boundary

**Files:** `src/gflow_cli/api/client.py`, `src/gflow_cli/api/transports/ui_automation.py`, `src/gflow_cli/auth/cookies.py`, `src/gflow_cli/auth/internal_chromium.py`, `src/gflow_cli/auth/verification.py`, `src/gflow_cli/auth/real_chrome.py`, experimental persistent transports, and their existing tests.

- [ ] **Step 1: Add failing tests**

```python
async def test_client_lease_wraps_persistent_context_launch(monkeypatch):
    events = []
    monkeypatch.setattr("gflow_cli.profile_lease.ProfileLease.acquire", lambda self: events.append("acquire"))
    monkeypatch.setattr("gflow_cli.profile_lease.ProfileLease.release", lambda self: events.append("release"))
    await open_client_and_close()
    assert events == ["acquire", "release"]

async def test_preinitialized_transport_does_not_acquire_a_second_lease(monkeypatch):
    calls = []
    monkeypatch.setattr("gflow_cli.profile_lease.ProfileLease.acquire", lambda self: calls.append(1))
    await open_client_with_preinitialized_transport_and_close()
    assert calls == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_client.py tests/api/transports/test_ui_automation.py tests/auth -q`

Expected: FAIL because persistent launch sites do not use one shared lease.

- [ ] **Step 3: Integrate acquisition/release**

Acquire before a component launches a persistent context, pass an ownership token into transports, and release only after context/browser/Playwright shutdown. Do not reacquire when a caller owns the context. Remove MCP/worker lock maps and the daemon’s ceremonial `profile.lock` file.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/api/test_client.py tests/api/transports/test_ui_automation.py tests/auth -q`

Expected: PASS with existing lifecycle ownership tests preserved.

- [ ] **Step 5: Commit**

```text
git add src/gflow_cli/api src/gflow_cli/auth src/gflow_cli/worker src/gflow_cli/mcp src/gflow_cli/ui tests
git commit -m "fix: enforce profile ownership at browser boundaries"
```

### Task 4: Make cancellation and teardown exception-safe

**Files:** `src/gflow_cli/api/_engine.py`, `src/gflow_cli/api/client.py`, `src/gflow_cli/api/transports/ui_automation.py`, `src/gflow_cli/auth/real_chrome.py`, `src/gflow_cli/ui/app.py`, focused client/auth/UI tests.

- [ ] **Step 1: Write cancellation tests**

```python
async def test_cancel_during_context_close_still_stops_playwright():
    session = await open_cancellable_session()
    task = asyncio.create_task(session.close())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.playwright_stopped is True

async def test_daemon_lifespan_releases_lease_after_worker_cancellation():
    async with running_daemon() as daemon:
        lease_path = daemon.lease_path
    assert ProfileLease(lease_path).try_acquire() is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_client.py tests/api/transports/test_ui_automation.py tests/auth/test_real_chrome.py tests/ui/test_app.py -q`

Expected: FAIL because cancellation can bypass later cleanup.

- [ ] **Step 3: Implement bounded cleanup**

Wrap lifespan shutdown in `try/finally`; cancel/await workers before browser closure; use bounded shielded cleanup for context, Playwright, child-process reaping, stores, and lease release; then re-raise the original cancellation.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/api/test_client.py tests/api/transports/test_ui_automation.py tests/auth/test_real_chrome.py tests/ui/test_app.py -q`

```text
git add src/gflow_cli/api src/gflow_cli/auth src/gflow_cli/ui tests
git commit -m "fix: complete browser cleanup under cancellation"
```
