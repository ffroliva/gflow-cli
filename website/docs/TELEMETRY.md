# Local usage metrics and telemetry

> **Local only:** Nothing leaves the machine in this version; a future version will add an
> opt-out weekly upload after a notice — see [#1164](https://github.com/ffroliva/gflow-cli/issues/1164).

gflow-cli records coarse, privacy-preserving usage counters locally on your machine to help
you inspect your own CLI and MCP activity via `gflow data stats` and preview weekly reports via
`gflow telemetry show`.

---

## The local store

All counters are stored in an isolated SQLite database on your local disk:

```text
<GFLOW_CLI_HOME>/telemetry.db
```

This file is completely separate from the asset catalog (`<GFLOW_CLI_HOME>/gflow.db`). Counters
never sit in a database holding prompts or media metadata.

Database properties:
- **No cached connection:** The database is opened per write and closed immediately, preventing
  WAL lock contention during long-running MCP server sessions.
- **Retention:** Counters are grouped by ISO week and retained for 53 weeks. Older entries are
  pruned automatically.
- **Fail-safe:** Counting never raises into, prints during, or noticeably slows user commands.
  Every SQLite write failure is silent.

---

## What is counted locally

The local report is assembled field-by-field from an explicit allow-list:

| Category | Description | Stored values |
|---|---|---|
| `commands` | CLI and MCP commands invoked | Command names only (e.g. `video t2v`) |
| `flags` | Option flags passed to CLI commands | Registered option names only (e.g. `--aspect`) |
| `exits` | Exit codes per command | Stable numeric exit codes (e.g. `0`, `11`, `37`) |
| `durations` | Invocation runtime buckets | `<10s`, `<1m`, `<5m`, `5m+` |
| `transitions` | Chained command workflows | `command_a -> command_b` within 30 minutes |
| `models` | Models from catalog operations | Known enum values; unknown values become `other` |
| `modes` | Generation modes (t2i, i2v, etc.) | Known enum values; unknown values become `other` |
| `aspects` | Aspect ratios from catalog operations | Known ratios/enums; unknown values become `other` |
| `outcomes` | Status of catalog operations | `started`, `succeeded`, `failed`, or `other` |
| `mcp_clients`| MCP client names from `clientInfo` | Known client names; unknown values become `other` |
| `crashes` | Privacy-safe crash fingerprints | Exception class and innermost function names |
| `tools` | Prompt tool invocations | Built-in tool names; custom/user tools become `custom` |

### Crash fingerprinting

When an unexpected exception occurs, gflow-cli computes a structural fingerprint:
- The exception class name and cause class (e.g. `TimeoutError from OSError`).
- At most three innermost frame names inside `gflow_cli` as `module:qualname`.
- Third-party packages collapse to generic markers (e.g. `<playwright>`).
- Traceback text, local variables, file paths, line numbers, and error messages are **never** read.

---

## What is never collected

The following data never enters `telemetry.db` and is never included in reports:
- Prompts or prompt text
- File paths or file names
- Remote storage URIs, buckets, or paths
- Google account identifiers, emails, cookies, or session tokens
- Flow project IDs, media UUIDs, or workflow IDs
- Account credit balances
- Character names, faces, voices, or personalities
- Raw error messages, logs, or full stack traces
- Positional argument values or free-form flag values

---

## Kill switches

You can disable all telemetry counting at any time using standard switches:

### 1. `DO_NOT_TRACK`
Set `DO_NOT_TRACK=1` in your environment or in `.env` (project-local or `$GFLOW_CLI_HOME/.env`):
```bash
export DO_NOT_TRACK=1
```
Any value other than empty, `0`, or `false` completely disables all telemetry recording.

### 2. `GFLOW_CLI_TELEMETRY`
Set `GFLOW_CLI_TELEMETRY=0` in your environment or `.env`:
```bash
export GFLOW_CLI_TELEMETRY=0
```
Any value other than `1`, `true`, `on`, or empty disables all telemetry recording.

### 3. Sentinel file
Create an empty sentinel file at `<GFLOW_CLI_HOME>/telemetry.off`:
```bash
touch ~/.config/gflow/telemetry.off
```
When this file exists, counting stops immediately (including in active MCP server processes).

---

## Commands

### `gflow telemetry status`
Displays the active kill switch, local database location, and collection scope:
```bash
gflow telemetry status
```

Machine-readable JSON output:
```bash
gflow telemetry status --json
```

### `gflow telemetry show`
Prints the complete report payload for the current ISO week as JSON to stdout:
```bash
gflow telemetry show
```

### `gflow data stats`
Shows your own aggregated command and generation usage across recent weeks:
```bash
gflow data stats
gflow data stats --weeks 4 --json
```
