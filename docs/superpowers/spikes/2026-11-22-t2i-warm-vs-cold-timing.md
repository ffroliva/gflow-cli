# How much of an `image t2i` run is startup?

**Date:** 2026-11-22 · **Profile:** one account served `flow.google.com`
**Cost:** image quota only: 10 one-image generations (one from a run whose harness failed to
parse the output), no credits. Each project-creating sample left one empty project.
**Script:** [`scripts/dev/spike_t2i_warm_vs_cold.py`](../../../scripts/dev/spike_t2i_warm_vs_cold.py)

## Question

Would routing CLI commands to an already-warm browser (the MCP server's long-lived worker)
remove enough of a text-to-image run to be worth building? And is project creation a large
enough share of a run to justify a page-free create?

## Method

Three arms on one profile, N = 3 each, run in blocks (the warm arm holds the profile lease, so
a subprocess on the same profile cannot run alongside it):

- **warm:** one `FlowApiClient` entered once, then three `generate_image` calls into one project;
- **cold_reuse:** `gflow image t2i ... --project <id>` as a subprocess, timed end to end;
- **cold_create:** the same subprocess with no `--project`, so it creates a project first.

The thresholds were written in the script's docstring before the first run.

## Results

| Arm | Samples (s) | Median (s) |
|---|---|---|
| warm, per call | 20.3, 17.6, 17.7 | 17.7 |
| cold_reuse | 28.6, 28.0, 37.3 | 28.6 |
| cold_create | 29.6, 43.8, 31.4 | 31.4 |
| warm client entry (Chrome start, Flow load), once | 6.2 | |

- `warm / cold_reuse` = **0.62**.
- `cold_create - cold_reuse` = **2.8 s**.

## Readings

- **A warm worker removes about 11 s of a 29 s run (about 38 %).** The ratio sits in the
  pre-registered 0.6–0.85 band: worth building only with another reason, such as fewer Chrome
  launches and profile leases per session. With N = 3 and the ratio 0.02 above the lower
  threshold, the speed case is **not settled** by this run.
- **Project creation costs 2.8 s,** under the 5 s threshold. A page-free `jHPbke` create is
  not worth building for speed. Its case is reliability, which the typed click-timeout
  handling (#1434) already addresses on the page path.
- The warm arm is an upper bound on the benefit: a worker's queued path adds a queue round
  trip the in-process client does not pay.

## Not measured

- Where the 11 s goes inside a cold run (interpreter start, imports, lease, Chrome, Flow
  load, download, teardown): the arms time the whole command.
- Variance across accounts, hours and Flow load: one profile, one sitting.
- The worker's real queued path.
