# Error rate in the first telemetry week: what the totals do and do not show

**Date:** 2026-11-22
**Data:** the endpoint's `totals` table, week 2026-W46 (9–15 Nov): 19 reports, 11,053 commands
**Cost:** $0. Code reading, one offline repro and one read-only D1 query. No browser, no generation.
**Harness:** `scripts/dev/spike_create_click_timeout.py`; the export is kept in the gitignored
`scripts/dev/_spike_out/telemetry_totals_2026-11-22.json`, produced by:

```
wrangler d1 execute gflow-telemetry --remote --json \
  --command "SELECT week, field, key, n FROM totals ORDER BY week, field, n DESC"
```

## Finding 1: a report carries the version that uploaded it, not the one that ran

`telemetry.py:1088` stamps `"version": __version__` when the report is built, and the endpoint
builds `x:version_exit` from that field (`gflow-telemetry-endpoint/src/index.ts:245`). A W46
report is sent during W47, so each exit is attributed to the version installed at upload time.
That version is an upper bound on the one that ran.

v0.101.2 was tagged on 2026-11-17, after W46 closed, so no W46 command ran on it. The four
installs reporting v0.101.2 ran W46 on v0.100.0 or earlier and upgraded before their report
went out. Those four installs account for:

| | v0.101.2 reporters (4) | all 19 reporters |
|---|---|---|
| commands | 6,470 (59 %) | 11,053 |
| exit 7 | 804 | 841 |
| exit 23 | 144 | 145 |
| exit 1 | 503 | 873 |

The error groups are concentrated in four heavy, quickly-upgrading installs, not spread across
releases. A per-release error rate needs the version recorded with each counter, which is a
client change.

## Finding 2: a Create click that times out escapes `create_project` unwrapped (exit 1)

The largest crash group is `TimeoutError <- <playwright> <- …rpc_reply <- …create_project`:
457 crashes in `migrated_composer:_rpc_reply` (the module before the move) and 5 in
`_mc_common:rpc_reply`, so the path exists at HEAD.

`rpc_reply` (`_mc_common.py:1598-1609`) wraps only the `asyncio.wait_for` timeout into
`TransportTimeoutError`. The action it awaits first is the Create click,
`button.click(timeout=15_000)` (`migrated_composer.py:719`). v0.96.1 has the same structure.

`spike_create_click_timeout.py` drives `rpc_reply` with an action that raises Playwright's
`TimeoutError`. It escapes as `playwright._impl._errors.TimeoutError`, which is not a subclass
of the builtin `TimeoutError`. `create_project` catches only `RpcResponseUnavailableError` and
`TransportTimeoutError`, so the exception reaches the unhandled-error path: exit 1 and an
incident capture with no remediation.

`image t2i` creates a project when neither `--project` nor `GFLOW_CLI_PROJECT` is set
(`cli_image.py:355-361`). In W46, 4,149 of 5,027 `image t2i` runs passed no `--project`; that is
an upper bound on creations, since the setting is invisible to the flag counter.

**Not shown:** why the button stopped being clickable, and whether a `jHPbke` request left the
page before the timeout. A retry is safe only if no `jHPbke` request was sent, and that has to
be observed, not inferred from the exception type.

## Finding 3: exit 7 has two classes, and `create_project` reaches both on three branches

`EXIT_CODE_MAP` gives exit 7 to `WireFormatError` and, by inheritance, to
`RpcResponseUnavailableError`, a write whose reply body the page could not read (#992, #1009).
`create_project` ends in exit 7 when:

- the reply carries no project id (`migrated_composer.py:758`);
- the reply is lost or late and no navigation to a new project is seen, the "outcome unknown"
  branch built by `_write_outcome_unknown` (`migrated_composer.py:727-748`);
- the same lost-reply branch in the rename that follows (`migrated_composer.py:811-812`).

These are candidates, not an attribution: `WireFormatError` is raised from 20 modules, and
`image upscale` (63) exits 7 without creating a project. The totals carry no route, so the
share of the 723 `image t2i` exit-7s that come from project creation is **unmeasured**.

## Finding 4: bare `gflow` is a telemetry artefact of `--verbose` (#1209)

226 of the 230 `cli|gflow` commands carry `--verbose`. Their durations run past 5 minutes and
their neighbours in `transitions` are `video upscale` and `credits user`, so they are real
commands. Before v0.100.0 the flag-width helper counted `--verbose` as taking a value and
swallowed the subcommand name (`cc3ef3a5`, #1203/#1209). The command name is lost; the 47
exit-11 results under `gflow` are still `ConfigurationError`s (a class that includes
`ProfileLockedError`), on unknown commands.

## Finding 5: the update notice is CLI-only and outcome-blind

`maybe_notify_update()` runs in the root group callback (`cli.py:225-228`), before the command,
from a once-a-day PyPI cache. A failure never mentions a newer version, and an MCP client never
sees the notice.

## Open items

| Open item | Evidence that settles it |
|---|---|
| Source of exit 7 on `image t2i` and `image upscale` | the error `route` recorded with the exit, or a reporter's incident capture |
| Whether exit 23 is #1312 (English count label, fixed in v0.100.1) | country or locale crossed with exit; note that v0.96.1 carries the same selector and its 5 installs show no exit 23 |
| Why the Create button is not clickable | `elementFromPoint` over the button and a dialog inventory at the moment the click times out |
| Retry safety after a click timeout | a request watch showing no `jHPbke` left the page |
| Per-release error rate | the version recorded with each counter, not the report |
