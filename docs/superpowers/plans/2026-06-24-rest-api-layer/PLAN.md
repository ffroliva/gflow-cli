# Plan 1 — REST `/api/v1` Layer (alongside MCP-SSE)

**Status:** Proposed (depends on PR #201 — the decoupled daemon — landing on `develop`).

Adds a versioned, OpenAPI-documented REST surface to the existing `gflow serve` daemon **without removing MCP-SSE**. Three surfaces coexist over one daemon and one `gflow.db`:

- **REST `/api/v1/*`** — the "clean REST API" Studio and external consumers drive (this plan).
- **MCP-SSE `/mcp/*`** — IDE agents (Cursor, Claude Desktop) — already shipped in PR #201.
- **Direct SQLite (WAL)** — Studio's hot-path reads (gallery, rosters) — no HTTP hop.

North star: **filmmaking**. Asset/profile/project CRUD is the supporting cast; the headline verbs are *generate*, *enqueue*, *compose scenes*, *track render status*.

---

## 1. Design Fundamentals

1. **Additive, zero-regression.** New `APIRouter(prefix="/api/v1")` mounted on the existing FastAPI app. `/mcp/*` routes untouched. No behavior change to the CLI or MCP tools.
2. **Reuse, don't re-implement.** REST handlers call the *same* `data.queries`, `DataStore`, and `worker.queue` functions the MCP tools already use. No duplicate DB controllers.
3. **Pydantic at the edge only.** Response models live in `src/gflow_cli/ui/schemas.py`, built from the existing frozen dataclasses (`AssetRecord`, `TimelineClip`, queue rows). Internal core stays dataclass-based.
4. **Same auth gate.** Reuse `GFLOW_DAEMON_TOKEN`; `127.0.0.1` bind by default; token required for non-local. One dependency (`require_token`) shared by REST + MCP.
5. **Generation is async, never inline.** `POST /api/v1/generate` enqueues into `generation_queue` and returns `202 + task_id`. The FlowWorker (PR #201) processes it. REST never drives Playwright directly — the daemon remains the single browser broker.
6. **OpenAPI is the contract.** FastAPI auto-serves `/api/v1/docs` + `/api/v1/openapi.json`. Studio's typed client is generated from it (no hand-written API types drift).
7. **Redaction everywhere.** Reuse `redact_metadata` in the request/response logging middleware; never log session tokens or signed URLs.

---

## 2. Phased Sequence

### Phase 1 — Read surface (zero-credit, safest first)

- **T1.1 — Schemas.** `src/gflow_cli/ui/schemas.py`: `AssetOut`, `ProjectOut`, `ProfileOut`, `TaskOut`, `SceneOut`, `Page[T]` (cursor/limit/offset envelope), `Problem` (RFC 9457).
- **T1.2 — Router skeleton + health.** `src/gflow_cli/ui/rest.py`: `APIRouter(prefix="/api/v1")`; `GET /health` (daemon up, db reachable, worker heartbeat, profile lock holder). Mount in `ui/app.py` *before* the SSE catch-all mount.
- **T1.3 — Assets.** `GET /assets` (filter: kind, model, aspect, project_id, prompt substring, date range; paginated) · `GET /assets/{asset_id}` · `GET /assets/{asset_id}/file` (stream local file w/ correct content-type; 404 if only `cloud_uri`) — all via `data.queries`.
- **T1.4 — Projects & Profiles.** `GET /projects`, `GET /projects/{id}` · `GET /profiles` (name, engine, session-valid bool, credits-today), `GET /profiles/{name}`. Profile session check reuses the daemon's existing verifier.
- **T1.5 — Tests.** `tests/ui/test_rest_read.py` — FastAPI `TestClient`, seeded in-memory `gflow.db`. Assert shapes, pagination, filters, 404s, content-types, token gate.

### Phase 2 — Write / generation surface

- **T2.1 — Enqueue.** `POST /api/v1/generate` (body: `task_type` t2i|i2v|t2v|r2v|character, `payload`, `profile`, `priority`). Validates → inserts `generation_queue` row → `202 {task_id, status:"pending"}`. Boundary validation (negative seeds, unsupported aspects) → `422 Problem`.
- **T2.2 — Task status.** `GET /api/v1/tasks` (filter by status/profile) · `GET /api/v1/tasks/{task_id}` (status, error_json as Problem, resulting `flow_media_id` / asset link). `DELETE /api/v1/tasks/{task_id}` (cancel if still pending).
- **T2.3 — Live task stream (optional).** `GET /api/v1/tasks/{task_id}/events` (SSE) for progress, so Studio gets push updates without polling. Bridges worker log events.
- **T2.4 — Scenes (filmmaker core).** `GET /api/v1/scenes`, `GET /api/v1/scenes/{id}`, `POST /api/v1/scenes` (compose clips → existing scene/Add-Clip path), `POST /api/v1/scenes/{id}/render` (server-side concat, credit-free). Reuses `cli_scene` / client scene helpers.
- **T2.5 — Tests.** `tests/ui/test_rest_generate.py` + `tests/ui/test_rest_scenes.py` — enqueue→status transitions with a mocked worker; validation failures; cancel path.

### Phase 3 — Hardening & contract gates

- **T3.1 — Middleware.** Request-id correlation, `redact_metadata` logging, uniform `Problem` exception handler (maps `GFlowError` subclasses → status + exit-code-aware detail).
- **T3.2 — OpenAPI polish.** Tags, summaries, examples; pin `openapi_version`; expose `/api/v1/docs`. Snapshot test: `tests/ui/test_openapi_contract.py` diffs `openapi.json` against a committed golden (catches accidental surface changes).
- **T3.3 — REST/queue parity test.** Assert every `task_type` accepted by `POST /generate` maps to a worker handler (mirror of the existing CLI/MCP symmetry test).
- **T3.4 — Docs.** `docs/REST_API.md` (endpoints, auth, examples, Problem catalogue) indexed in `docs/INDEX.md`; ROADMAP note.

---

## 3. New / touched files

```
src/gflow_cli/ui/
├── rest.py        # APIRouter(/api/v1) — assets, projects, profiles, generate, tasks, scenes, health
├── schemas.py     # Pydantic response models (edge only)
├── middleware.py  # request-id + redaction + Problem handler
└── app.py         # (edit) mount rest router before SSE catch-all
docs/REST_API.md
tests/ui/test_rest_read.py · test_rest_generate.py · test_rest_scenes.py · test_openapi_contract.py
```

No new runtime deps (FastAPI already in PR #201). `python-multipart` only if file upload endpoints are added later.

---

## 4. Definition of Done

- [ ] `/api/v1` read + write + scenes endpoints live; `/mcp/*` unchanged.
- [ ] `POST /generate` enqueues and the worker drains it; status observable via `GET /tasks/{id}`.
- [ ] Token gate enforced; redaction verified; uniform `Problem` errors.
- [ ] `/api/v1/openapi.json` stable (golden snapshot) and `/api/v1/docs` renders.
- [ ] All gates: `ruff`, `ruff format`, `uv run pyright src` (0), scoped pytest, `uv lock --check`.
- [ ] Live: `gflow serve` on an authed profile → `curl /api/v1/assets` returns catalog; `POST /generate` (1 Imagen credit) → asset appears in `GET /assets`.
- [ ] `docs/REST_API.md` written + indexed.
