# Production Readiness: Queue Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans (recommended). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent duplicate paid executions and preserve truthful task outcomes across upgrades, cancellation, crashes, and MCP/daemon races.

**Architecture:** Keep the existing queue repository and worker. Add an additive payload codec, transactional claims, checkpointed execution phases, and explicit `indeterminate` recovery. MCP and daemon share the claim API; the CLI is not routed through a command bus.

**Tech Stack:** SQLite transactions, typed request DTOs, structlog, pytest subprocess tests.

---

### Task 1: Capture real Flow handles before choosing retry semantics

**Files:** `src/gflow_cli/api/client.py`, `src/gflow_cli/api/dto.py`, `src/gflow_cli/worker/daemon.py`, new `tests/worker/test_queue_reconciliation.py`.

- [ ] **Step 1: Add a fake-transport probe**

```python
async def test_submit_checkpoint_receives_handles(fake_transport):
    observed = []
    await run_one_task(fake_transport, on_submit_attempt=observed.append)
    assert observed[0].phase == "submit_attempted"
    assert observed[0].handle is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/worker/test_queue_reconciliation.py -q`

Expected: FAIL because generation methods expose no submit-boundary checkpoint.

- [ ] **Step 3: Add the smallest typed callback seam**

Add a concrete callback at the existing generation boundary. Record only operation/workflow/media identifiers and phase; never prompts, headers, cookies, or signed URLs. Do not add a generic event bus.

- [ ] **Step 4: Verify and document observed contracts**

Run: `uv run pytest tests/worker/test_queue_reconciliation.py -q`. If handle-only polling requires a live wire observation, record the result in the live-verification document before selecting retry behavior.

- [ ] **Step 5: Commit**

```text
git add src/gflow_cli/api src/gflow_cli/worker tests/worker docs/superpowers/specs
git commit -m "test: capture generation reconciliation handles"
```

### Task 2: Add V1 codec with legacy decoding

**Files:** Create `src/gflow_cli/worker/codec.py`; modify `src/gflow_cli/worker/queue.py`; create `tests/worker/test_codec.py`.

- [ ] **Step 1: Write codec tests**

```python
def test_missing_schema_version_decodes_as_legacy_v0():
    assert decode_payload("t2i", {"prompt": "sunrise"}).schema_version == 0

def test_v1_round_trip_preserves_top_level_fields():
    payload = {"schema_version": 1, "prompt": "sunrise", "count": 1}
    assert encode_payload("t2i", decode_payload("t2i", payload)) == payload

def test_unknown_schema_version_fails_before_execution():
    with pytest.raises(QueueSchemaError):
        decode_payload("t2i", {"schema_version": 99, "prompt": "sunrise"})
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/worker/test_codec.py -q`

Expected: FAIL because no codec exists.

- [ ] **Step 3: Implement the additive codec**

Treat absent version as V0; keep fields top-level; validate task type, required request fields, enums, counts, and safe output paths. Raise a redacted RFC 9457-compatible queue schema error for unknown/malformed versions.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/worker/test_codec.py tests/worker/test_queue.py -q`

Expected: PASS with existing unversioned fixtures.

- [ ] **Step 5: Commit**

```text
git add src/gflow_cli/worker/codec.py src/gflow_cli/worker/queue.py tests/worker
git commit -m "feat: version worker queue payloads"
```

### Task 3: Add migration and atomic claims

**Files:** Create `src/gflow_cli/data/migrations/0009_queue_claims.sql`; modify `src/gflow_cli/worker/queue.py`; modify `tests/worker/test_queue.py`; create `tests/worker/test_queue_multiprocess.py`.

- [ ] **Step 1: Write claim tests**

```python
def test_claim_next_pending_changes_only_one_row(store):
    repo = QueueRepository(store)
    repo.enqueue_task("a", "profile", "t2i", {"prompt": "a"})
    assert repo.claim_next_pending("profile", claimant="one").task_id == "a"
    assert repo.claim_next_pending("profile", claimant="two") is None

def test_invalid_payload_fails_without_browser_launch(store, monkeypatch):
    repo = QueueRepository(store)
    repo.enqueue_task("bad", "profile", "t2i", {"schema_version": 99})
    monkeypatch.setattr("gflow_cli.worker.daemon.FlowApiClient", fail_if_called)
    assert repo.claim_next_pending("profile", claimant="one") is None
    assert repo.get_task("bad").status == "failed"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/worker/test_queue.py tests/worker/test_queue_multiprocess.py -q`

Expected: FAIL because selection and processing are separate and the schema has no claim/checkpoint columns.

- [ ] **Step 3: Add repository operations**

Extend the queue table with claimant metadata and redacted checkpoint JSON. Add `indeterminate` to the status check while preserving `completed`. Implement `claim_next_pending(profile, claimant)` and `claim_task(task_id, claimant)` in `BEGIN IMMEDIATE` transactions: select, decode, fail invalid payloads, or conditionally transition exactly one row to `processing`.

- [ ] **Step 4: Verify single and multiprocess claims**

Run: `uv run pytest tests/worker/test_queue.py tests/worker/test_queue_multiprocess.py -q`

Expected: PASS with exactly one winner across two processes.

- [ ] **Step 5: Commit**

```text
git add src/gflow_cli/data/migrations/0009_queue_claims.sql src/gflow_cli/worker/queue.py tests/worker
git commit -m "fix: claim generation tasks atomically"
```

### Task 4: Share claims between daemon and MCP

**Files:** `src/gflow_cli/worker/daemon.py`, `src/gflow_cli/mcp/tools.py`, `tests/worker/test_daemon.py`, `tests/mcp/test_tools.py`, new `tests/worker/test_mcp_daemon_race.py`.

- [ ] **Step 1: Write the race test**

```python
async def test_mcp_and_daemon_cannot_process_the_same_task(queue):
    first, second = await asyncio.gather(mcp_claim(queue), daemon_claim(queue))
    assert sorted([first is not None, second is not None]) == [False, True]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/worker/test_mcp_daemon_race.py -q`

Expected: FAIL because MCP currently processes immediately after enqueue while daemon can select the same pending row.

- [ ] **Step 3: Refactor callers**

Make daemon polling call `claim_next_pending()` and MCP call `claim_task()` for its new ID. Make `process_task()` accept only a claimed processing task. Remove duplicate MCP/worker generation lock dictionaries; the browser lease plan owns profile serialization.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/worker tests/mcp -q`

Expected: PASS, including schema/parity tests.

- [ ] **Step 5: Commit**

```text
git add src/gflow_cli/worker src/gflow_cli/mcp tests/worker tests/mcp
git commit -m "fix: share atomic queue claims across worker and MCP"
```

### Task 5: Persist cancellation and crash recovery

**Files:** `src/gflow_cli/worker/queue.py`, `src/gflow_cli/worker/daemon.py`, `src/gflow_cli/ui/app.py`, `tests/worker/test_queue_reconciliation.py`, `tests/e2e/test_daemon_e2e.py`.

- [ ] **Step 1: Write phase tests**

```python
def test_cancel_before_submit_is_safe_failure(repo):
    repo.update_checkpoint("task", phase="claimed", may_have_spent=False)
    repo.cancel_task("task")
    assert repo.get_task("task").status == "failed"

def test_cancel_after_submit_without_handle_is_indeterminate(repo):
    repo.update_checkpoint("task", phase="submit_attempted", may_have_spent=True)
    repo.cancel_task("task")
    assert repo.get_task("task").status == "indeterminate"

def test_restart_reconciles_handle_without_resubmitting(repo, fake_client):
    repo.update_checkpoint("task", phase="remote_started", handle="operations/1")
    recover_processing(repo, fake_client)
    assert fake_client.submit_count == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/worker/test_queue_reconciliation.py tests/e2e/test_daemon_e2e.py -q`

Expected: FAIL because startup marks every processing row failed and cancellation bypasses the failure funnel.

- [ ] **Step 3: Implement monotonic checkpoints**

Persist phase, `may_have_spent`, and non-secret handles. Catch `CancelledError`, persist the correct state, and re-raise. On startup classify by phase; reconcile only with a handle-only poll path, otherwise mark `indeterminate`. Never call generation during recovery.

- [ ] **Step 4: Make daemon E2E safe**

Remove unconditional profile-lock deletion, allocate a free localhost port, and wrap process cleanup in `try/finally`. Assert lease reacquisition after shutdown.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/worker/test_queue_reconciliation.py tests/e2e/test_daemon_e2e.py -q`

```text
git add src/gflow_cli/worker src/gflow_cli/ui/app.py tests/worker tests/e2e/test_daemon_e2e.py
git commit -m "fix: preserve uncertain queue outcomes"
```
