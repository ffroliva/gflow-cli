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
        "never share one publicly. Override via GFLOW_CLI_HAR_PATH."
    ),
)
debug_traceback: bool = Field(
    default=False,
    description=(
        "Bypasses hash redaction for unhandled (non-GFlowError) exceptions: "
        "prints the real message + traceback to the console and JSON output "
        "instead of SHA-256 hashes. SECURITY: may leak tokens/cookies present "
        "in exception text — for local debugging only. "
        "Override via GFLOW_CLI_DEBUG_TRACEBACK."
    ),
)
```

No CLI flags, no validators needed — both are plain env-var-driven settings, resolved the same way every other `GFLOW_CLI_*` knob is.

### 2. HAR capture (`src/gflow_cli/api/client.py`)

`FlowApiClient._persistent_context_kwargs()` (the single production browser-launch site — confirmed `UiAutomationTransport.setup()` always takes the shared-page branch in production and never opens its own context; its standalone-launch branch is exercised only by dev/e2e scripts) adds `record_har_path` when set:

```python
if self.settings.har_path is not None:
    self.settings.har_path.parent.mkdir(parents=True, exist_ok=True)
    kwargs["record_har_path"] = str(self.settings.har_path)
    logger.warning(
        "client.har_capture_enabled",
        har_path=str(self.settings.har_path),
        hint="HAR file will contain full request/response bodies, headers, "
        "and cookies — do not share it publicly or attach it to a public bug report.",
    )
```

Playwright writes the HAR on context close; no other lifecycle changes needed. Default `record_har_mode`/`record_har_content` (full capture, embedded content) are left as Playwright's own defaults — that's the useful mode for this use case and there's no reason to expose a second knob for it.

### 3. Debug tracebacks

**`src/gflow_cli/observability.py`** — `emit_unhandled_event` gains a `debug: bool = False` kwarg:

```python
def emit_unhandled_event(
    logger: FilteringBoundLogger,
    exc: BaseException,
    *,
    cli_command: str,
    debug: bool = False,
) -> None:
    if debug:
        logger.error(
            "error_unhandled",
            exception_class=type(exc).__name__,
            message=str(exc),
            traceback="".join(traceback.format_exception(exc)),
            cli_command=cli_command,
        )
        return
    # ... existing hash path unchanged
```

**`src/gflow_cli/_cli_helpers.py`** — both call sites read `get_settings().debug_traceback` and pass it through:

- `_handle_unhandled_error` (console path): when `debug_traceback` is set, print the real exception + full traceback (via `traceback.format_exception`) instead of the generic message, prefixed with a `[yellow]` one-line warning that the output may contain sensitive tokens/cookies.
- `run_with_handlers`'s `as_json` branch: `json_output.unexpected_payload()` gains an optional `debug: BaseException | None = None` param; when passed, the payload's `error` dict gets `detail` (the message) and `traceback` fields instead of staying generic. Exit code stays 1 either way — this only changes payload content, not control flow.

Both call sites already have `exc`/`e` in scope, so this is a straight pass-through — no new exception plumbing.

### Non-goals

- No per-command `--har` flag (see Decisions).
- No new redaction/scrubbing layer on top of the raw traceback — the whole point of the flag is "give me the truth," gated by an explicit opt-in and a warning, matching how `GFLOW_CLI_HAR_PATH` is handled.
- No change to the `GFlowError` path (`_handle_gflow_error` / `error_payload`) — those already print full `detail`/`remediation_hint` unredacted; only the *unhandled* exception path is hashed today.
- No change to `--verbose`'s existing log-level behavior — it stays orthogonal to `debug_traceback`. (Its console hint text should get corrected to mention `GFLOW_CLI_DEBUG_TRACEBACK` instead of implying `--verbose` alone reveals detail — small copy fix bundled into the same PR.)

## Testing

Extends existing test files rather than adding new ones:

- `tests/api/test_client_launch_kwargs.py` — `record_har_path` present when `har_path` set, absent when `None`; parent directory created.
- `tests/test_observability.py` — `emit_unhandled_event(debug=True)` logs raw message/traceback instead of hashes; `debug=False` (default) unchanged, covering the existing `test_emit_unhandled_event_hashes_message_and_stack`.
- `tests/cli/test_error_handling.py` — console output switches between generic message and real traceback based on `debug_traceback`; JSON payload gains `detail`/`traceback` fields under the same setting.

## Docs

`docs/CONFIGURATION.md` gets both new env vars added to its reference table, each with the same security caveat as in the `Field` descriptions above.
