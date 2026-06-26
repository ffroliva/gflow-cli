# Roadmap

> Themes, not deadlines. `gflow-cli` is an unofficial CLI tracking a private Google Flow API — a single upstream UI change can rearrange a sprint, so we publish themed milestones with deliverables and let velocity emerge.

## v0.9.0 — Maturity & Visibility (current)

The release that makes the underlying data layer visible to the user. Adds `gflow data list` for read-only catalog browsing, publishes this roadmap, wires sponsorship, and refreshes the documentation surface to reflect everything that shipped through v0.8.x.

- Local SQLite catalog (shipped in PR #58, surfaced here)
- `gflow video t2v` model picker (`omni-flash` / `veo-lite` / `veo-fast` / `veo-quality` / `veo-lite-lp`)
- `gflow video i2v` — image-to-video with optional end frame
- `gflow video r2v` — reference-to-video (Flow ingredients)
- `gflow data list {projects,images,videos,profiles}` — read-only catalog query
- Locale-agnostic media-dialog selectors (fixes non-English Chrome profiles)
- `ROADMAP.md` (sponsorship wiring deferred to a follow-up patch release)

## v0.10.0 — Data Query Surface

Extends the data layer surface from read-only listing to inspection and selective export. Same local SQLite catalog, richer ways to interrogate it.

- `gflow data show <media_id>` — full record for one image / video / project
- `gflow data search` — filter by prompt substring, model, aspect, date range
- `gflow data export` — JSON / CSV / TSV
- `gflow data prune` — retention controls (`--older-than`, `--keep-last-n`)

## v0.11.0 — Local Studio, Background Worker & MCP SSE Service (in progress / develop)

The local Web UI Filmmaking Studio, background task worker, and MCP HTTP/SSE service are integrated into a single unified daemon interface under `gflow serve`.

- [x] **Uvicorn Daemon:** `gflow serve` starts the local FastAPI/Uvicorn server with background task loop.
- [x] **MCP SSE Server:** Exposes the MCP server over HTTP/SSE, allowing IDE clients and external tools to run JSON-RPC commands.
- [x] **Flow Worker Daemon:** Local queue manager and `FlowWorker` background task processor reading from SQLite queue tables and managing sequential profile-locked generation runs.
- [ ] **REST & Static UI:** FastAPI endpoints for asset management and hosting of the Filmmaking Studio (single-page app).
- [ ] **Aggregated Management:** Full view of accounts, profiles, projects, and active storyboarding queues.

## v1.0.0 — Stable API

The point at which `FlowApiClient` carries a SemVer commitment and the documentation reaches "production-ready" standard.

- `FlowApiClient` SemVer commitment
- HTTP transport revival path (community-contrib; if feasible)
- Production-ready documentation

---

*Themes, not deadlines. An unofficial CLI tracking a private API can have its sprint broken by a single UI change upstream — fixed dates would be dishonest.*
