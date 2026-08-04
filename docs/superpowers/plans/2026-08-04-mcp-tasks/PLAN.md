# Implementation Plan: MCP Tasks Extension (SEP-2663) for #409

**Goal:** Implement the MCP 2026-07-28 Tasks extension (`tasks/get`, `tasks/cancel`) over the SQLite `generation_queue`, refactor MCP generation tools to return non-blocking task handles with optional blocking fallback, and wire `FlowWorker` into `gflow serve`.

---

## Tasks

- [ ] **Task 1: Implement `TasksExtension` Subclass**
  - **File:** `src/gflow_cli/mcp/tasks_extension.py`
  - **Details:** Subclass `mcp.server.extension.Extension`. Implement `tasks/get` (fetches `QueueTask` via `QueueRepository` and maps status) and `tasks/cancel` (updates task status to `failed` and releases `ProfileLease`).

- [ ] **Task 2: Update MCP Generation Tools to Return Task Handles**
  - **File:** `src/gflow_cli/mcp/tools.py`
  - **Details:** Update `gflow_generate_image` and `gflow_generate_video` to add `wait: bool = False`. Default to enqueuing task and returning `{ "task_id": ..., "status": "pending" }`. Retain blocking path when `wait=True`.

- [ ] **Task 3: Wire `FlowWorker` into `gflow serve` Server Loop**
  - **Files:** `src/gflow_cli/mcp/server.py`, `src/gflow_cli/cli.py`
  - **Details:** Connect `FlowWorker` background queue processor to start during `gflow serve` startup so enqueued tasks are claimed and executed.

- [ ] **Task 4: Register `TasksExtension` on `MCPServer`**
  - **File:** `src/gflow_cli/mcp/server.py`
  - **Details:** Register `TasksExtension` instance on `MCPServer`.

- [ ] **Task 5: Comprehensive Unit & Parity Tests**
  - **Files:** `tests/mcp/test_tasks_extension.py`, `tests/mcp/test_cli_parity.py`, `tests/features/test_mcp_tasks_steps.py`
  - **Details:** Unit tests for `tasks/get`, `tasks/cancel`, non-blocking enqueue, and CLI-MCP parity validation.

- [ ] **Task 6: Impeccable Routine Quality Gates & Documentation**
  - **Files:** `docs/MCP.md`, `website/docs/MCP.md`
  - **Details:** Run `/gflow:check` (hygiene, ruff, pyright, pytest) and document MCP Tasks extension usage in user guides.
