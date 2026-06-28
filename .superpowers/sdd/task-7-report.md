# Task 7 Report: `gflow tools` CLI group

**Status:** DONE_WITH_CONCERNS
**Commit:** 1e8e5e0
**Tests:** 3 passed, 0 failed

## Files created/modified
- `src/gflow_cli/cli_tools.py` — new Click group `tools` with `list`, `show`, `run` subcommands
- `src/gflow_cli/cli.py` — added import + `main.add_command(_tools_group)`
- `tests/cli/test_cli_tools.py` — 3 tests per plan (with one fix noted below)

## Concern: test 3 deviated from plan's exact code

The plan's `test_tools_run_json_without_key_falls_back` used bare `CliRunner().invoke(...)` and `json.loads(result.output)`. This fails because:
- This project's Click version (8.3.3) has no `mix_stderr` parameter on CliRunner
- `main()` always calls `configure_logging()` which routes structlog to `sys.stderr` via `PrintLoggerFactory(file=sys.stderr)`
- CliRunner captures stderr into the same buffer as stdout, so the `prompt_expander_no_key` JSON log line precedes the payload in `result.output`, causing `json.loads` to raise `JSONDecodeError: Extra data`

**Fix applied:** added `install_log_capture` fixture (already in `tests/conftest.py`) and a `monkeypatch.setattr("gflow_cli.cli.configure_logging", lambda *_a, **_kw: None)` to keep structlog in LogCapture mode during the test. All assertions are unchanged.
