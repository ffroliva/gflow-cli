---
name: data-layer-test-pollution-trap
description: "Test fixtures write to the user's real ~/AppData/Local/.../gflow.db catalog DB on dev machines; surfaced 2026-05-26 with 100% pollution"
---

On 2026-05-26 the user's production catalog at `<GFLOW_CLI_HOME>/gflow.db` was found to contain **only pytest fixture data** — every row pointed to `pytest-of-ffrol\pytest-N\test_*` tmp paths. No real provenance was present despite many real generations having been run.

**Cause:** tests instantiate `DataStore` / `OperationRecorder` without overriding the resolved DB path. `Settings.resolved_db_path()` falls back to `<GFLOW_CLI_HOME>/gflow.db`, which is the user's real catalog on a dev machine. Tests that don't `monkeypatch.setenv("GFLOW_CLI_DB_PATH", ...)` BEFORE `get_settings()` is cached leak writes to the production DB.

**Filed as [#86](https://github.com/ffroliva/gflow-cli/issues/86) — RESOLVED, closed 2026-05-26.** Fix: the autouse `_isolate_settings` fixture now lives in `tests/conftest.py`, redirecting `GFLOW_CLI_HOME` + `GFLOW_CLI_DB_PATH` to per-test tmp dirs and calling `reset_settings()` before/after every test. (No separate `platformdirs` tripwire was added; the autouse redirect alone closed the leak.) **Inverse trap:** that same fixture silently sandboxes tests that NEED the real home — see [[test-isolation-real-env-opt-out]].

**Cleanup performed 2026-05-26:** moved `gflow.db` aside as `gflow.db.backup-2026-05-26-pre-clean`. CLI re-creates a fresh DB on next invocation. Cleanup itself surfaced [#88](https://github.com/ffroliva/gflow-cli/issues/88) — `data list` crashed on the empty DB because `_safe_db` skipped migrations. Fixed in PR #89.

**Why:** dev-machine pollution masked real data, made `data list` output meaningless for actual users, and risked schema migrations running against the user's real DB during test runs.

**How to apply:**
- When writing any new data-layer test, EXPLICITLY set `GFLOW_CLI_DB_PATH` to a `tmp_path / "gflow.db"` AND call `reset_settings()` after the env-var change. See `tests/cli/test_cli_data.py` for the working pattern.
- If you're running pytest locally and see real-looking media IDs in your DB later, check `local_files.path` for `pytest-of-` prefixes — that's the symptom.

Related:
- [[data-layer-overview]] — data layer module map
- [[data-layer-v0.9.0-bugs]] — PR #78's DB path drift + duration fixes
- [[windows-dev-quirks]] — `GFLOW_CLI_HOME` is under `AppData\Local\ffroliva\gflow-cli` on Windows
