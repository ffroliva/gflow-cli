# Spike: a detached child gflow survives its parent and finishes a generation (#1194)

**Date:** 2026-11-15 · **Host:** Windows 11 · **Profile:** served flow.google.com ·
**Script:** [`scripts/dev/spike_1194_detach.py`](../../../scripts/dev/spike_1194_detach.py) ·
**Cost:** $0 (one `image t2i`)

## Question

`--no-wait` (owner decision) starts a detached child `gflow` that runs the queued job.
Does a child started with the telemetry sender's flags keep running after the process
that started it exits, and can it launch Chrome and finish a generation with nothing
attached to its console?

## Method

`spawn` starts `python -m gflow_cli image t2i "…" --profile P --json --out-file X` with
`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` (POSIX: `start_new_session=True`), stdin
from DEVNULL, and stdout and stderr to a per-job log file. It prints the child pid and
exits at once. The parent ran from an agent's shell tool, whose shell also exited.
`check` then reads the pid's liveness and the log.

## Result

| Observation | Value |
|---|---|
| Child alive after parent and its shell exited | yes (checked 10 s later) |
| `CREATE_BREAKAWAY_FROM_JOB` needed | no (not set) |
| Child finished | yes, exited by itself after ~37 s |
| Output | `--out-file` written (662 KB JPEG, the stem kept, extension from the bytes) |
| Log | the full `--json` envelope (`files[].local_path`, signed `fife_url`) |

## Conclusions

- The telemetry sender's flags are enough on Windows for a child to outlive its parent
  and drive Chrome. `--no-wait` can reuse them.
- stdout and stderr must go to a per-job log, not DEVNULL: it is the only place a child's
  failure would show before the queue row is written.

## Not measured

- A terminal **window** closing (the console's close event) while the child runs, and a
  parent inside a job object with kill-on-close (a CI step, some IDE terminals). The
  child has no console and its own process group, so neither should reach it, but this
  run did not prove it.
- POSIX (`start_new_session`) on this date. The telemetry sender uses it in CI on Linux
  and macOS.
