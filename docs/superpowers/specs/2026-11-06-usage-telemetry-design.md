# Usage metrics: default-on, no identifier, opt-out

Status: **draft.** Direction set by the maintainer on 2026-11-06: default-on with opt-out, as complete
as privacy law allows. `/gflow:predict` returned CAUTION; its mitigations are in this document. Legal
review is still required (see "Gates").

## Why

We know downloads and stars. We do not know which commands people use, which ones fail, or how
workflows are put together, so roadmap priorities are guesses.

## How the industry does it

| Tool | Default | Collects | Transport |
|---|---|---|---|
| az | opt-out | OS, command, parameters, result | local save, hidden background process |
| gh (v2.91.0) | opt-out | pseudonymous; subcommands and flags; `GH_TELEMETRY=log` previews | |
| .NET CLI | opt-out, first-run notice | usage | |
| AWS SAM CLI | opt-out | command names, Python, OS, errors | |
| Homebrew | opt-out | usage events | background process, HTTPS, fails fast |
| Marigold CLI | opt-out, **no identifier**, nothing sent on the run that shows the notice | invocations | |
| gcloud | opt-in at install | command, duration, errors; no argument values | |
| Go toolchain | local by default, upload opt-in | weekly totals, no ID, public reports | weekly HTTP POST, versioned config download |
| kubectl | none found | | |

Shared mechanics, all adopted here: names never values, an environment kill switch, `DO_NOT_TRACK`, a
way to see the exact payload, and never slowing or failing a command.

## The central idea

**No identifier, and no stored reports.** Opt-out with a persistent ID is the weak case in law: an ID
is likely personal data, and the UK statistical exception does not cover user-level tracking. Removing
the ID is not enough alone, because stable fields plus exact counters form a fingerprint that could
link one install's reports across weeks. So the endpoint adds each report to weekly totals and discards
it. Nothing per-install is ever stored.

| Question | How it is answered |
|---|---|
| Weekly active installs | a total incremented once per report |
| New vs established users | `install_age` totals |
| Duplicate reports | random `report_id`, one per week, remembered 14 days with no content |
| Which commands, flags, failures | counter totals |
| How workflows chain | transition totals |

**The price:** a cross must be chosen before the data arrives, because reports are gone afterwards. The
endpoint holds the whole report at ingest, so a new cross needs no client change, but it only counts from
that day on. Crosses in v1: surface by command by exit; `os` by exit; `version` by exit; `os` by
`install_age`; `installer` by `update` exit; `storage` by exit. Following one install across weeks is impossible by design. Counts are also a lower
bound: plugin MCP users who skip `mcp setup`, and people who try gflow for under a week, never report.

## Goals

- Answer: which commands, flags and models, CLI or MCP, which MCP clients, how often they fail, how
  long they take, how commands chain, how new the user base is, where unexpected crashes happen,
  which prompt tools are used and whether they changed the prompt.
- Updates: how fast each release is adopted, and whether `gflow update` fails per installer.
- `gflow data stats`: the user's own numbers, offline.
- Reach everyone who has seen the notice, by default.

## Non-goals

- Learning **who** users are, or **what** they make. Prompts are never collected.
- Per-run traces. Weekly totals only.
- A public dashboard. The PyPI BigQuery breakdown lives in the other repo.
- Uninstall tracking. `uv tool uninstall` never runs our code, and falling `install_age` totals show
  churn well enough.
- `gflow feedback` (person-initiated report, opens a prefilled GitHub issue, offers the latest incident
  report; extends #707) and `gflow purge` (removes the local data an uninstall leaves behind). Both are
  separate work.

## Anti-goals: this has failed if

- A prompt, path, URI, profile name, ID, or raw error text leaves the machine.
- Anything is sent before the notice has been shown, or any week counted before that moment.
- A command gets slower, prints anything beyond the notice, or fails because of telemetry.
- The endpoint stores anything that could link two reports.
- An opt-out is lost.

## Design

**Local state.** `<home>/telemetry.db`, a stdlib SQLite file separate from the catalog, so counters
never sit in a database holding prompts. Tables: `counters`, `meta`, `reports` (one row per week:
`report_id`, `due_at`, `sent_at`). Counts are by ISO week, kept 53 weeks. No connection is cached, and
every failure is silent. The catalog sees only generations and cannot answer "which commands", so this
is new local collection.

**Opt-out state.** A separate sentinel file, `<home>/telemetry.off`, that nothing but `off` and `on`
touches, so no writer can undo it. Unlike everything else here, `off` fails loudly if it cannot write.
Unreadable state means no upload.

**Hook sites.** `click.Group.invoke` for the CLI and `_guarded` for MCP, which every tool already
passes through. Which hook fired is the CLI/MCP split. An outcome is a string: the exit code, `queued`
for an MCP call that returned `pending`, or `error` for an MCP failure with no code. Ctrl-C records
`130`. `--help` records nothing. On MCP the write runs in `asyncio.to_thread`, because a locked database
stalled the event loop for about 300 ms when measured.

**Names only, by construction.** The CLI hook walks the arguments Click received against the Click
tree and keeps only names that exist in that tree. A positional prompt cannot become a key.

**Bug data has three levels.** Default-on, in the weekly totals: exit codes per command, and a crash
location for unexpected errors, such as `TimeoutError at api.transports.ui_automation:submit`. That
key holds only the exception class and the innermost frame inside `gflow_cli`, as Go's stack counters
do. Local only: incident bundles, unchanged and never uploaded. Person-initiated: `gflow feedback`.
Exception messages, stack traces and logs are never sent automatically, because they can hold prompts
and paths.

**Automated runs.** Reporting never prompts, never waits, and never changes an exit code. MCP clients
still get only the opaque incident reference.

**Transitions.** `meta` holds the previous command name and time. A next command within 30 minutes
increments `previous -> next`. The pair is only ever a weekly total.

**Notice.** Printed once per version to stderr when stderr is a TTY: at the first interactive command,
after "Session saved" in `gflow auth login`, and in `gflow mcp setup`. Never for `gflow telemetry`.
Nothing is sent on the run that shows it. **No notice shown on this machine means no upload**, so CI,
containers and piped-only use stay silent. A new version re-shows it, and upload waits until it has
been seen. Never a prompt.

**Opt-out.** `gflow telemetry off` stops counting and upload, and a running MCP server honours it.
`off --delete` also wipes local data. Remote deletion is impossible because nothing per-install is
stored, and the notice says so. `on`, `status` and `show` complete the group, and all work without a
TTY. `on` under a kill switch persists, says what still blocks it, and exits 0.

**Kill switches.** `GFLOW_CLI_TELEMETRY` set to anything but `1`, `true`, `on` or empty, or
`DO_NOT_TRACK` set to anything but empty, `0` or `false`, stop everything. They are read from the
environment and from both `.env` files, and any source can only turn telemetry off. They are not a
`Settings` field, so a bad value can never exit 11.

**Upload.** Counting is offline; nothing is sent per command. One report per install per week, so
1,000 installs make about 1,000 small requests a week. Rules:

- Only from a package index install, never CI, and never after opt-out or a `410`.
- Only weeks that started after the notice was seen. Earlier local counts stay local.
- A report becomes due at a random moment up to 7 days after its week closes, so reports spread
  across the whole week. It is drawn fresh each week, because a fixed slot per install would be a weak
  identifier.
- One report per attempt, at most one attempt a day, checked at each CLI command and MCP tool call.

A daemon thread, the `update_check.py` pattern, delivered 0 of 16 POSTs when the process lived 0.5 s or
less. So the upload runs in a detached child process with no window, as az and Homebrew do. It uses
stdlib `urllib`, a 5 s timeout, no redirects, and never reads the response body. The endpoint is a
constant with no override, so a cloned repo's `.env` cannot redirect it.

**Plain REST, with the reply as the only coordination.** No pub/sub, broker or client polling.

| Reply | Client action |
|---|---|
| `204` | mark the week sent; rows stay, so `gflow data stats` keeps its history |
| `400`, `413` | mark it sent; retrying a rejected report is pointless |
| `429` + `Retry-After` | wait that long, plus a random extra of up to 6 hours so retries do not arrive together |
| `5xx`, network error | keep the week, try another day |
| `410` | persist `upload_disabled`, never send again |

**Load control.** The client's stored week is the queue, so a late report loses nothing. D1's free tier
allows 100,000 row writes a day. If a write fails on that limit, the endpoint answers `429` with a
`Retry-After` reaching past 00:00 UTC, when the limit resets, and the extra random wait spreads the
retries. At about 200 rows per report, the free tier carries roughly 3,500 installs a week. The paid
tier is the next step, with no client change.

`410` is the server-side stop. It ships in the first upload release because installed versions cannot
be retrofitted. The allow-list is never changed remotely; a new field is a public, versioned release.

**Payload allow-list, complete:**

| Field | Values |
|---|---|
| `schema`, `report_id`, `week` | integer; random per week; ISO week |
| `version`, `python`, `os`, `arch` | release; major.minor; windows, linux, darwin, other; x86_64, arm64, other |
| `installer` | uv, pipx, pip, other |
| `install_age` | `<1w`, `<1m`, `<6m`, `6m+`, from `first_seen` in `meta` |
| `storage` | local, s3, gcs, other: the scheme of `GFLOW_CLI_STORAGE_URI` only. S3-compatible services such as MinIO all count as `s3`; telling them apart would mean reading a private endpoint |
| `commands`, `flags`, `exits`, `durations` | keyed by surface, then command. `exits`: `{"cli": {"video t2v": {"0": 2, "11": 1}}}`. Durations: `<10s`, `<1m`, `<5m`, `5m+` |
| `transitions` | `{"image t2i -> video i2v": 3}` |
| `models`, `modes`, `aspects`, `outcomes` | from the catalog, opened read-only, so what actually ran. Values outside the known enums become `other` |
| `mcp_clients` | known names from the MCP `clientInfo`; anything else becomes `other` |
| `crashes` | keyed by surface: `{"cli": {"TimeoutError at api.client:post": 1}}` |
| `tools` | prompt tool use, by name and result: `{"prompt-architect": {"expanded": 3, "unchanged": 1}}`. Only unmodified packaged tools keep their name; every "My Tools" entry, including one that shadows a packaged name, counts as `custom` |

Every value is a name in our own code, a bucket, or a count. Built field by field, never by filtering.

**Never sent:** prompts, file names, paths, storage URIs, Google account, email address or account name
(including `.gflow_account`), project or media ids, credit balance, characters, profile names, error
text, stack traces, logs, free-form values of any flag, user tool names, tool options, the LLM endpoint
or model a tool used. A user who wants to be contacted volunteers it
separately; it is never read from the machine.

**Conventions.** Commands and flags follow the big CLIs: `gflow telemetry on|off|status|show`,
`gflow data stats`, `gflow feedback`, and `DO_NOT_TRACK`.

**The endpoint** stores totals by week, field and key, plus seen `report_id`s for 14 days. It never
stores the IP or the report body, and logs nothing per request. Country is derived at the edge, so it
is not in `show`; the notice must list it. It is an incremented total only. Anything published hides
totals under 5.

**Promise.** `docs/SECURITY.md:20` and `:129` say gflow ships no telemetry and no usage stats. PR 1
rewords them to say what is true then: local counters, nothing leaves the machine. PR 2 changes them
with the uploader. The daily PyPI update check is named in the same place. ADR #9 ("no SaaS
dependencies", `PLAN.md:714`) is amended explicitly in PR 2.

## Estimating real numbers

Reports undercount: opt-outs, CI, machines that never saw the notice, and people who leave within a week
send nothing. No client code measures this, and nothing may be added to do so:

- **Never** send an opt-out signal. "User said no" is itself tracking.
- **Never** count opted-out installs through another route, such as moving the update check to our own
  endpoint. That would quietly defeat the opt-out.

The estimate is built from public data, outside this repo:

| Figure | Source |
|---|---|
| Floor | weekly reports, measured |
| Ceiling | PyPI downloads of the same release in the same window, excluding mirrors |
| Shares, such as % video | used directly, assuming opt-outs behave like everyone else |

Any figure shared outside the project gives the measured floor, the estimated range and the method, and
is never presented as measured. Shares are more reliable than counts, but privacy-minded users may
differ, and that is stated too.

## Law, as researched (not legal advice)

- **UK:** the PECR statistical exception permits this without consent if statistics are the sole
  purpose, information is clear, and objecting is simple and free. It excludes user-level tracking and
  profiling. Commentary only so far; the ICO text and its case studies must be read.
- **EU:** no EU-wide exception today. Article 5(3) of the ePrivacy Directive generally requires consent
  for analytics; France and Spain have narrow exemptions and Germany does not. The EDPB says it does
  not apply to processing that stays on the device, which covers PR 1. An aggregated audience
  measurement exemption is in the Digital Omnibus, but that is still in trilogue.
- **Consequence:** default-on is supported for the UK and most of the world, but not settled for EU
  users. See open decision 2.

## Open decisions

1. **Central store: decided 2026-11-06.** Cloudflare Worker + D1, created with `--jurisdiction=eu`.
   **Created 2026-11-06:** D1 `gflow-telemetry`, id `4dd3ffe8-e5cb-4ed6-95c0-60a817c893ee`,
   `jurisdiction: eu`, region EEUR, empty. Cloudflare account `ffroliva` (`6e84ee0d4c899e5b5fa648302e0bfba9`,
   login ffroliva@gmail.com). The same login also reaches another account, so every wrangler call
   pins `CLOUDFLARE_ACCOUNT_ID`, and the Worker's `wrangler.toml` must set `account_id`.
2. **EU users:** for counsel. Default-on for all, or a consent prompt where the machine's timezone is
   in the EU. Not built until decided.
3. **Data controller:** who the notice names.
4. **Uses of the totals: decided 2026-11-06.** Improving the tool, and aggregate figures shared
   publicly and with sponsors. The notice states both. **This is the main legal risk:** the UK exception
   needs statistics for improving the service as the sole purpose. Counsel must answer before PR 2
   merges. If the answer is no, default-on upload does not ship as designed.

## Gates

Public RFC before any code, with a public poll and a stop threshold set in advance, because an issue
alone reaches few people. PR 1 is local only: no notice, no network. It ships as a one-release public
preview of `gflow telemetry show`. Notice, `on`, `off` and upload land together in PR 2, after legal
review and with a live endpoint.
