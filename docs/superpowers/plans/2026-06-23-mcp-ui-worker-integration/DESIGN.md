# High-Level & Low-Level Design: Integrated Local Studio & Flow Worker

This document defines the High-Level Design (HLD) and Low-Level Design (LLD) to unify the **Local Filmmaking Studio (Web UI)**, the **Flow Worker (Queue Daemon)**, and the **MCP SSE (Server-Sent Events) Server** into a cohesive system.

---

## 1. High-Level Design (HLD)

The goal is to transition `gflow-cli` from a simple terminal tool into a local, self-contained **Creative Director Studio**. 

Instead of separate application lifecycles, the system uses a single core daemon that coordinates background rendering task queues, SQLite catalog synchronization, local user sessions, and both JSON-RPC (MCP) and REST communications.

### 1.1 System Context Diagram

```
                  ┌────────────────────────────────────────┐
                  │          External Interfaces           │
                  │  (Claude Desktop, Cursor, Browser)     │
                  └─────────┬────────────────────┬─────────┘
                            │                    │
                            │ MCP / Stdio        │ HTTP / REST / SSE
                            ▼                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        gflow Local Daemon                              │
│                                                                        │
│  ┌───────────────────────┐          ┌──────────────────────────────┐   │
│  │   MCP stdio Adapter   │          │     Uvicorn / FastAPI        │   │
│  └──────────┬────────────┘          ├──────────────────────────────┤   │
│             │                       │  - REST Endpoints            │   │
│             │                       │  - MCP SSE Endpoint          │   │
│             │                       │  - Static Asset Server       │   │
│             │                       └──────────────┬───────────────┘   │
│             │                                      │                   │
│             └─────────────────┐  ┌─────────────────┘                   │
│                               ▼  ▼                                     │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                       Application Core                          │   │
│  │  - FlowApiClient (Browser / Playwright automation)               │   │
│  │  - FlowWorker (Asynchronous Background Queue Daemon)            │   │
│  │  - PromptExpander (Gemini Flash Creative Director)              │   │
│  └────────────────────────────┬────────────────────────────────────┘   │
│                               │                                        │
│                               ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                          Data Layer                             │   │
│  │  - SQLite operations catalog (gflow.db)                         │   │
│  │  - Chrome profile contexts (profile_default/, profile_email/)   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Unifying MCP and Web UI through SSE
The MCP standard natively supports **Server-Sent Events (SSE)**. Under this design:
1. The local daemon runs a FastAPI server hosting the Filmmaking Studio (single-page React app).
2. The same FastAPI server exposes `/mcp/sse` (SSE connection endpoint) and `/mcp/message` (RPC client-to-server POST endpoint).
3. Local Web UI client scripts can communicate using standard MCP JSON-RPC, keeping the communication protocol uniform across IDE agents and local UI components.

### 1.3 Flow Worker Integration
The `google-flow-worker` engine is ported directly into the `gflow-cli` codebase as `src/gflow_cli/data/worker.py`. 
* **State Engine:** The worker polls a SQLite table (`generation_queue`) for pending items.
* **Resilience:** If a generation fails (e.g. anti-bot triggers or browser crashes), the worker updates the status code, logs the RFC 9457 error details, and applies exponential backoff before retrying.
* **Concurrency:** The worker runs a single-threaded queue loop per Chrome profile, preventing context collisions.

---

## 2. Low-Level Design (LLD)

### 2.1 Directory Structure Extensions
```
src/gflow_cli/
├── mcp/
│   ├── __init__.py
│   ├── server.py       # Exposes FastMCP instance (Stdio + SSE compatible)
│   ├── tools.py        # Maps MCP tools to FlowApiClient / FlowWorker
│   ├── prompts.py      # Prompts (formula expanders)
│   └── resources.py    # Resources (db/schema, docs/mcp-guide)
├── ui/
│   ├── __init__.py
│   ├── app.py          # FastAPI application definitions & routing
│   ├── server.py       # Uvicorn boot wrapper (gflow ui)
│   └── static/         # Compiled Web UI studio assets (HTML/JS/CSS)
└── worker/
    ├── __init__.py
    ├── daemon.py       # Background worker polling generation_queue
    └── queue.py        # Queue CRUD interfaces and state definitions
```

### 2.2 Database Schema Updates (generation_queue)
To manage background rendering without double billing, a new `generation_queue` table is added:

```sql
CREATE TABLE generation_queue (
    task_id TEXT PRIMARY KEY,
    profile_name TEXT NOT NULL,
    project_id TEXT,
    kind TEXT NOT NULL,         -- 'image' | 'video'
    prompt TEXT NOT NULL,
    aspect TEXT NOT NULL,
    options_json TEXT,          -- model, seed, reference assets, voices
    status TEXT NOT NULL,       -- 'pending' | 'processing' | 'succeeded' | 'failed'
    attempts INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    error_details_json TEXT     -- RFC 9457 structured error if failed
);
```

### 2.3 Interface Specifications

#### 2.3.1 Local FastAPI REST Endpoints

##### UI Client API
* `GET /api/v1/projects`: Retrieves projects from the sqlite database.
* `GET /api/v1/projects/{project_id}/assets`: Lists generated assets.
* `GET /api/v1/profiles`: Lists active Google profiles and auth status.
* `POST /api/v1/profiles/rename`: Renames active profiles.
* `POST /api/v1/queue/submit`: Appends a new generation task to the `generation_queue` table.
* `GET /api/v1/queue/status/{task_id}`: Inspects task status.

##### MCP Server Sent Events (SSE)
* `GET /mcp/sse`: Handshake endpoint. Returns SSE stream mapping client transport ID.
* `POST /mcp/message?transport_id={id}`: Receives client JSON-RPC requests (e.g. tool execution, prompt list, resource query).

#### 2.3.2 Flow Worker Loop (Pseudo-Code)
```python
class FlowWorker:
    def __init__(self, profile_name: str, db_path: str):
        self.profile_name = profile_name
        self.db = DataStore(db_path)
        self.lock = asyncio.Lock()

    async def start(self):
        while True:
            task = await self.db.get_next_pending_task(self.profile_name)
            if task:
                async with self.lock:
                    await self.process_task(task)
            else:
                await asyncio.sleep(5)  # Poll interval

    async def process_task(self, task: TaskDTO):
        await self.db.update_status(task.task_id, "processing")
        try:
            async with FlowApiClient(profile_name=self.profile_name) as client:
                if task.kind == "image":
                    result = await client.generate_image(...)
                elif task.kind == "video":
                    result = await client.generate_video(...)
                await self.db.update_status(task.task_id, "succeeded", result)
        except GFlowError as e:
            await self.db.log_failure(task.task_id, e.to_problem_details())
```

---

## 3. System Rules & Concurrency

### 3.1 Profile Locking (No Collisions)
Chromium profiles are locked at the OS level. Running the CLI, the Web UI, and the Background Worker concurrently on the same profile requires strict guards:
1. **Queue Serialization:** Background task workers are single-threaded per Chrome profile.
2. **File locks:** Before `FlowApiClient` initializes Playwright, it checks for a file-based lock `lockfile.lock` inside the Chrome user-data folder, waiting up to 60 seconds before throwing a `ConcurrencyError`.
3. **Pre-flight verification:** Read-only actions (like catalog browse or queue check) bypass Playwright entirely and read from SQLite, which supports concurrent WAL-mode queries.
