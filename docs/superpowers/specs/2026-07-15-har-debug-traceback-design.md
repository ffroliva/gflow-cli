# Diagnostic visibility: HAR capture + debug tracebacks — design

**Issue:** [#316](https://github.com/ffroliva/gflow-cli/issues/316) — "Add --har option and GFLOW_CLI_DEBUG_TRACEBACK env var for diagnostic visibility." Consolidated from #311 (Item 6), a production debugging session where the operator had no way to capture raw network traffic or see a real traceback — every unclassified error collapses to `"Unexpected error."` plus a SHA-256 hash.

## Problem

Two independent gaps when diagnosing a gflow-cli failure live:

1. **No network capture.** There's no way to see the actual request/response bodies Flow's frontend exchanged (submit payloads, wire-format surprises, WAF responses). The only visibility is whatever a given transport chooses to log.
2. **Errors are opaque by design.** `observability.emit_unhandled_event()` intentionally hashes the message and traceback of any non-`GFlowError` exception (`message_hash`, `stack_hash`) so aggregated logs never leak PII/tokens. Correct for telemetry, but it also means a developer chasing a real bug locally gets nothing to go on beyond "Unexpected error. Re-run with --verbose to capture details" — a hint that is currently false: `--verbose` only lowers the structlog level, it does not touch the hash.

Both are opt-in, explicit-consent debug knobs — never default-on, because both can capture sensitive material (auth cookies, bearer tokens, session identifiers).

## Decisions (from brainstorming)

- **HAR capture is env-var only — no `--har` CLI flag.** This codebase has no shared Click option decorator; every flag (`--profile`, `--headless`, ...) is hand-copied into each of the ~15 generation command functions. Adding `--har` to all of them is a lot of surface for a debug-only feature. `GFLOW_CLI_HAR_PATH` gives the same capability with a one-line settings field and matches how other debug toggles in this codebase already work (`CHROME_BINARY`, `GFLOW_CLI_TRANSPORT`).
- **`GFLOW_CLI_DEBUG_TRACEBACK` affects console output, not just logs.** The point is live debugging, so the real message + traceback must print to the terminal, not only end up in a structured log event nobody is tailing.
- **JSON mode gets the same visibility.** Per the user: "most robust and safe approach giving the most observability possible." `--json` output must carry the same raw detail as the Rich console path when the flag is set — no diminished mode for scripted callers.

## Design

### 1. Settings (`src/gflow_cli/config.py`)

Two new fields on `Settings`, following the existing `db_path: Path | None` / boolean-flag patterns:

```python
har_path: Path | None = Field(
    default=None,
    description=(
        "When set, captures full Playwright network traffic (requests, "
        "responses, headers, cookies) to this HAR file for the session. "
        "SECURITY: HAR files can contain auth cookies and bearer tokens — "
        "never share one publicly; the file is chmod 0o600 on POSIX. "
        "Concurrent gflow processes pointed at the same path will overwrite "
        "each other's HAR (last-writer-wins) — use a distinct path per run. "
        "Override via GFLOW_CLI_HAR_PATH."
    ),
)
debug_traceback: bool = Field(
    default=False,
    description=(
        "Bypasses hash redaction for unhandled (non-GFlowError) exceptions: "
        "prints the real message + traceback to the console and, under "
        "--json, into the payload's error.traceback field, instead of "
        "SHA-256 hashes. SECURITY: may leak tokens/cookies present in "
        "exception text — for local debugging only. Never pipe --json "
        "output under this flag to a shared/persistent system (CI logs, "
        "log aggregators, webhooks) without redacting it first. "
        "Override via GFLOW_CLI_DEBUG_TRACEBACK."
    ),
)
```

No CLI flags, no validators needed — both are plain env-var-driven settings, resolved the same way every other `GFLOW_CLI_*` knob is.

### 2. HAR capture (`src/gflow_cli/api/client.py`)

`FlowApiClient._persistent_context_kwargs()` (the single production browser-launch site — confirmed `UiAutomationTransport.setup()` always takes the shared-page branch in production and never opens its own context; its standalone-launch branch is exercised only by dev/e2e scripts) adds `record_har_path` when set. **Note:** the method today is a single `return {...}` dict literal (`client.py:333-370`, no local variable) — this is a small restructuring of its shape, not a pure insertion:

```python
def _persistent_context_kwargs(self) -> JsonObject:
    kwargs: JsonObject = {
        "user_data_dir": str(self.profile_dir),
        # ... existing fields unchanged ...
    }
    if self.settings.har_path is not None:
        self.settings.har_path.parent.mkdir(parents=True, exist_ok=True)
        kwargs["record_har_path"] = str(self.settings.har_path)
        logger.warning(
            "client.har_capture_enabled",
            har_path=str(self.settings.har_path),
            hint="HAR file will contain full request/response bodies, headers, "
            "and cookies — do not share it publicly or attach it to a public bug report.",
        )
    return kwargs
```

Playwright writes the HAR lazily on context close (`_close_browser_resources` → `close_context_bounded(self._context, owner="client")`, `client.py:692`). Default `record_har_mode`/`record_har_content` (full capture, embedded content) are left as Playwright's own defaults — that's the useful mode for this use case and there's no reason to expose a second knob for it.

**File permissions (D3 must-fix):** a HAR file holds live auth cookies and bearer tokens — higher sensitivity than the CDP lockfile this codebase already hardens (`browser_manager.py::_write_lock`, `mode=0o600`, "not world-readable on multi-user POSIX boxes"). After the context close in `_close_browser_resources` succeeds and `self.settings.har_path` is set, best-effort `os.chmod(self.settings.har_path, 0o600)` (POSIX; no-op on Windows, wrapped in `try/except OSError` — never fail teardown over a permissions tweak).

**Known caveat, not fixed (documented, per D1):** `GFLOW_CLI_HAR_PATH` is env-scoped, so two concurrent `gflow` processes (or a daemon serving multiple profiles) pointed at the same path would each write their own HAR on context close — last-writer-wins, silent overwrite, no error. Acceptable for an opt-in single-operator debug tool; call this out in the `Field` description so a user pointing multiple concurrent runs at one path isn't surprised.

### 3. Debug tracebacks

**`src/gflow_cli/observability.py` stays untouched (D14 cut).** Both `_cli_helpers.py` call sites already have the live `exc`/`e` object in scope — there's no need to route the raw message/traceback *through* `emit_unhandled_event` to reach them, and nothing in this codebase consumes the structured `error_unhandled` log event today (no aggregator, no tailer). Threading a `debug` kwarg through it would be a third redundant surface for zero additional consumers, and it would also weaken `emit_unhandled_event`'s module docstring guarantee ("NEVER includes the raw exception message or formatted traceback") from absolute to conditional for no practical gain. So: `emit_unhandled_event` keeps hashing unconditionally, always, regardless of `debug_traceback` — the structured log event stays privacy-safe by construction, permanently.

**`src/gflow_cli/_cli_helpers.py`** — both call sites read `get_settings().debug_traceback` and build their own raw output directly from the exception already in scope, independent of `emit_unhandled_event`:

- `_handle_unhandled_error` (console path): still calls `emit_unhandled_event(...)` unchanged (hashed log line). Additionally, when `debug_traceback` is set, print the real exception + full traceback (`traceback.format_exception(exc)`) instead of the generic message, prefixed with a `[yellow]` one-line warning that the output may contain sensitive tokens/cookies.
- `run_with_handlers`'s `as_json` branch: still calls `emit_unhandled_event(...)` unchanged. `json_output.unexpected_payload()` gains an optional `debug: BaseException | None = None` param; when passed, the payload's `error` dict gets `detail` (the message) and `traceback` fields instead of staying generic. Exit code stays 1 either way — this only changes payload content, not control flow.

  **Security caveat (D3 must-fix) — JSON is a materially different risk surface than the console line.** A human watches the interactive console live and can react to the yellow warning; `--json` output is designed to be piped into CI logs, log aggregators, and webhooks that persist or forward it unreviewed. The `debug_traceback` `Field` description and `docs/CONFIGURATION.md` entry must carry an explicit, JSON-specific line: *"Under `--json`, the raw traceback lands in the JSON payload's `error.traceback` field — never pipe `--json` output under this flag to a shared or persistent system (CI logs, log aggregators, chat webhooks) without redacting it first."*

Net effect: 4 files touched (`config.py`, `client.py`, `_cli_helpers.py`, `docs/CONFIGURATION.md`) instead of 5 — `observability.py` drops out of the diff entirely.

### Non-goals

- No per-command `--har` flag (see Decisions).
- No new redaction/scrubbing layer on top of the raw traceback — the whole point of the flag is "give me the truth," gated by an explicit opt-in and a warning, matching how `GFLOW_CLI_HAR_PATH` is handled.
- No change to the `GFlowError` path (`_handle_gflow_error` / `error_payload`) — those already print full `detail`/`remediation_hint` unredacted; only the *unhandled* exception path is hashed today.
- No change to `--verbose`'s existing log-level behavior — it stays orthogonal to `debug_traceback`. (Its console hint text should get corrected to mention `GFLOW_CLI_DEBUG_TRACEBACK` instead of implying `--verbose` alone reveals detail — small copy fix bundled into the same PR.)

## Testing

Extends existing test files rather than adding new ones:

- `tests/api/test_client_launch_kwargs.py` — `record_har_path` present when `har_path` set, absent when `None`; parent directory created; file chmod 0o600 after close on POSIX (skip the permission assertion on Windows).
- `tests/test_observability.py` — unchanged; `emit_unhandled_event` keeps its existing `test_emit_unhandled_event_hashes_message_and_stack` behavior, always hashed regardless of `debug_traceback` (no new param).
- `tests/cli/test_error_handling.py` — console output switches between generic message and real traceback based on `debug_traceback`.
- `tests/test_json_output.py` — extends the existing `TestErrorPayload`/`test_unexpected_is_privacy_safe` coverage of `unexpected_payload()`: default (`debug=None`) stays privacy-safe and unchanged; passing an exception adds `detail`/`traceback` to the `error` dict. This is the precise, low-indirection place to pin the new optional param, one level closer than the CLI-integration test in `test_error_handling.py`.

## Docs

`docs/CONFIGURATION.md` gets both new env vars added as `### GFLOW_CLI_HAR_PATH` / `### GFLOW_CLI_DEBUG_TRACEBACK` entries (the file's existing reference format is per-variable `###` subsections with bolded `**What:**`/`**Default:**` fields, not a literal table), each carrying the same security caveats as the `Field` descriptions above, including the JSON-specific warning.
