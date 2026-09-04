---
name: data-layer-v0.9.0-bugs
description: "Two latent data-layer bugs shipped in v0.9.0 (DB path drift, NULL-duration crash) — both fixed in PR"
---

Discovered 2026-05-25 while testing `gflow data list profile|videos|images` against the v0.9.0 install on Windows. Both shipped to PyPI in v0.9.0; fixed in PR #78 (merged to develop, will go out in v0.9.1).

## Bug 1 — Default DB path resolver mismatch (issue #79)

`gflow data list *` used `cli_data._db_path()` which called `platformdirs.user_data_dir("gflow-cli")` with default appauthor + hardcoded filename `data.db`. On Windows that resolved to `%LOCALAPPDATA%\gflow-cli\gflow-cli\data.db` (doubled appname). The recorder + `data media` used the canonical `Settings.resolved_db_path()` which resolved to `%LOCALAPPDATA%\ffroliva\gflow-cli\gflow.db`.

**Symptom:** every `data list` subcommand crashed with `DataStoreError: Failed to open catalog ... unable to open database file` on a fresh install. Feature was unusable OOB on Windows.

**Workaround:** `set GFLOW_CLI_DB_PATH=%LOCALAPPDATA%\ffroliva\gflow-cli\gflow.db`.

**Fix:** `_db_path()` delegates to `Settings.resolved_db_path()` after the env-var fast path. The env-var direct lookup is preserved so `monkeypatch.setenv()` in tests still works against the cached `get_settings()` singleton (without this preservation, 2 tests fail because the lru_cache returns the pre-monkeypatch value).

## Bug 2 — `data list videos` crashes on NULL duration (issue #80)

`src/gflow_cli/data/queries.py:247` did `float(r["duration"])` with no NULL guard. `duration_seconds` is nullable (omni-flash t2v omits it; smoke fixtures insert rows without it). Every `data list videos` invocation crashed with `TypeError: float() argument must be a string or a real number, not 'NoneType'` once such a row existed.

**Fix:** `VideoRow.duration: float | None`; NULL guarded in `queries.list_videos` and in the Rich-table renderer (`f"{r.duration:g}s"` → `""` when None); `--json` emits `"duration": null`. Regression test `test_data_list_videos_null_duration` inserts a profile/project/video chain with `duration_seconds=None` via the public repository API and asserts both output modes exit 0.

## Why both shipped

Pre-release council didn't exercise `gflow data list videos` against a real catalog. The data-layer landing (PR #58) added the schema + recorder + queries together with the unit tests, but the unit tests used fixtures whose videos all have `duration_seconds=5.0`. The drift between `cli_data._db_path()` (added in #58 as a sibling resolver) and `Settings.resolved_db_path()` (canonical) never surfaced because both `data list` and `data media` tests use `GFLOW_CLI_DB_PATH=<tmp>` — the bug only appears when the env var is unset.

**How to apply:** when adding a new CLI subcommand that opens the data store, *always* delegate to `Settings.resolved_db_path()` — never call `platformdirs.user_data_dir(...)` directly. When adding a new dataclass row type for query results, audit every nullable column in the SQL against the dataclass field types (the schema's `REAL`/`TEXT` columns are NULL-allowing by default unless `NOT NULL` is declared).

Related: [[data-layer-overview]], [[exit-code-16-data-store]], [[full-test-suite-ooms]].
