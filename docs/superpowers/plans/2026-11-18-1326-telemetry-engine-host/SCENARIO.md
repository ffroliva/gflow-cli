# Scenario: count the browser engine and the served Flow host in the weekly report (#1326)

Feature: two closed-enum counter maps in the weekly telemetry report, `engines`
(`playwright`, `patchright`, `other`) and `hosts` (`labs`, `flow`, `unknown`), counted once
per command or queued job that launched a browser. The client is `gflow-cli`; the ingest
endpoint is the `gflow-telemetry-endpoint` Worker with its D1 database.

## Coverage map

| Dimension | Applies | Why |
|---|---|---|
| D1 Auth & session | No | No auth state is read or written |
| D2 WAF / reCAPTCHA | No | No Flow request is added |
| D3 Selector drift | No | No selector; the host comes from `page.url` |
| D4 Batch & resume | Yes | A batch is one command; it must count once |
| D5 Concurrency | Yes | The MCP worker drives browsers in its own process |
| D6 Data layer | Yes | The local `telemetry.db` counters table gains two kinds |
| D7 Errors & exit codes | Yes | A run that fails after launch still counts; nothing may raise |
| D8 Cross-platform | Low | The engine is a setting; the host is a parsed URL |
| D9 Transport | Yes | The endpoint rejects unknown keys today |
| D10 Headless / headed | No | Both launch through the same point |
| D11 Input validation | Yes | The endpoint must accept only the closed enums |
| D12 Observability | Yes | A 400 from the endpoint is currently silent |
| D13 MCP parity | Yes | Direct MCP tools and queued jobs must both count |

## Scenario table

| # | Scenario | Dim | Severity | Expected |
|---|---|---|---|---|
| S1 | New client uploads to the endpoint before the endpoint is deployed | D9 | Critical | Prevented by order: the endpoint deploys first (deployed 2026-11-18). A 400 is treated as sent and is not logged, because the detached upload has no log sink; redeploying the endpoint from a branch without the new key kinds would drop browser weeks silently |
| S2 | Old client (no new keys) uploads to the new endpoint | D9 | Critical | 204, totals as before |
| S3 | Report carries `engines: {"chromium": 1}` or a raw hostname in `hosts` | D11 | High | 400, nothing stored |
| S4 | Command never launches a browser (`gflow docs`, `telemetry show`) | D7 | High | Neither map counts it |
| S5 | Browser launches, then the run fails before navigation | D7 | Medium | Engine counted; host counted as `unknown` |
| S6 | Routing reads the URL twice on a retry | D4 | Medium | One count per command (set, not increment) |
| S7 | Queued MCP job runs in the worker process | D5, D13 | High | Counted once per job by the worker |
| S8 | Direct MCP tool drives the browser in the server process | D13 | High | Counted at tool return, as the CLI is |
| S9 | `GFLOW_CLI_BROWSER_ENGINE` holds a value outside the enum | D11 | Low | Counted as `other` |
| S10 | EU/EEA user consented to notice v2 | D12 | High | No upload until notice v3 is shown and consented |
| S11 | Telemetry is off or killed | D6 | High | Nothing recorded |
| S12 | `telemetry.db` is locked | D6 | Low | The count is dropped; the command is unaffected |

## Must-cover before merge (Critical + High)

S1, S2, S3, S4, S7, S8, S10, S11.

## Deferred

- A cross of host by exit code (`x:host_exit`): it needs the host per command in the
  `exits` map. Revisit once report volume makes a per-host failure rate meaningful.

## Suggested BDD scenarios

```gherkin
Scenario: a browser-driven command counts its engine and served host once
  Given telemetry is on
  When a command launches the playwright engine and Flow serves flow.google.com
  Then the week's report has engines {"playwright": 1} and hosts {"flow": 1}

Scenario: a command that never launches a browser counts neither
  When `gflow docs` runs
  Then the report has no engines and no hosts entry

Scenario: the endpoint refuses a value outside the enum
  When a report carries engines {"chromium": 1}
  Then the endpoint answers 400 and stores nothing

Scenario: an old client is still accepted
  When a schema 1 report without engines or hosts is uploaded
  Then the endpoint answers 204
```

## Known-issues cross-reference

None. Host values follow the Host-Membership Discipline in `AGENTS.md`: the field records
the host Flow served and supports no capability claim.
