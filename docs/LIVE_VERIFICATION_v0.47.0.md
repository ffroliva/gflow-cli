# Live Verification — v0.47.0

**Date:** 2026-08-01
**Features under test:**
1. MCP SDK 2.0.0 migration & dual-era protocol support ([#407](https://github.com/ffroliva/gflow-cli/pull/407))
2. Entity provenance recording on generation operations ([#402](https://github.com/ffroliva/gflow-cli/issues/402), PR [#408](https://github.com/ffroliva/gflow-cli/pull/408))
3. Dependency drift CI gate (`resolve-drift`)

**Verifier:** repo owner's Windows 11 workstation, profiles `ffroliva` and `pr389fresh2`
**Credit cost:** zero — live stdio MCP transport verified credit-free via JSON-RPC protocol initialization; entity provenance logic unit-verified across 121 data tests with 3 opt-in E2E tests added.

## 1. MCP 2.0.0 Migration & Dual-Era Protocol Verification (Credit-Free)

`gflow mcp run` driven over stdio subprocess transport with standard JSON-RPC 2.0 initialization protocol payload:

| Probe | Result |
|---|---|
| Protocol negotiation | Negotiated `2024-11-05` protocol version with `gflow-cli v0.47.0` |
| Capabilities response | `prompts`, `resources`, `tools` capabilities returned successfully |
| Stdio stdout isolation | `_redirect_stdout_to_stderr()` preserved JSON-RPC output cleanly |
| Cache hints | `ttl_ms=3600000` (1h) on list routes, `ttl_ms=300000` (5m) on `resources/read`, all `scope=private` |

## 2. Entity Provenance Recording (#402 / #408)

- Data layer persistence updated across `record_generated_images`, `record_started_video`, `_insert_fallback_video_operation`, and `record_failed_operation` in `src/gflow_cli/data/recorder.py`.
- Tool provenance and entity provenance composed in a single `set_operation_metadata` payload to prevent column overwriting.
- `tests/data/test_recorder.py` and `tests/data/test_failure_recording.py` passed **121 unit tests**.
- Opt-in live E2E gate created in `tests/e2e/test_entity_provenance_e2e.py` covering:
  - `test_t2i_entity_attach_records_provenance`
  - `test_i2i_entity_attach_records_provenance`
  - `test_rejected_entity_attach_still_records_provenance`

## 3. Dependency Drift CI Job (`resolve-drift`)

- Unpinned requirement installation with `--upgrade` validated locally in clean environment.
- Prevents breaking upstream package changes (e.g. `mcp 2.0.0` deleting `mcp.server.fastmcp`) from breaking fresh installations.

## 4. Ledger summary

| Layer | Evidence |
|---|---|
| File count | 121 unit tests passing in `tests/data`, 114 tests passing in `tests/mcp`/`tests/ui` |
| Protocol negotiation | `initialize` response over stdio returning `gflow-cli v0.47.0` |
| Provenance schema | `entity_ids` and `entity_names` persisted in `operations.metadata_json` |
| CI Quality Gates | 100% clean across `ruff`, `pyright`, `check_doc_links`, `check_website_docs_pii`, `check_repo_hygiene` |
