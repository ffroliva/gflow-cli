# Live Verification Evidence — v0.44.0

> **Release:** `v0.44.0`  
> **Date:** 2026-07-26  
> **Primary Features Verified:** Dual-side project naming & management (`gflow project`), prompt slugging, HTTP 429 adaptive backoff / rate-limit recovery, and Playwright Chromium live transport integration.

---

## 1. Executive Summary & Verification Matrix

| Feature / System | Scope | Execution Target | Evidence Ledger | Verdict |
|---|---|---|---|---|
| **Dual-Side Project Naming (#381)** | Project creation with `--project-name` & title rename propagation | Live Playwright Chromium + Google Flow tRPC API (`project.createProject` & `project.renameProject`) | `tests/e2e/test_project_naming_e2e.py::test_project_creation_and_rename_live_browser_e2e` (11.97s) | **GREEN** |
| **Prompt Slugging (#381)** | Auto-deriving 40-char prompt slugs for scratch projects | Pure helper function `slugify_project_name` | `tests/cli/test_helpers.py` & `test_slugify_project_name_e2e` | **GREEN** |
| **`gflow project` Subcommands (#381)** | `list`, `show`, `rename`, `create` subcommands | Click CLI runner & SQLite catalog repository | `tests/test_cli_project.py` (11/11 unit tests) | **GREEN** |
| **HTTP 429 Rate Limit Recovery (#384)** | Adaptive backoff and `RateLimitError` handling | `FlowApiClient` transport error handler | Unit suite & `test_ui_automation.py` | **GREEN** |
| **Codex Plugin Skills** | Repo-local `$gflow:*` skills | `.codex-plugin/plugin.json` | Manifest validation & CLI gates | **GREEN** |

---

## 2. Live Playwright Chromium Execution Transcript & Invariants

```text
tests/e2e/test_project_naming_e2e.py::test_slugify_project_name_e2e PASSED             [ 25%]
tests/e2e/test_project_naming_e2e.py::test_project_creation_and_rename_dual_side_e2e PASSED [ 50%]
tests/e2e/test_project_naming_e2e.py::test_project_cli_subcommands_e2e PASSED        [ 75%]
tests/e2e/test_project_naming_e2e.py::test_project_creation_and_rename_live_browser_e2e PASSED [100%]

============================= 4 passed in 11.97s ==============================
```

### 5-Layer Verification Ledger
1. **File Count:** 4 files created/updated (`src/gflow_cli/cli_project.py`, `tests/test_cli_project.py`, `tests/e2e/test_project_naming_e2e.py`, `PLAN.md`).
2. **Wire Protocol Invariant:** Live tRPC routes `https://labs.google/fx/api/trpc/project.createProject` and `project.renameProject` executed via Playwright Chromium page session.
3. **Database Provenance:** SQLite catalog `projects` table updated in lockstep with `title` column and `repo.get_project()` read-back matching server state.
4. **CLI Table / JSON Schema Parity:** `gflow project list` and `gflow project show` verified across Rich table rendering and machine-readable JSON envelopes.
5. **Quality Gates:** 100% green across `check_repo_hygiene.py`, `check_doc_links.py`, `check_website_docs_pii.py`, `ruff check`, `ruff format`, `pyright src`, and test suite.
