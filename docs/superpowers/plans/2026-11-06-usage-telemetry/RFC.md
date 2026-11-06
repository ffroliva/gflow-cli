<!--
Draft body for the public design announcement issue (plan Task 0). Not posted.
Before posting: the maintainer reads the whole text and gives an explicit go. Then post it as an
issue and link it from the next release notes.
-->

# Planned: anonymous weekly usage totals for gflow-cli

**Status:** planned, nothing built yet. This announces the design before any code lands, so you know
exactly what will be counted and how to turn it off. Comments on what is collected are welcome.

## The short version

gflow-cli would keep **counts** of which commands you run, on your machine. You can read them at any
time with `gflow data stats`. Later, once a week, it would send those counts as an **anonymous total**:
no account, no ID, no prompts, no file names. You would see a notice before anything is sent, and one
command turns it off for good.

This changes a promise we make today. [`SECURITY.md`](https://github.com/ffroliva/gflow-cli/blob/main/docs/SECURITY.md) says "We don't ship
telemetry". That sentence will change, and this issue announces it before it does.

## Why

We know downloads and stars. We don't know which commands people use, which ones fail, or which
workflows matter, so the roadmap is guesswork. Bug reports reach us from a handful of people. Exit
codes counted across everyone would show us the failures nobody reports.

## What other CLIs do

| Tool | Default | No identifier? |
|---|---|---|
| Azure CLI, GitHub CLI, .NET CLI, AWS SAM CLI, Homebrew | on, with opt-out | no: each keeps an install or machine ID |
| Google Cloud CLI | asks at install | no |
| Go toolchain | counts locally, upload is opt-in | **yes**: weekly totals, no ID |
| **gflow-cli (proposed)** | counts locally; upload on after a notice, with opt-out | **yes**: weekly totals, no ID, nothing stored per install |

We are borrowing the Go model's privacy design (weekly totals, no ID) with an opt-out default like
the others.

## How it would work

**Two stages, released separately.**

1. **Local only.** gflow counts command names, exit codes and durations in a separate file on your
   machine. Nothing leaves it. `gflow data stats` and `gflow telemetry show` print what is counted.
   This ships first, as a preview you can inspect.
2. **Weekly upload**, at least one release later, and only after legal review:
   - A notice is printed **once**, the first time you use gflow in a terminal (usually right after
     `gflow auth login`). It is shown again only if what we collect changes, never on ordinary
     updates. It is never a question and never waits, so agents and MCP clients are not blocked.
     **Nothing is sent on that run**, and counts from before the notice are never sent.
   - Once a week, at a random moment, one small report goes to an endpoint we run on Cloudflare,
     with the data stored in the EU.
   - The endpoint adds the report to weekly totals and **throws the report away**. It never stores
     your IP address or anything that could link two reports. So there is nothing per person to
     look up or delete.
   - No notice shown means no upload. CI, containers and scripts that never show a terminal stay
     silent.
   - Upload never slows a command, prints anything, or changes an exit code.

## Exactly what would be sent

Every value is a name from gflow's own code, a size bucket, or a count. The list is complete; adding
a field means a new public release.

| Field | Example |
|---|---|
| gflow, Python, OS, CPU | `0.94.0`, `3.12`, `windows`, `x86_64` |
| How gflow was installed | `uv`, `pipx`, `pip`, `other` |
| Install age | `<1w`, `<1m`, `<6m`, `6m+` |
| Storage type | `local`, `s3`, `gcs`, `other` (never the address) |
| Commands, flags, exit codes, durations, CLI or MCP | `video t2v`: exit 0 twice, exit 11 once |
| Which command followed which | `image t2i -> video i2v`: 3 |
| Models, modes, aspect ratios | names gflow knows; anything else is `other` |
| MCP client | the client's name from a fixed list of well-known clients; anything else is `other` |
| Unexpected crashes | error type and where in gflow's code: `TimeoutError at api.client:post` |
| Prompt tools | packaged tool name and whether it changed the prompt; your own tools count as `custom` |

The endpoint also counts the **country** a report came from (Cloudflare provides it). It is a total,
never stored with the report.

## Never sent

Prompts. File names or paths. Storage addresses. Your Google account, email or name. Project, media
or character IDs. Credit balance. Profile names. Error messages, stack traces or logs. Any value you
typed into a flag. The names or settings of your own prompt tools.

## Turning it off

Any of these stops counting **and** upload:

- `gflow telemetry off` (add `--delete` to wipe the local counts too)
- `DO_NOT_TRACK=1`, the cross-tool convention
- `GFLOW_CLI_TELEMETRY=0`

We will never report that you opted out. That would be tracking in itself.

## What we would do with the totals

Decide what to fix and build next, and publish aggregate figures, for example "40% of runs are video",
including to sponsors. Figures under 5 are never published. Any published number says it is a lower
bound and how it was estimated.

## Open questions

- **EU users.** UK law allows this kind of anonymous statistics with a clear notice and easy opt-out.
  The EU position is less settled. Depending on legal advice, EU users may be asked first instead of
  opted in. Upload will not ship until this is answered.
- **Sharing totals with sponsors.** We are checking that this fits the legal basis above. If it does
  not, either the sharing or the default changes.

## Comments

Tell us here if a field worries you, if something is missing from the "never sent" list, or if you
see a way a value could identify you. Those comments shape the final list.
