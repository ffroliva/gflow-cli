# Plan 2 — Gflow Studio Scaffold (separate Tauri/React repo)

**Status:** Proposed (depends on Plan 1 — the REST `/api/v1` layer — for its primary data surface; can begin scaffolding against mocks in parallel).

Stands up **Gflow Studio**, a desktop filmmaking client, as an **independent sibling repo** (`gflow-studio`) — deliberately *not* inside `gflow-cli` (no Node/Vite/FFmpeg in the Python package; see `DESIGN.md` §1.2). Studio is a thin, beautiful **view + director's chair** over the daemon; all browser automation, auth, and generation stay server-side.

North star: **edit videos like a filmmaker.** Timeline + viewport are the center of gravity; asset/profile/project management is the toolbox around them.

---

## 1. Architecture (three surfaces, one daemon)

```
Gflow Studio (Tauri shell + React/TS)
  ├── REST /api/v1     → actions + CRUD (generate, scenes, tasks, assets)   [Plan 1]
  ├── MCP-SSE /mcp     → live agent/log stream, IDE-parity tool calls       [PR #201]
  └── Tauri→Rust→SQLite (WAL, read-only) → hot gallery/roster reads, <10ms  [DESIGN §3]
FFmpeg sidecar (Tauri) → local preview splicing / scrubbing (no credits)
```

Studio **never** launches a browser or touches Chrome profiles — generation is always `POST /api/v1/generate` → daemon worker (DESIGN §3.2 / SCENARIO D5).

---

## 2. Tech stack

- **Tauri 2** (Rust shell, small binary, native FS + sidecar) · **React 18 + TypeScript + Vite**.
- **State:** Zustand (or Redux Toolkit) + React Query for server cache.
- **Styling:** CSS variables design tokens from `STUDIO_DESIGN.md` §1.1 (dark, glassmorphic), `framer-motion` for spring drags.
- **API client:** generated from the daemon's `/api/v1/openapi.json` (Plan 1 T3.2) — zero hand-written drift.
- **Direct DB:** `rusqlite` in a Tauri command (`fetch_assets`, `fetch_characters`) opened `READ_ONLY | WAL`, `busy_timeout=5000` (DESIGN §3.1).

---

## 3. Phased Sequence

### Phase 0 — Repo & shell
- **T0.1** Create `gflow-studio` repo (sibling to `gflow-cli`). Tauri 2 + React/TS/Vite template. CI: lint + typecheck + build.
- **T0.2** Design-token stylesheet (`tokens.css`) + theme provider from `STUDIO_DESIGN.md`. App shell: 3-pane layout (Asset Navigator · Viewport · Timeline).
- **T0.3** Strict TS contracts (`AssetRecord`, `TimelineClip`, `TaskOut` …) — generated from OpenAPI; never hand-edited.

### Phase 1 — Data plane (read-only, no generation)
- **T1.1** API client module: REST base (`http://127.0.0.1:8000/api/v1`), token header, React Query hooks (`useAssets`, `useProjects`, `useProfiles`, `useTasks`).
- **T1.2** Tauri Rust command `fetch_assets(db_path)` (WAL read-only) for fast gallery; choose REST-vs-direct per surface (hot lists → direct, mutations → REST).
- **T1.3** **Asset Gallery** with virtualization (`react-virtualized`/intersection hook) — thousands of thumbnails at 60fps (DESIGN §2.3). Filter/search bar wired to `GET /assets`.
- **T1.4** **Profile & Project panels** — switch active profile, see session-valid + credits-today, browse projects. Read-only first.
- **T1.5** Error boundaries per pane (`ComponentBoundary`, DESIGN §2.1) so a bad asset never crashes the timeline.

### Phase 2 — Viewport & Timeline (the filmmaker core)
- **T2.1** Viewport video player (canvas/`<video>`), transport controls, frame scrub.
- **T2.2** Timeline editor: Video/Audio/Caption tracks, draggable clips with spring physics, playhead, trim handles (maps to scene `startTime/endTime`).
- **T2.3** Scene compose → `POST /api/v1/scenes` + `POST /scenes/{id}/render` (server-side concat, credit-free); poll/stream task status.
- **T2.4** FFmpeg sidecar for *local* preview splicing/scrub proxies (DESIGN §3.2) — parse stdout progress → React progress bar. Local-only; final renders stay server-side.

### Phase 3 — Generation & live feedback
- **T3.1** Generation panel: prompt + model/aspect/seed pickers → `POST /api/v1/generate` → `202 task_id`.
- **T3.2** Task dashboard: subscribe to `GET /api/v1/tasks/{id}/events` (SSE, Plan 1 T2.3) or `/mcp/sse` for live Playwright logs in a status window (STUDIO_DESIGN §4).
- **T3.3** New asset auto-lands in gallery on task completion (invalidate React Query / DB watch).
- **T3.4** Character roster + reuse (entity attach) surfaced for movie-consistency workflows.

### Phase 4 — Polish & package
- **T4.1** Micro-animations pass (hover/click scales, transitions — STUDIO_DESIGN §1.2).
- **T4.2** Empty/error/loading states; keyboard shortcuts (J/K/L scrub, space play).
- **T4.3** Tauri bundle (Windows-first; macOS/Linux follow); bundle FFmpeg sidecar; signed installer.
- **T4.4** `README` + screenshots; document "start the daemon (`gflow serve`), then launch Studio".

---

## 4. Cross-cutting (from SCENARIO.md)

- **Single-writer respect:** Studio issues *zero* browser subprocesses; all generation via REST→queue (D5).
- **WAL discipline:** every direct-read connection sets `busy_timeout=5000`, opens read-only (D6).
- **SSE resilience:** socket drop mid-generation → task continues server-side; reconnect rehydrates from `gflow.db` (D9).
- **Cross-platform:** Windows-first (user's env); validate Tauri signal/path behavior vs POSIX (D8).

---

## 5. Definition of Done (MVP)

- [ ] `gflow-studio` repo builds + packages a Windows desktop binary.
- [ ] Connects to a running `gflow serve`; gallery, projects, profiles render from live data.
- [ ] Timeline composes a scene and triggers a credit-free server-side render; result plays in viewport.
- [ ] A generation submitted from Studio enqueues, runs on the daemon, and the new asset auto-appears.
- [ ] No browser/Chrome process is ever spawned by Studio (verified).
- [ ] Error boundaries isolate a corrupted asset without crashing the app.

---

## 6. Open questions (resolve before Phase 0)

1. **Repo location** — new top-level `gflow-studio/` sibling, or a GitHub repo under the same owner? (DESIGN says separate codebase.)
2. **Auth surface for Studio** — local `GFLOW_DAEMON_TOKEN` only, or a richer local session? (MVP: token in app settings.)
3. **Direct-SQLite vs REST default** — confirm which surfaces go direct (proposed: read-heavy gallery/roster direct; everything else REST).
4. **Distribution** — personal use first, or public release with signed installers + auto-update?
