# Scenario: Decoupled Daemon & Flow Worker Queue

This document evaluates the decoupled UI daemon and background worker queue across the 12 structured failure dimensions of `gflow-cli`.

---

## 1. Coverage Map

| Dimension | Status | Reason |
|---|---|---|
| **D1 — Auth & session lifecycle** | **Active** | Tracks user profile directory concurrency and session expirations during queued generations. |
| **D2 — WAF / reCAPTCHA scoring** | **Active** | Re-using browser pages for sequential queued generation loops. |
| **D3 — Selector cascade drift** | Skipped | Handled natively by browser automation core; decoupled daemon only forwards parameters. |
| **D4 — Batch manifest & resume** | Skipped | Standard manifest runs remain in CLI; the daemon runs a sequential database queue. |
| **D5 — Concurrency & Page pool** | **Active** | Serializing concurrent incoming MCP requests using async locks to avoid browser context crashes. |
| **D6 — Data layer (SQLite / DataStore)** | **Active** | Audits database lock resolutions during parallel WAL reads from visual clients and worker writes. |
| **D7 — Error propagation & exit codes** | **Active** | Ensures queued task failures capture RFC 9457 Problem Details and write them to SQLite. |
| **D8 — Cross-platform paths** | **Active** | Audits Uvicorn and signal trapping differences between Windows (Pro/Ultra local systems) and POSIX. |
| **D9 — Transport edge cases** | **Active** | Analyzes SSE stream disconnects, handshake drops, and malformed JSON-RPC payloads. |
| **D10 — Headless vs headed environment** | **Active** | Audits daemon background startup states with and without visual screens. |
| **D11 — Input validation & boundary values**| **Active** | Validates malformed payload parameters inside database task records. |
| **D12 — Observability & logging** | **Active** | Audits log redactions and correlation ID tracking through the FastAPI logger. |

---

## 2. Scenario Table

| # | Dimension | Scenario | Severity | Expected Behaviour | Test Category |
|---|---|---|---|---|---|
| 1 | D1 Auth & Session | UI client runs a generation task but the profile session has expired | High | Task status transitions to `failed`. `error_json` stores a `SessionExpiredError` (exit code 10) with a remediation instruction to run `gflow auth login`. | Integration |
| 2 | D1 Auth & Session | User launches `gflow serve` on profile X, then runs manual CLI command on profile X concurrently | Critical | Manual CLI command exits fast with `ProfileLockedError` (exit code 11) because daemon holds the active `profile.lock`. | Integration |
| 3 | D5 Concurrency | Daemon receives multiple rapid MCP video generation requests | High | Tasks are successfully enqueued in `generation_queue` (status `pending`). The background worker pulls and processes them sequentially, serialized by an `asyncio.Lock`. | Unit |
| 4 | D6 Data Layer | UI client performs heavy select queries while daemon writes a completed task state | Medium | SQLite WAL mode allows concurrent reads without locking. Both connections respect `PRAGMA busy_timeout = 5000` to queue writes. | Integration |
| 5 | D7 Error Prop | Background worker fails due to an unexpected exception | High | Worker catches exception, maps it to an RFC 9457 JSON payload, updates task status to `failed`, and logs `data.persistence_failed_after_success`. | Unit |
| 6 | D8 Cross-platform | User issues Ctrl+C (`SIGINT`) to `gflow serve` on a Windows console | High | FastAPI/Uvicorn catches the signal, triggers the shutdown sequence, closes database connections, deletes `profile.lock`, and terminates cleanly. | Integration |
| 7 | D9 Transport | Client socket disconnects from `/mcp/sse` midway through a long-running generation | Medium | Generation task continues in the SQLite queue backend to completion. The next client handshake recovers the status from `gflow.db`. | E2E live |
| 8 | D9 Transport | UI client sends a malformed JSON-RPC tool payload over `/mcp/message` | Medium | Daemon returns a standard JSON-RPC `-32600` (Invalid Request) error response immediately. Task is not enqueued. | Integration |
| 9 | D11 Input Val | Database queue contains task payload with negative seeds or unsupported aspects | Medium | Input validation inside the worker raises `ValidationError` immediately before starting browser context. Task status is marked `failed`. | Unit |
| 10| D12 Observability | Log payload of incoming MCP request contains user credentials or signed URLs | High | The FastAPI request logger executes `redact_metadata` before sending logs to stdout/logs file. | Unit |

---

## 3. Must-Cover Before Merge (Critical + High)

1. **Sequential Queue Serialization (D5):** Verify that enqueuing multiple concurrent generations executes them one by one without triggering browser context crashes.
2. **Profile File Locking (D1):** Verify that the daemon holds an active profile filesystem lock, causing concurrent manual CLI runs to fail fast with exit code 11.
3. **OS Signal Termination (D8):** Verify that Ctrl+C terminates the daemon process cleanly, releasing database files and deleting filesystem locks.
4. **Task Crash Recovery (D6):** Verify that on daemon boot, any tasks left in a hung `processing` status are safely converted to `failed`.

---

## 4. Suggested BDD Scenarios (for `tests/features/`)

```gherkin
Feature: Headless Daemon and Queue Processing
  As a visual filmmaking client
  I want to submit generation requests to a queue
  So that they execute sequentially without profile collisions

  Scenario: Sequential generation processing under concurrent load
    Given the gflow serve daemon is running for profile "default"
    When the client sends 3 concurrent "generate_image" tool calls
    Then the database contains 3 task entries in "generation_queue"
    And the task entries execute sequentially
    And the client receives 3 completed event responses
```
