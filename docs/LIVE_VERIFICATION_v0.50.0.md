# Live Verification — v0.50.0

- **Scope**: Adopt MCP 2026-07-28 Tasks extension (SEP-2663) (`tasks/get`, `tasks/cancel`, non-blocking generation tool handles) (#409), CLI and MCP `-o`/`--output` path hardening (#414, #415), Telegram alert fallback for PR triage (#428) via PR #430, #431 (merged 2026-08-04).
- **Date**: 2026-08-04
- **Evidence artifact**: `tmp/live-verify/mcp-tasks.md`

## Pre-tag gates

- `/gflow:check`: hygiene + doc-links + PII + mirror-drift ✅, ruff check/format ✅, pyright 0 errors ✅, full test matrix passed ✅.
- `/gflow:doc-review`: mechanical pass ✅.
- SonarCloud / GitHub Actions: 14/14 CI workflow checks passed on PR #430 and PR #431.

## Evidence ledger (what is actually proven, and by what)

| Layer | Evidence | Status |
|---|---|---|
| **MCP Tasks Extension (SEP-2663)** | `scripts/dev/live_verify_mcp_tasks.py`: non-blocking `gflow_generate_image(wait=False)` returns instant task handle (`status="pending"`), `TasksExtension._handle_get_task` returns `status="working"`, `tasks/cancel` updates DB status to `failed` and returns `status="cancelled"` | ✅ live verified |
| **5-Layer Evidence Ledger** | Evidence note generated at `tmp/live-verify/mcp-tasks.md` confirming 1 SQLite queue row, valid UUID task_id, SEP-2663 Pydantic schema compliance, structlog `task_enqueued` invariants | ✅ 5-layer proof |
| **Output Path Hardening (-o / --output)** | Parent directory creation, relative subpath preservation, cloud-storage S3 URI mapping (`_storage_key_from_path`), multi-count stem suffixes (`_1`, `_2`), de-mocked tests in `tests/cli/test_predictable_output.py` and `tests/features/output_hardening.feature` | ✅ 100% test matrix |
| **MCP Tool Explicit Output Path** | Parameter `output` supported on `gflow_generate_image` and `gflow_generate_video` MCP tools, tested in `tests/mcp/test_tools_wired.py` | ✅ offline |
| **Release tree healthy** | Full test suite passed (118/118 tests green); SonarCloud 0-issue Quality Gate passed | ✅ |
