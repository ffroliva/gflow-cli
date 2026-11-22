# Phase timing of a cold `gflow image t2i` run

**Date:** 2026-11-22 · **Profile:** one account served `flow.google.com`
**Cost:** image quota only: 5 one-image generations, no credits. Each sample created one
project (left in the account).
**Script:** [`scripts/dev/spike_t2i_phase_timing.py`](../../../scripts/dev/spike_t2i_phase_timing.py)

## Question

Where does the non-generation time in a cold `gflow image t2i` run go? The earlier
warm-vs-cold spike measured 11 s of overhead but could not split it.

## Pre-registered reading

- Chrome launch + Flow load >= 60% of non-generation time: the resident browser removes
  the dominant cost.
- Imports >= 40%: trimming them helps CLI and MCP both.
- Neither dominates: neither fix is worth it alone.

## Method

In-process instrumentation of the production code path with `time.perf_counter` around
each phase boundary: imports, `FlowApiClient.__aenter__` (Chrome launch + Flow load),
`create_project`, `generate_image` and `__aexit__` (teardown). N = 5, sequential, same
profile.

Limitation: `imports` is 0 for samples 1-4 because Python's import machinery caches
modules across `asyncio.run()` calls. Sample 0's 0.5 s is a lower bound (the spike's own
imports already pulled some of the chain).

## Results

| Phase | Samples (s) | Median (s) | Share of non-gen |
|---|---|---|---|
| imports | 0.5, 0.0, 0.0, 0.0, 0.0 | 0.0 | 0% (cached) |
| client_enter (Chrome + Flow load) | 6.4, 5.8, 5.8, 6.5, 7.7 | 6.4 | 55% |
| create_project | 3.7, 3.0, 3.0, 3.9, 3.9 | 3.7 | 32% |
| generate | 19.0, 16.6, 18.3, 16.7, 17.0 | 17.0 | — |
| teardown | 0.4, 0.4, 1.5, 0.6, 0.5 | 0.5 | 5% |
| **total** | 29.5, 25.9, 28.6, 27.7, 29.0 | **28.6** | |

Non-generation median: 11.6 s.

## Readings

- **Chrome alone (55%) does not cross the 60% threshold.** A resident browser that
  only skips the Chrome start is below the pre-registered bar for "removes the dominant
  cost".
- **Chrome + project creation together (87%) do.** A resident browser that also reuses
  a project across runs would remove 10.1 of the 11.6 s overhead, leaving only imports
  and teardown (0.5 s each).
- **Imports are unmeasured at 0% (cached).** A subprocess timing or `python -X importtime`
  would give the real number. The 0.5 s from sample 0 is a lower bound.
- **Teardown is cheap (0.5 s).** Not worth optimising.

## Decision

A resident browser is worth building **only if it also reuses a project.** Chrome alone
does not cross the pre-registered threshold; Chrome + project reuse removes 87% of the
overhead.

## Not measured

- The import cost as a subprocess (the in-process measurement is confounded by caching).
- Whether `--project` with an existing project is cheaper than `create_project` (the
  in-process path creates a new project every time).
- Variance across profiles, times of day and Flow builds.
