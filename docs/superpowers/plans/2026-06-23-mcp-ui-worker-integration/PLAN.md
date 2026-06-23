# Integrated Studio, Worker Daemon & MCP SSE Plan

> **For agentic workers:** Run `/gflow:status --feature mcp-ui-worker-integration` to find the
> next unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Implement the local Filmmaking Web Studio, port the `google-flow-worker` background queue loop, and support Server-Sent Events (SSE) for HTTP MCP integrations.

---

## File structure

### New files
```
src/gflow_cli/worker/
  daemon.py       # FlowWorker polling loop
  queue.py        # SQLite queue storage interface
src/gflow_cli/ui/
  app.py          # FastAPI application definitions & REST routes
  server.py       # Uvicorn server startup runner
tests/worker/test_daemon.py
  Unit/mock checks for background task processing
tests/ui/test_app.py
  Integration tests for REST and SSE endpoints
```

### Modified files
```
pyproject.toml
  Add fastapi, uvicorn, and sse-starlette to dependencies.
src/gflow_cli/cli.py
  Add CLI command `gflow ui` to run the FastAPI server.
src/gflow_cli/mcp/server.py
  Allow boot option in FastMCP to bind to SSE HTTP transport.
CHANGELOG.md
  Document upcoming Integrated Studio.
ROADMAP.md
  Update roadmap milestones.
```

---

## Task 1 — Add Studio Dependencies & Database Queue Table

**What:** Add FastAPI/Uvicorn dependencies and write database migration for queue schema.

**Files:**
- `pyproject.toml`
- `src/gflow_cli/data/migrations/0002_queue.sql` (or schema initialization SQL)

**Steps:**
- [ ] Add `fastapi`, `uvicorn`, and `sse-starlette` to dependencies in `pyproject.toml`.
- [ ] Run `uv sync` to update the lockfile.
- [ ] Add the `generation_queue` table creation to SQLite migrations.
- [ ] Write integration test verifying migration runs cleanly and schema parameters query.

---

## Task 2 — Implement Queue Storage & Flow Worker Daemon

**What:** Port the background queue loop and status updates.

**Files:**
- `src/gflow_cli/worker/queue.py`
- `src/gflow_cli/worker/daemon.py`
- `tests/worker/test_daemon.py`

**Steps:**
- [ ] Create `src/gflow_cli/worker/queue.py` containing SQLite queue queries.
- [ ] Create `src/gflow_cli/worker/daemon.py` executing the FlowWorker infinite poll loop.
- [ ] Enforce sequential execution locks per profile name.
- [ ] Log RFC 9457 error details in JSON column on generation failure.
- [ ] Write unit tests verifying queue additions, status polling, and error logging under mock client conditions.

---

## Task 3 — Expose FastAPI REST App & Static UI Server

**What:** Construct the local API and static file routes.

**Files:**
- `src/gflow_cli/ui/app.py`
- `tests/ui/test_app.py`

**Steps:**
- [ ] Create `src/gflow_cli/ui/app.py`.
- [ ] Implement REST endpoints: projects browse, profiles management, and queue submission.
- [ ] Implement static file server routes mounting compiled UI build folder `src/gflow_cli/ui/static/`.
- [ ] Write integration tests verifying REST client calls.

---

## Task 4 — Implement MCP SSE (Server-Sent Events) HTTP Transport

**What:** Wire the FastMCP instance into FastAPI SSE routes.

**Files:**
- `src/gflow_cli/mcp/server.py`
- `src/gflow_cli/ui/app.py`

**Steps:**
- [ ] Update `server.py` to support binding FastMCP routes to SSE.
- [ ] Expose `GET /mcp/sse` and `POST /mcp/message` inside `ui/app.py`.
- [ ] Write integration tests validating SSE JSON-RPC exchanges.

---

## Task 5 — Click CLI Command `gflow ui`

**What:** Define Click entrypoint to start the server.

**Files:**
- `src/gflow_cli/cli.py`
- `src/gflow_cli/ui/server.py`

**Steps:**
- [ ] Create `src/gflow_cli/ui/server.py` to initialize Uvicorn.
- [ ] Add Click command `gflow ui [--port PORT] [--host HOST] [--profile NAME]` in `cli.py`.
- [ ] Enforce file-based profile locks when booting the daemon.

---

## Task 6 — Roadmap & Documentation Updates

**What:** Sync project docs and verify repository hygiene.

**Files:**
- `ROADMAP.md`
- `CHANGELOG.md`
- `PLAN.md`

**Steps:**
- [ ] Refine `ROADMAP.md` to reflect the integrated Web UI, worker queue, and SSE milestones.
- [ ] Update root `PLAN.md` backlog to link this plan.
- [ ] Run `/gflow:check` to ensure the entire suite is green.

---

## Definition of done

- [ ] All task steps checked off.
- [ ] `/gflow:check` green.
- [ ] Uvicorn server starts cleanly with `gflow ui`.
- [ ] User can browse the local filmmaking dashboard on `127.0.0.1:8000` and submit background rendering jobs.
