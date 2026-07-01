# Live Verification — v0.23.0

Release date: 2026-07-01. This document records what was exercised against **live
Google Flow** (credit-free wherever possible) for the user-facing changes in v0.23.0,
following the 5-layer verification ledger.

## Scope

| Change | Surface | Live-verified? |
|---|---|---|
| **#228** — MCP generation now functional (tool → FlowWorker → download → record; `tools` applied) | MCP `gflow_generate_image` / `gflow_generate_video` | 🟡 Wiring proven live (tool → worker → real Flow REST + structured-error translation); the final image-write layer was **not** run live (expired session — environmental) — see below |
| **#230 / #222** — macOS generation 401 fixed (path=/ cookie read + headed-context seed) | `auth/cookies.py`, `api/client.py` (macOS headed Chrome) | ✅ Reporter-verified end-to-end on macOS (Apple Silicon) |
| **#219** — Chromium-only-host chrome-channel gate + T2I extra-image guard | `browser_manager.py`, image path | ⚠️ Not reproducible on this maintainer box (Google Chrome is installed) — covered by automated tests |

## #228 — MCP generation path (credit-free image)

Exercised the **wired MCP tool path in-process** (`gflow_generate_image`, profile
`denon82`, aspect 1:1, count 1) — the exact path this release fixes (tool → queue →
`FlowWorker.process_task` → real `FlowApiClient` → real Flow REST).

**Proven live (structlog evidence, 2026-07-01):**
- `mcp.tool.profile_resolved requested=denon82 resolved=denon82` — profile resolution
- `mcp.tool.task_enqueued task_type=t2i` → `Processing task` — enqueue + worker dispatch
- `client.persistent_context_launch channel=chrome chrome_strategy_requested=True cookies_db_present=True password_store_basic=True` — real headed Chrome, chrome-strategy
- `client.context_cookie_state context_cookie_count=57 flow_session_cookie_present=True google_sapisid_present=True` — cookies loaded
- Reached a **real Flow REST call**: `POST project.createProject`
- **Structured-error translation confirmed** (the core #228 wiring): the server returned
  HTTP 401 → worker raised `AuthExpiredError` → the MCP tool returned a clean RFC-9457
  failure envelope:
  ```json
  {"status":"failed","error":{"type":".../auth-expired","title":"Authentication expired",
   "status":401,"detail":"HTTP 401","remediation_hint":"Run `gflow auth login ...`",
   "exit_code":3}}
  ```
  This exercises exactly the tool→worker→status-translation path the release wires, plus
  the "any non-`completed` status → failure" behavior added in this release.

**Not completed live (environmental, not a code defect):** the actual image creation +
download + `local_files` recording did not run because the `denon82` Flow session was
**server-side expired** (HTTP 401 on a fully-loaded cookie jar — a stale session token,
distinct from the #222 macOS decrypt bug). Completing the image-write layer requires an
interactive `gflow auth login --profile denon82` (browser login). The generation +
download + recording code beneath the MCP wiring is the **same shared FlowWorker /
FlowApiClient path** exercised live in prior releases (CLI image path) and by the
automated suite.

## #230 / #222 — macOS generation 401

Verified **end-to-end on macOS (Apple Silicon) by the issue reporter** (@gunalak), both
branches of the fix (PR #230 body evidence):
- **Seed fires** (headed decrypt loaded nothing): `context_cookie_count=0
  flow_session_cookie_present=False` → `preread_count=8 preread_session=True` →
  `context_cookies_seeded seeded_session=True` → `createProject` + `batchGenerateImages`
  **200** → image written.
- **Native decrypt** (seed is a no-op): `context_cookie_count=7
  flow_session_cookie_present=True` → **200** → image written.

Council review (`/gflow:pr-council-review` #230): correctness/security/auth/quality all
GREEN; tests/memory YELLOW (coverage-gap follow-up tracked).

## Automated coverage

- Full suite (this release tree): **1918 passed / 7 skipped** (pre-#230), plus **70
  passed** on the #230-affected suites (`tests/api/test_client_launch_kwargs.py`,
  `tests/auth/`) after the merge.
- `pyright src`: 0 errors. `ruff check` / `ruff format --check`: clean.
- New MCP-wiring regression tests (`tests/mcp/test_tools_wired.py`,
  `tests/worker/test_daemon.py`) assert the full enqueue → worker → status-translation
  path and the `tool_specs` application.

## Follow-ups

- MCP → worker → download/record has no gated live e2e harness yet (tracked): a
  `test_mcp_*_e2e.py` mirroring `test_daemon_e2e.py` (opt-in, credit-free image).
- Two #230 regression tests (falsifiable `/fx` cookie-path test + `_preread` capture
  test) tracked as a post-merge follow-up.
