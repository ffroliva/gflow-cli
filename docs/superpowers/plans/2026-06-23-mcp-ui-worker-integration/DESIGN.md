# High-Level & Low-Level Design: Decoupled MCP Daemon & Flow Worker

This document defines the High-Level Design (HLD) and Low-Level Design (LLD) to decouple the **Visual Filmmaking Studio (Web/Desktop UI)** from the **gflow-cli engine**. 

Instead of embedding visual frontend assets and custom REST routers inside the Python CLI package, `gflow-cli` acts strictly as the headless backend engine. It exposes standard Model Context Protocol (MCP) tools, prompts, and resources over a local Server-Sent Events (SSE) HTTP stream. Independent frontend applications (e.g. built via Tauri/React or Electron) connect to this SSE stream as standard MCP clients and read the local SQLite database directly.

---

## 1. High-Level Design (HLD)

### 1.1 System Context Diagram

```
┌────────────────────────────────────────┐
│        Visual Filmmaking Studio        │
│  - React / TypeScript Timeline Editor  │
│  - Bundled FFmpeg / CapCut sidecars    │
│  - Excluded from gflow-cli codebase    │
└───────┬────────────────────────┬───────┘
        │                        │
        │ SQLite Read (WAL)      │ MCP JSON-RPC over SSE
        ▼                        ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        gflow Local Daemon                              │
│  (Triggered via: gflow serve)                                          │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    Uvicorn / FastAPI Server                     │   │
│  │  - Exposes FastMCP over SSE (/mcp/sse & /mcp/message)           │   │
│  │  - Excludes static UI assets or custom REST controllers         │   │
│  └────────────────────────────┬────────────────────────────────────┘   │
│                               │                                        │
│                               ▼                                        │
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
│  │  - SQLite operations catalog (gflow.db with WAL mode)           │   │
│  │  - Chrome profile contexts (profile_default/, profile_email/)   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Separation of Concerns & Decoupled Communication
*   **Separation of Concerns:** `gflow-cli` is kept free of node, npm, Vite, and system-level FFmpeg path resolutions. Python manages browser automation, session authentication, and database schemas. The frontend handles timelines, visual elements, and media splicing.
*   **Unified Interface (MCP over SSE):** The backend daemon exposes an MCP server over Server-Sent Events. The visual client uses this unified JSON-RPC interface to submit generations, meaning the frontend behaves identically to standard IDE agents (Cursor, Claude Desktop).
*   **Direct SQLite Read (WAL Mode):** Since SQLite is configured in WAL (Write-Ahead Logging) mode, the decoupled visual client reads catalog data (`gflow.db`) directly from disk for presentation (gallery, logs, character rosters), achieving sub-10ms read times without any HTTP API latency or duplication of database controllers.

---

## 2. Low-Level Design (LLD)

### 2.1 Directory Structure Extensions
```
src/gflow_cli/
├── mcp/
│   ├── __init__.py
│   ├── server.py       # Exposes FastMCP instance (Stdio & SSE compatible)
│   ├── tools.py        # Maps MCP tools to FlowApiClient / FlowWorker
│   ├── prompts.py      # Prompts (Gemini formulas, character constructors)
│   └── resources.py    # Resources (db/schema, docs/mcp-guide)
├── ui/
│   ├── __init__.py
│   ├── app.py          # FastAPI application wrapper exposing SSE routes
│   └── server.py       # Uvicorn boot wrapper (gflow serve)
└── worker/
    ├── __init__.py
    ├── daemon.py       # Background worker polling generation_queue
    └── queue.py        # Queue CRUD database interfaces
```

### 2.2 Interface Specifications

#### 2.2.1 MCP SSE Endpoints
The decoupled Web UI connects to these endpoints:
*   `GET /mcp/sse`: Handshake endpoint. Establishes the Server-Sent Events stream connection, assigning a client ID and returning the endpoint URI.
*   `POST /mcp/message?transport_id={id}`: Handles JSON-RPC requests (tool calls, prompt queries, resource reads) sent from the UI.

#### 2.2.2 Flow Worker Loop (Pseudo-Code)
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
```

---

## 3. Concurrency & DB Locking

### 3.1 SQLite WAL Concurrency
The SQLite database `gflow.db` is configured with `journal_mode=WAL` and `foreign_keys=ON`. This is critical for decoupled architectures:
1. **Unblocked Readers:** The decoupled UI app can run complex SQLite read queries concurrently while the background FlowWorker is writing new assets.
2. **Busy Timeout Handling:** Both the daemon and the decoupled UI client must configure:
   ```sql
   PRAGMA busy_timeout = 5000;
   ```
   This ensures that concurrent database modifications queue gracefully rather than throwing immediate `DatabaseLocked` exceptions.

### 3.2 Playwright Profile Serialization
Because Google Chrome profile locks restrict browser instances to a single OS process, the UI client must never launch browser-level subprocesses. Instead, it must post generation tasks as tool requests over MCP SSE. The local daemon worker serializes the requests internally using an `asyncio.Lock`, preventing browser context collisions.
