# Usage data and privacy notice

gflow-cli counts command use locally, then sends one small report of weekly totals after you have seen
the notice below in an interactive terminal. The report has no install or account identifier. You can
turn uploads off at any time.

## What is counted on this machine

Counters are kept in `<GFLOW_CLI_HOME>/telemetry.db`, separately from the catalog database. They are
grouped by ISO week and retained for 53 weeks. gflow counts command and flag names, outcomes, coarse
duration buckets, command transitions, crash fingerprints made from exception and code names, and
generation details read from the local catalog. It also records the storage service category and MCP
client category. Prompts, argument values and other free-form text are not used as counter keys.

`gflow telemetry show` prints the current week's report as JSON, including its random per-week
`report_id`; this is the report sent by the uploader. `gflow data stats` shows your own local usage
and generation totals.

## The weekly report

These are all the fields the client can send. Values are names from gflow-cli, coarse categories, or
counts.

| Field | Values sent |
|---|---|
| `schema`, `report_id`, `week` | Schema number; a random report ID used once for that week; ISO week. |
| `version`, `python`, `os`, `arch` | gflow-cli release; Python major and minor; `windows`, `linux`, `darwin`, or `other`; `x86_64`, `arm64`, or `other`. |
| `installer` | `uv`, `pipx`, `pip`, or `other`. |
| `install_age` | `<1w`, `<1m`, `<6m`, or `6m+`, based on the local first-seen week. |
| `storage` | `local`, `s3`, `gcs`, or `other`, from the storage URI scheme only. S3-compatible services such as MinIO count as `s3`. |
| `commands`, `flags`, `exits`, `durations` | Counts by `cli` or `mcp` surface and command. Flags are names only. Exit counts use exit-code names such as `0` or `11`; duration buckets are `<10s`, `<1m`, `<5m`, or `5m+`. |
| `transitions` | Counts of command pairs, such as `image t2i -> video i2v`, when the next command starts within 30 minutes. |
| `models`, `modes`, `aspects`, `outcomes` | Generation values read from the local catalog; unknown enum values become `other`. |
| `mcp_clients` | Known names from MCP `clientInfo`; unknown names become `other`. |
| `crashes` | Counts of code-only crash fingerprints by surface. A fingerprint contains exception class names, up to three gflow-cli function names, and a third-party package marker where relevant. |
| `tools` | Counts by packaged prompt-tool name and whether it expanded or left the prompt unchanged. Custom tools, including tools that shadow a packaged name, are counted only as `custom`. |

## What is never sent

The report never contains prompts, file names, paths, storage URIs, Google account information, email
addresses, account names (including `.gflow_account`), project or media IDs, credit balances,
characters, profile names, error text, stack traces, logs, flag values, user-authored tool names,
tool options, or the endpoint or model used by an LLM tool. No install ID is created or sent.

## How reports are used and discarded

The maintainer uses the totals to improve gflow-cli and to share aggregate figures publicly and with
sponsors. Published figures hide any total below 5. The totals can be compared across the categories
in the table, but they cannot be used to follow an install between weeks.

The endpoint adds each report to weekly totals and discards its body. It does not store the report's
IP address or log individual requests. A random `report_id` is retained for 14 days without report
content, solely to reject duplicate submissions. The server derives a country at the network edge
from the request IP and stores only an incremented country total. Country is not in the report and
cannot be displayed by `gflow telemetry show`.

Because the server keeps no per-install report or identifier, it has nothing to look up or delete for
a particular machine. `gflow telemetry off --delete` removes local counters; it cannot undo totals
that have already been added.

## When an upload can happen

Uploads are enabled by default outside the EU and EEA, but only when all these conditions are met:

- This machine has shown the notice below in an interactive terminal. Nothing is uploaded on the run
  that first shows it, and only weeks that began after the notice was shown can be sent.
- gflow-cli was installed from a package index. Editable and local-source installs do not upload.
- The process is not running in CI, and uploads have not been turned off or disabled by the server.
- A completed week is due. Its send time is chosen randomly within the seven days after that week
  ends. There is at most one attempt per day.

## EU and EEA: uploads need your consent

gflow checks the machine's timezone only. It uses the `TZ` IANA timezone when set, otherwise the
POSIX local timezone configuration or the Windows timezone key. A timezone that cannot be
determined is treated as being in the EU/EEA, so uploads stay off until you consent. gflow does not
read your IP address or locale to make this decision, and never sends the timezone or region.

Local counters still work in the EU/EEA and remain on your device unless you consent. To allow
weekly uploads, run `gflow telemetry on`; this records your choice locally in
`<GFLOW_CLI_HOME>/telemetry.consent`. Run `gflow telemetry off` to withdraw consent and stop
counting and uploads. Environment kill switches still take precedence.

The request runs in a detached background process, so the command can exit without waiting. The
request uses a five-second timeout. A failed request is retried on a later day. An HTTP 410 response
permanently disables future uploads for that machine; this gives the server a way to stop every
installed client without an update.

Agents and MCP-only use without an interactive terminal do not display the notice, so those machines
do not upload. A person can show the notice by running a gflow command from an interactive terminal.
The telemetry commands themselves do not show it.

## Turn usage uploads off or on

Run `gflow telemetry off` to stop local counting and uploads. Add `--delete` to also remove the local
usage database. You can also set `GFLOW_CLI_TELEMETRY=0` or `DO_NOT_TRACK=1` in the process
environment, `$GFLOW_CLI_HOME/.env`, or the working-directory `.env`. Any one of these can turn
uploads off. `gflow telemetry on` removes the local opt-out marker; environment kill switches still take
precedence, and uploads still require the notice to have been shown.

For exact switch status, run `gflow telemetry status`. The `gflow telemetry on` and `off` controls
are CLI-only by design; MCP exposes local statistics through `gflow_usage_stats` but does not expose
an opt-out command.

The server that receives reports is public at
[github.com/ffroliva/gflow-telemetry-endpoint](https://github.com/ffroliva/gflow-telemetry-endpoint),
so what it stores can be checked. The client sends to `https://gflow-telemetry.ffroliva.workers.dev/v1/report`;
the sender is `src/gflow_cli/telemetry_upload.py` in this repository.

## Notice

Notice version: 2

```text
gflow sends weekly usage totals to improve it, and we share overall figures publicly and with
sponsors. Commands, flag names, models, error codes, and your country as a total. No ID, prompts,
paths or account details. Each report is added to totals and discarded.
Turn it off: gflow telemetry off, or set GFLOW_CLI_TELEMETRY=0
See exactly what is sent: gflow telemetry show. Full notice: gflow docs TELEMETRY
```

The notice shown in the EU/EEA:

```text
gflow can send weekly usage totals to help improve it; overall figures are shared publicly and with sponsors. Commands, flag names, models, error codes, and your country as a total. No ID, prompts, paths or account details. Each report is added to totals and discarded.
In your region nothing is sent unless you agree: gflow telemetry on (undo: gflow telemetry off)
See exactly what would be sent: gflow telemetry show. Full notice: gflow docs TELEMETRY
```

## Controller and legal basis

The data controller is the gflow-cli maintainer (your.name Oliva); contact via the address in
[docs/SECURITY.md](SECURITY.md). **Pending legal review (#1164).**
