# Usage Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local usage counters and `gflow data stats`, then default-on, opt-out weekly upload of allow-listed totals with no identifier.

**Architecture:** `telemetry.py` owns the local store, state and report. CLI and MCP each call it from one existing chokepoint. `telemetry_upload.py` carries the only network code, runs as a detached child process, and lands in a separate, gated PR.

**Tech Stack:** Python 3.11+, click 8.5, stdlib `sqlite3` and `urllib`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-usage-telemetry-design.md`

## Global Constraints

- Nothing here may raise into, print during, or noticeably slow a user command. The one exception: `gflow telemetry off` fails loudly if it cannot persist.
- Never write to stdout: MCP stdio and `--json` depend on it. Structured logging only, at debug level.
- No identifier of any kind. `report_id` is random, one per week, and never reused.
- Payload keys are exactly the spec's allow-list. Built field by field, never by filtering.
- Nothing is uploaded until the notice has been shown on that machine, and only weeks that started after it.
- Resolve the home with `_resolve_home()` (`config.py:134`), never `get_settings()`: telemetry must work when settings are invalid.
- No cached SQLite connection. A long-lived `gflow mcp run` pinned the WAL at 8.2 MB when measured.
- The notice states every use of the data: improving the tool, and aggregate figures shared publicly and with sponsors.
- No code path may signal an opt-out or count an opted-out install.
- Notice version constant: `NOTICE_VERSION = 1`. Payload schema constant: `SCHEMA = 1`.
- Run commands with `uv run`; `/gflow:check` before every commit; no AI attribution in commits.

## Review Focus

1. Read-only home, corrupt `telemetry.db`, or a locked file: the command behaves as if telemetry did not exist. Pinned in Task 1.
2. Four gflow processes counting at once (1 locked error in 400 when measured): a dropped count is fine, "database is locked" text is not. Pinned in Task 1.
3. A prompt that looks like a command or flag (`gflow video t2v "--ref image t2i"`): never becomes a key. Pinned in Task 2.
4. `gflow ... --json` and a stdio MCP call: stdout byte-identical with telemetry on and killed. Pinned in Tasks 2 and 3.
5. Laptop offline for months, a clock set in the future, or a fast command that exits at once: nothing lost, nothing sent twice, nothing older than the notice. Pinned in Task 8.

---

## Sequence and gates

| Step | What | Gate |
|---|---|---|
| 0 | Public RFC and poll | **Maintainer's explicit go** |
| PR 1 | Tasks 1-5, local only | RFC window closed, stop threshold not reached |
| PR 2 | Tasks 6-9, notice and upload | Counsel's review, EU answer, endpoint live, one release after PR 1 |

---

### Task 0: Public RFC

**Files:** none. Drafted in the scratchpad.

- [ ] **Step 1:** Draft from the spec: Why, the industry table, payload, never-list, the `SECURITY.md` change, what happens to weeks counted before the notice.
- [ ] **Step 2:** Maintainer sets a numeric stop threshold for the poll, in advance, and reads the draft.
- [ ] **Step 3:** On the maintainer's explicit go, post the issue and a Discussions poll, and link both from release notes.
- [ ] **Step 4:** Link the public issue from `ffroliva/oss-ops#1`. Use the public number in every later commit.

---

## PR 1: local only

### Task 1: Local store and kill switches

**Files:**
- Create: `src/gflow_cli/telemetry.py`
- Modify: `pyproject.toml` (declare `python-dotenv`, today only transitive through `pydantic-settings`), `uv.lock`, `tests/conftest.py:141`, `.env.template`, `docs/CONFIGURATION.md`
- Test: `tests/test_telemetry.py`

**Interfaces:**
- Produces: `killed() -> str | None` (returns `"GFLOW_CLI_TELEMETRY"`, `"DO_NOT_TRACK"` or `None`); `opted_out() -> bool`; `record(surface: Literal["cli", "mcp"], command: str, flags: Iterable[str], outcome: str, duration_s: float) -> None`; `read_counters(weeks: int | None = None) -> list[Counter]`; `iso_week(now: datetime | None = None) -> str`; `Counter(week, surface, kind, key, n)` frozen dataclass; `kind` is one of `command`, `flag`, `exit`, `duration`, `transition`, `client`, `crash`, `tool`.

- [ ] **Step 1: Write the failing tests**

```python
def test_record_counts_command_flag_exit_duration(): ...
    # record("cli", "video t2v", ["--ref"], "0", 12.0) twice:
    # command "video t2v" n=2, flag "video t2v --ref" n=2, exit "video t2v 0" n=2,
    # duration "video t2v <1m" n=2

@pytest.mark.parametrize("seconds,bucket", [(9.9, "<10s"), (10, "<1m"), (299, "<5m"), (300, "5m+")])
def test_duration_buckets(seconds, bucket): ...

@pytest.mark.parametrize("value,expected", [("1", "DO_NOT_TRACK"), ("true", "DO_NOT_TRACK"),
    ("0", None), ("false", None), ("", None)])
def test_do_not_track(value, expected, monkeypatch): ...

@pytest.mark.parametrize("value", ["0", "false", "off", "maybe"])
def test_gflow_cli_telemetry_turns_off(value, monkeypatch): ...

def test_a_bad_value_never_fails_a_command(monkeypatch): ...  # GFLOW_CLI_TELEMETRY=maybe; gflow --help exit 0
def test_either_dotenv_can_turn_off_and_neither_can_turn_on(tmp_home, tmp_cwd): ...
def test_killed_records_nothing(monkeypatch): ...      # no telemetry.db is created
def test_opted_out_records_nothing(tmp_home): ...      # telemetry.off exists
def test_corrupt_db_is_silent(tmp_home): ...           # garbage bytes in telemetry.db; record() returns None
def test_read_only_home_is_silent(tmp_home): ...       # record() returns None
def test_contention_is_lossy_and_silent(tmp_home): ...  # 4 subprocesses x 50 records: exit 0, empty stderr, 0 < n <= 200
def test_rows_older_than_53_weeks_are_pruned(tmp_home): ...
def test_first_seen_is_set_once(tmp_home): ...
def test_transition_within_30_minutes_counts_a_pair(tmp_home): ...  # "image t2i -> video i2v" n=1
def test_transition_after_30_minutes_counts_nothing(tmp_home): ...
def test_no_connection_outlives_record(tmp_home): ...
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_telemetry.py -v`
Expected: FAIL, `ModuleNotFoundError: gflow_cli.telemetry`

- [ ] **Step 3: Implement**

Tables `counters(week, surface, kind, key, n, PRIMARY KEY(week, surface, kind, key))` and `meta(key PRIMARY KEY, value)` in `<_resolve_home()>/telemetry.db`, WAL, `timeout=0.2`, upsert `n = n + 1`, one transaction per `record`, connection opened and closed each call. The whole body of `record` sits in `try/except Exception` with a debug log. `killed()` reads `os.environ`, then `dotenv_values` over both `_env_files()`, cached per process; any off value wins. Do **not** add a `Settings` field: a bad bool exits 11 (`cli.py:161`). `opted_out()` is `(<home>/telemetry.off).exists()`, not cached. In `tests/conftest.py` set `GFLOW_CLI_TELEMETRY=0` next to `UPDATE_CHECK`; `tests/test_telemetry.py` unsets it. Document `DO_NOT_TRACK` too, including that it is honoured from `.env`.

- [ ] **Step 4: Run them**

Run: `uv run pytest tests/test_telemetry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/telemetry.py pyproject.toml uv.lock tests/test_telemetry.py tests/conftest.py .env.template docs/CONFIGURATION.md
git commit -m "feat(telemetry): local weekly counter store with kill switches"
```

### Task 2: CLI hook

**Files:**
- Modify: `src/gflow_cli/telemetry.py`, `src/gflow_cli/cli.py:132`, `src/gflow_cli/observability.py:165`
- Test: `tests/test_telemetry_cli.py`

**Interfaces:**
- Consumes: `record` from Task 1.
- Produces: `leaf_and_flags(root: click.Group, argv: Sequence[str]) -> tuple[str, list[str]]`; `class CountingGroup(click.Group)` in `telemetry.py`, used as `@click.group(cls=CountingGroup)` on `main`; `crash_key(exc: BaseException) -> str`; `record_crash(surface: Literal["cli", "mcp"], exc: BaseException) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_leaf_walks_groups():
    assert leaf_and_flags(main, ["video", "t2v", "a cat", "--ref", "x.png"]) == ("video t2v", ["--ref"])

def test_prompt_shaped_like_flag_is_not_a_key():
    assert leaf_and_flags(main, ["video", "t2v", "--nonsense", "image t2i"]) == ("video t2v", [])

def test_flag_with_equals_keeps_name_only():
    assert leaf_and_flags(main, ["video", "t2v", "--ref=secret.png", "x"])[1] == ["--ref"]

def test_short_flag_normalises_to_long(): ...   # reports the option's first long opt

def test_unknown_command_is_group_name():
    assert leaf_and_flags(main, ["nonsense"])[0] == "gflow"

def test_sys_exit_code_is_recorded(): ...       # a command that sys.exit(11) records "11"
def test_keyboard_interrupt_records_130(): ...
def test_abort_records_1(): ...
def test_unhandled_exception_records_1_and_propagates(): ...
def test_non_integer_system_exit_is_coerced(): ...
def test_help_records_nothing(): ...            # gflow video t2v --help
def test_bad_config_exit_11_is_still_recorded(): ...
def test_runner_args_are_used_not_sys_argv(): ...
def test_json_stdout_identical_with_telemetry_on_and_killed(): ...  # gflow data list projects --json
def test_crash_key_is_class_and_innermost_gflow_frame(): ...
    # raised from gflow_cli.api.client.post: "ValueError at api.client:post"
def test_crash_key_never_holds_the_message(): ...   # message "secret prompt C:\\Users\\jane" is absent
def test_crash_with_no_gflow_frame_is_other(): ...  # "ValueError at other"
def test_handled_crash_is_counted_on_both_arms(): ...  # run_with_handlers, with and without --json
def test_crash_reporting_never_changes_the_exit_code(): ...  # record_crash raising leaves exit 1
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_telemetry_cli.py -v`
Expected: FAIL, `ImportError: leaf_and_flags`

- [ ] **Step 3: Implement**

`CountingGroup.parse_args` stores `list(args)` in `ctx.meta["telemetry_argv"]` before calling `super()`. `CountingGroup.invoke` wraps `super().invoke(ctx)`, derives the outcome from `SystemExit`, `click.exceptions.Exit`, `click.ClickException.exit_code`, `click.Abort` (`"1"`), `KeyboardInterrupt` (`"130"`) and any other `BaseException` (`"1"`), re-raises each untouched, and calls `record` in `finally`, skipping it when `--help` is in the argv before a `--`. `leaf_and_flags` keeps a flag only if it is in the leaf's `params` opts. Crashes never reach `invoke` as exceptions, because `run_with_handlers` turns them into `SystemExit(1)`, so call `record_crash("cli", exc)` from `emit_unhandled_event` (`observability.py:165`), which both arms pass through (`_cli_helpers.py:379`, `:457`). `crash_key` walks the traceback for the innermost frame whose module starts with `gflow_cli`, strips that prefix, and caps the key at 120 characters. It never reads `str(exc)`.

- [ ] **Step 4: Run them, plus the CLI suite**

Run: `uv run pytest tests/test_telemetry_cli.py tests/cli -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/telemetry.py src/gflow_cli/cli.py src/gflow_cli/observability.py tests/test_telemetry_cli.py
git commit -m "feat(telemetry): count CLI commands and flag names at the group boundary"
```

### Task 3: MCP hook

**Files:**
- Modify: `src/gflow_cli/mcp/tools.py:261`, `src/gflow_cli/telemetry.py`
- Test: `tests/mcp/test_telemetry_hook.py`

**Interfaces:**
- Consumes: `record`.
- Produces: `KNOWN_MCP_CLIENTS: frozenset[str]`; `client_key(name: str | None) -> str`; `outcome_of(result: dict[str, Any]) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_success_records_outcome_0(): ...         # surface "mcp", command = fn.__name__
async def test_error_envelope_is_not_success(): ...     # returned {"status": "error"}
async def test_tool_local_refusals_are_not_success(): ... # the rate_limited and invalid_tools returns
async def test_pending_records_queued(): ...
async def test_gflow_error_records_its_exit_code(): ... # InsufficientCreditsError records "37"
async def test_unexpected_exception_records_1_and_envelope_unchanged(): ...
async def test_unexpected_exception_records_its_crash_key(): ...  # surface "mcp"
async def test_record_failure_never_reaches_client(): ...
async def test_record_runs_off_the_event_loop(): ...    # a stalled write does not delay another task
async def test_a_running_server_honours_off(): ...      # telemetry.off created mid-session stops counting
async def test_known_client_name_is_counted_and_unknown_becomes_other(): ...
async def test_missing_client_info_is_not_an_error(): ...
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/mcp/test_telemetry_hook.py -v`
Expected: FAIL, no rows recorded

- [ ] **Step 3: Implement**

`try/finally` in `_guarded.wrapper`, calling `await asyncio.to_thread(record, ...)`, with the code from `_exit_code_for` (`_cli_helpers.py:328`) on a raised `GFlowError`. `outcome_of` reads the returned envelope; read `tools.py:191` and `:215` for the refusal shapes, and use `"error"` where an envelope holds no code. Client name: `server.get_context().session.client_params.clientInfo.name` inside `try/except Exception`; `client_params` can be `None`. Fill `KNOWN_MCP_CLIENTS` only with names seen from real clients.

- [ ] **Step 4: Run them, plus the MCP suite**

Run: `uv run pytest tests/mcp -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/mcp/tools.py src/gflow_cli/telemetry.py tests/mcp/test_telemetry_hook.py
git commit -m "feat(telemetry): count MCP tool calls at the error funnel"
```

### Task 4: `gflow data stats` and its MCP twin

**Files:**
- Modify: `src/gflow_cli/data/queries.py`, `src/gflow_cli/cli_data.py`, `src/gflow_cli/mcp/tools.py`, `tests/mcp/test_cli_parity.py` (`CLI_TO_MCP`), `docs/MCP.md:134`, `docs/USAGE.md`
- Test: `tests/test_cli_data.py`, `tests/test_data_queries.py`, `tests/mcp/test_usage_stats.py`

**Interfaces:**
- Consumes: `read_counters`, `killed`, `opted_out`.
- Produces: `usage_stats(*, db_path: Path, weeks: int) -> list[UsageRow]` grouped by command, model, status, month; CLI `gflow data stats --weeks N --json`, default 12; tool `gflow_usage_stats(weeks: int = 12)`, returning the stats plus a `telemetry` block with state and switch in force.

- [ ] **Step 1: Write the failing tests**

```python
def test_usage_stats_groups_by_command_model_status_month(): ...
def test_usage_stats_never_selects_prompt_columns(): ...   # no prompt text in output for a seeded op
def test_cli_stats_json_has_commands_and_generations(): ...
def test_cli_stats_empty_catalog_and_no_counters(): ...    # friendly line, exit 0
def test_cli_stats_when_killed_says_counters_are_off(): ...
async def test_mcp_twin_matches_cli_json(): ...
async def test_mcp_when_killed_says_counters_are_off(): ...  # never reads as zero usage
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_data_queries.py tests/test_cli_data.py tests/mcp/test_usage_stats.py -q`
Expected: FAIL, `usage_stats` undefined

- [ ] **Step 3: Implement**, following `list_errors_cmd` (`cli_data.py:491`) for options and `_emit`. Add `"data stats": "gflow_usage_stats"` to `CLI_TO_MCP`. `queries.py` must not import `telemetry`.

- [ ] **Step 4: Run them, plus parity**

Run: `uv run pytest tests/test_data_queries.py tests/test_cli_data.py tests/mcp -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/data/queries.py src/gflow_cli/cli_data.py src/gflow_cli/mcp/tools.py tests docs
git commit -m "feat(data): gflow data stats, local usage from counters and catalog"
```

### Task 5: `gflow telemetry status|show`, docs, PR 1

**Files:**
- Create: `src/gflow_cli/cli_telemetry.py`, `docs/TELEMETRY.md`
- Modify: `src/gflow_cli/telemetry.py`, `src/gflow_cli/tools/runtime.py:147`, `src/gflow_cli/cli.py:626`, `src/gflow_cli/mcp/tools.py`, `tests/mcp/test_cli_parity.py` (`_MCP_EXEMPT`), `docs/SECURITY.md:20`, `docs/SECURITY.md:129`, `docs/INDEX.md`, `docs/DATA_LAYER.md:608`, `AGENTS.md`, `skills/gflow-cli/SKILL.md`, `CHANGELOG.md`
- Test: `tests/test_cli_telemetry.py`

**Interfaces:**
- Consumes: `read_counters`, `DataStore.open_readonly`.
- Produces: `build_report(week: str) -> dict[str, object]`; `report_id_for(week: str) -> str`; `SCHEMA`; table `reports(week, report_id, due_at, sent_at)`; `record_tool(spec: ToolSpec, expanded: bool) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
ALLOWED = {"schema", "report_id", "week", "version", "python", "os", "arch", "installer",
           "install_age", "storage", "commands", "flags", "exits", "durations", "transitions",
           "models", "modes", "aspects", "outcomes", "mcp_clients", "crashes", "tools"}

def test_report_keys_are_exactly_the_allow_list(): ...
def test_report_never_contains_seeded_secrets(): ...
    # seed catalog with prompt, path, profile "jane.doe", s3://bucket/x, and a .gflow_account email;
    # assert none in json.dumps(report)
def test_counters_are_keyed_by_surface_then_command(): ...
    # report["exits"] == {"cli": {"video t2v": {"0": 2, "11": 1}}}
def test_models_come_from_the_catalog(): ...  # a seeded operation counts with no --model on argv
@pytest.mark.parametrize("field", ["models", "modes", "aspects", "outcomes"])
def test_unknown_value_becomes_other(field): ...
@pytest.mark.parametrize("uri,kind", [(None, "local"), ("s3://b/x", "s3"), ("gs://b/x", "gcs"), ("memory://x", "other")])
def test_storage_is_scheme_only(uri, kind): ...
@pytest.mark.parametrize("days,bucket", [(6, "<1w"), (29, "<1m"), (179, "<6m"), (180, "6m+")])
def test_install_age_buckets(days, bucket): ...
def test_report_id_is_stable_within_a_week_and_differs_across_weeks(): ...
def test_packaged_tool_keeps_its_name_with_expanded_and_unchanged_counts(): ...
def test_user_tool_counts_as_custom(): ...            # <home>/tools/acme-client.toml: "acme-client" absent
def test_user_tool_shadowing_a_packaged_name_counts_as_custom(): ...
def test_tool_counted_through_cli_and_generation_paths(): ...  # gflow tools run, and --tool
def test_tool_recording_failure_leaves_the_result_unchanged(): ...
def test_report_works_with_no_catalog_and_does_not_create_one(): ...
def test_show_prints_the_report_as_json(): ...
def test_status_names_the_kill_switch_in_force(): ...
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_cli_telemetry.py -v`
Expected: FAIL, `build_report` undefined

- [ ] **Step 3: Implement.** Read the catalog through `DataStore.open_readonly`; `_safe_db` creates and migrates. Take `storage` from the scheme of `get_settings().storage_uri` in a `try`, using `other` if settings are invalid; never keep the bucket or path. `apply_tool` has several return points, so rename its body `_apply_tool` and make `apply_tool` call it, then `record_tool(spec, result.was_expanded)`. `record_tool` keeps the name only if `load_builtin_tools().get(spec.name) == spec`, and otherwise uses `custom`. Confirm in a test whether the MCP `tools` param reaches `apply_tool`; `_cli_helpers.py:259` is the only other caller. Enums for `other`: `Model`, `VideoModel`, `OperationKind`, `OperationStatus`, and the aspect values in use. `status` prints what is counted and the switch in force; at this stage it says uploading does not exist yet. `show` prints `build_report(iso_week())`. Add the report to the `telemetry` block of `gflow_usage_stats`, and exempt `telemetry status` and `telemetry show` in `_MCP_EXEMPT`: `"same state and report returned by gflow_usage_stats"`. Write `docs/TELEMETRY.md` covering local counters only. Reword `docs/SECURITY.md:20` and `:129` to say counters are kept locally and nothing leaves the machine. Regenerate with `uv run python scripts/ci/generate_website_docs.py` and `uv run python scripts/ci/generate_plugin_skills.py`.

- [ ] **Step 4: Run the Impeccable Routine**

Run: `/gflow:check`
Expected: all gates green

- [ ] **Step 5: Verify by a run.** On an installed build: run `gflow doctor`, `gflow data stats`, `gflow telemetry show`; confirm rows appear; repeat with `DO_NOT_TRACK=1` and confirm nothing is added; call `gflow_usage_stats` through a real MCP client and note its client name. No Flow surface is touched, so state that in the PR and name this run as the verification.

- [ ] **Step 6: Commit, review, PR**

```bash
git add src tests docs website plugins AGENTS.md skills CHANGELOG.md
git commit -m "feat(telemetry): status and show, local usage docs"
```

Then `/gflow:branch-review`, `gh pr create --base develop`, `/gflow:sonar <N>`.

---

## PR 2: notice and upload

**Do not start merging until:** PR 1 has been out for one release, counsel has reviewed `docs/TELEMETRY.md` and answered the spec's open decisions 2 to 4, and the endpoint from Task 6 answers.

### Task 6: Ingest endpoint

**Files:** outside this repo, in a new public repo, so the promise can be checked. Cloudflare Worker + D1, created with `wrangler d1 create <name> --jurisdiction=eu`, which cannot be added later.

**Interfaces:**
- Produces: `POST /v1/report` (JSON, max 16 KB). Replies per the spec's table.

- [ ] **Step 1:** Write the tests: an extra key returns 400; a body over 16 KB returns 413; a repeat `report_id` returns 204 and adds nothing; with `STOPPED=1` every request returns 410; no stored row contains a `report_id` beyond the seen table, or any body text.
- [ ] **Step 2:** Implement validation against the allow-list. Add each value, and each v1 cross from the spec, to `totals(week, field, key, n)` and increment `reports`, then discard the body. Keep `seen(report_id, expires)` for 14 days. Never store the IP. Increment country as a total only. Disable request logging.
- [ ] **Step 3:** When a D1 write fails on the daily limit, roll back that report and return 429 with `Retry-After` reaching past the next 00:00 UTC. Test: no partial totals remain from a rejected report.
- [ ] **Step 4:** Run: `POST` a sample against the deployed URL. Expected: 204, totals move, and neither the store nor the logs hold the IP, the body or a per-report row. Then set `STOPPED=1` and confirm 410.

### Task 7: Notice and opt-out

**Files:**
- Modify: `src/gflow_cli/telemetry.py`, `src/gflow_cli/cli_telemetry.py`, `src/gflow_cli/cli.py:193`, `src/gflow_cli/cli.py:332`, `src/gflow_cli/cli.py:725`, `tests/mcp/test_cli_parity.py`
- Test: `tests/test_telemetry_notice.py`

**Interfaces:**
- Produces: `uploads_enabled() -> bool`; `maybe_show_notice() -> bool`, True if shown on this run; `set_opted_out(value: bool) -> None`; `gflow telemetry on`; `gflow telemetry off [--delete]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_notice_not_seen_means_no_upload(): ...
def test_notice_seen_means_upload_is_enabled_by_default(): ...
def test_nothing_is_sent_on_the_run_that_shows_the_notice(): ...
def test_notice_goes_to_stderr_never_stdout(): ...
def test_notice_shows_once_per_version(): ...
def test_new_notice_version_stops_upload_until_shown(): ...
def test_auth_login_shows_it_after_session_saved(): ...
def test_mcp_setup_shows_it(): ...
@pytest.mark.parametrize("case", ["ci", "stderr_piped", "killed", "opted_out", "already_seen", "telemetry off"])
def test_notice_is_not_shown(case): ...
def test_off_stops_counting_and_upload_and_persists(): ...
def test_off_works_without_a_tty(): ...
def test_off_that_cannot_write_exits_11_and_says_so(): ...
def test_no_writer_can_undo_off(): ...                 # upload and notice code never touch telemetry.off
def test_off_delete_wipes_local_data_and_says_remote_holds_nothing_per_install(): ...
def test_on_under_a_kill_switch_persists_says_what_blocks_and_exits_0(): ...
def test_corrupt_db_means_no_upload(): ...             # fail closed
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_telemetry_notice.py -v`
Expected: FAIL, `uploads_enabled` undefined

- [ ] **Step 3: Implement.** `uploads_enabled()` is false if killed, opted out, in CI, not `_installed_from_index()`, `upload_disabled` set, or `notice_seen_version != NOTICE_VERSION`, and false on any read error. Call `maybe_show_notice` next to the update notice, after "Session saved", and in `mcp_setup`; skip it for the `telemetry` group. Notice copy, exact, subject to counsel:

```
gflow sends weekly usage totals to improve it, and we share overall figures publicly and with
sponsors. Commands, flag names, models and error codes. No ID, prompts, paths or account details.
Each report is added to totals and discarded.
Turn it off: gflow telemetry off, or set GFLOW_CLI_TELEMETRY=0
See exactly what is sent: gflow telemetry show. Full notice: gflow docs TELEMETRY
```

`click.echo(..., err=True)`. `off` raises `ConfigurationError` if the sentinel cannot be written. Add `on` and `off` to `_MCP_EXEMPT`: `"a person's choice about their own machine; an agent should not change it"`.

- [ ] **Step 4: Run them**

Run: `uv run pytest tests/test_telemetry_notice.py tests/mcp/test_cli_parity.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli tests
git commit -m "feat(telemetry): first-run notice and opt-out"
```

### Task 8: Uploader

**Files:**
- Create: `src/gflow_cli/telemetry_upload.py`
- Modify: `src/gflow_cli/cli.py:193`, `src/gflow_cli/mcp/tools.py` (`_guarded`, so a long-running server still reports)
- Test: `tests/test_telemetry_upload.py`

**Interfaces:**
- Consumes: `build_report`, `report_id_for`, `uploads_enabled`.
- Produces: `next_due_week(now: datetime) -> str | None`; `maybe_upload() -> None`; `send(week: str) -> None`; `_ENDPOINT`, a constant with no env override; `python -m gflow_cli.telemetry_upload` runs `send` for the due week.

- [ ] **Step 1: Write the failing tests**

```python
def test_not_enabled_spawns_nothing(): ...
def test_weeks_before_the_notice_are_never_sent(): ...
def test_week_is_not_due_before_its_random_delay(): ...   # delay drawn from 0-168 h, stored per week
def test_delay_is_redrawn_each_week(): ...
def test_at_most_one_attempt_a_day(): ...
def test_one_report_per_attempt_oldest_first(): ...
def test_204_marks_sent_and_keeps_rows(): ...             # data stats still shows that week
@pytest.mark.parametrize("status", [400, 413])
def test_rejected_report_is_dropped_not_retried(status): ...
def test_429_waits_for_retry_after_plus_a_random_extra(): ...   # extra drawn from 0-6 h
@pytest.mark.parametrize("failure", [500, "timeout", "dns"])
def test_transient_failure_keeps_the_week(failure): ...
def test_410_stops_uploading_forever(): ...
def test_redirect_is_not_followed(): ...
def test_body_is_never_read(): ...
def test_two_months_offline_sends_each_week_once(): ...
def test_future_clock_loses_nothing(): ...
def test_maybe_upload_never_raises(): ...
def test_child_has_no_console_window_on_windows(): ...    # creationflags, DEVNULL stdio
def test_report_survives_the_parent_exiting_at_once(): ... # loopback server, parent exits immediately
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_telemetry_upload.py -v`
Expected: FAIL, module missing

- [ ] **Step 3: Implement.** `maybe_upload` does the cheap check, stamps `last_attempt` synchronously, then starts `subprocess.Popen([sys.executable, "-m", "gflow_cli.telemetry_upload"], ...)`, detached, all stdio `DEVNULL`, with `CREATE_NO_WINDOW | DETACHED_PROCESS` on Windows and `start_new_session=True` elsewhere. On MCP it runs inside the same `to_thread` call as `record`. `send` uses `urllib.request` with a no-redirect handler and a 5 s timeout, reads only the status and `Retry-After`, and applies the spec's reply table. A daemon thread is not acceptable here: 0 of 16 POSTs arrived when the process lived 0.5 s or less.

- [ ] **Step 4: Measure.** Time `maybe_upload` on Windows when a report is due. Expected: under 30 ms. Over that, stop and take it to the maintainer.

- [ ] **Step 5: Run them**

Run: `uv run pytest tests/test_telemetry_upload.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/gflow_cli/telemetry_upload.py src/gflow_cli/cli.py src/gflow_cli/mcp/tools.py tests/test_telemetry_upload.py
git commit -m "feat(telemetry): weekly best-effort upload of completed weeks"
```

### Task 9: Change the promise, verify, PR 2

**Files:**
- Modify: `docs/SECURITY.md`, `docs/TELEMETRY.md`, `DISCLAIMER.md`, `docs/CONFIGURATION.md`, `docs/MCP.md`, `PLAN.md:714`, `README.md`, `llms.txt`, `skills/gflow-cli/SKILL.md`, `AGENTS.md`, `CHANGELOG.md`
- Test: `tests/test_telemetry_docs.py`

- [ ] **Step 1: Write the failing test**

```python
def test_notice_version_matches_docs(): ...     # "Notice version: 1" in docs/TELEMETRY.md == NOTICE_VERSION
def test_no_doc_says_no_telemetry(): ...
def test_notice_copy_matches_the_doc(): ...
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_telemetry_docs.py -v`
Expected: FAIL on `SECURITY.md`

- [ ] **Step 3: Write.** The `SECURITY.md` lines from Task 5 become: weekly totals with no identifier, how to turn them off, link to the notice; and they name the daily PyPI update check with `GFLOW_CLI_UPDATE_CHECK=0`. `TELEMETRY.md` gains controller, data including the edge-derived country that `show` cannot display, every use of it, how reports are discarded, why remote deletion has nothing to act on, how the server can stop uploads, notice version. `DISCLAIMER.md` gains a "Usage data" section. Amend ADR #9 at `PLAN.md:714` to name this one endpoint. Repo-wide grep for `telemetry`, `phone-home`, `usage stats`, including `llms.txt` and skill headers. Regenerate the website mirror and plugin skills.

- [ ] **Step 4: Run** `/gflow:check`. Expected: all gates green.

- [ ] **Step 5: Verify by a run**, on an installed wheel: the notice appears in a real terminal and nothing is sent on that run; it is absent when piped; `show`; one real POST lands in the totals with only allow-listed keys and no IP, from a command that exits at once; `off` stops the next one; `DO_NOT_TRACK=1` stops it; flip the endpoint to 410 and confirm the client stops for good. Also check whether an agent's terminal passes as a TTY, because that would mark the notice seen with no person present. Paste the redacted result into the PR.

- [ ] **Step 6: Commit, review, PR**

```bash
git add docs DISCLAIMER.md README.md llms.txt skills plugins AGENTS.md PLAN.md CHANGELOG.md website tests
git commit -m "docs(telemetry): replace the no-telemetry promise with the usage notice"
```

Then `/gflow:pr-council-review <N>`, `/gflow:sonar <N>`.
