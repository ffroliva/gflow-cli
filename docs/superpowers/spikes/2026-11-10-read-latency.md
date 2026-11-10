# Spike: latency of a direct read vs the browser read (#1225)

**Date:** 2026-11-10 · **Profile:** `denon82` (Flow served `flow.google.com`) · **Base:** `develop`
ca069fca · **Cost:** $0 (reads only, no submit, upload, create or delete)
· **Script:** [`scripts/dev/spike_read_latency.py`](../../../scripts/dev/spike_read_latency.py)
· **Part of:** #1229 (S5)

## Question

What are the p50 and p95 wall times of three reads? Each is measured on the browser path (cold
Chrome and warm page) and on a direct `batchexecute` call (fresh token and cached token). The
reads are:

| read | rpc |
|---|---|
| status of one generation | `as29s` |
| project listing | `Zzl0ze` |
| balance | `nzlxg` |

Is the direct tier worth maintaining for speed?

## Method

All arms use the same profile, project and finished clip (the newest in the local catalog), with
N = 10 per arm per read. The arms ran one after another on an otherwise idle Windows machine, on
one network. The pre-registered reading is in the script's docstring.

| arm | what is timed |
|---|---|
| **browser cold** | A new `FlowApiClient` (Chrome start, profile lease), the route's `goto`, and the page's own reply, read with gflow's `_rpc_reply`. For `nzlxg` it is `client.get_credits()`, exactly what `gflow credits` runs. The clock stops at the reply. Client entry and teardown are timed apart. |
| **browser warm** | One `FlowApiClient`. The page is parked on `about:blank` (untimed), then the route's `goto` and the page's own reply are timed. |
| **direct fresh** | A new httpx client, with the cookie jar already in memory: one GET of the shell for `at` (S1, #1221), then the POST. This is what a one-shot CLI call would pay. |
| **direct cached** | One long-lived httpx client and a cached `at`: the POST only. This is what a poller would pay. |

A direct call counts only if it decodes to a frame of its rpc. All 60 did. The disk cookie read
(`browser_cookie3`) is timed apart.

## Observed

Wall time in seconds, N = 10. With N = 10, p95 is the maximum.

| read | browser cold p50 / p95 | browser warm p50 / p95 | direct fresh p50 / p95 | direct cached p50 / p95 |
|---|---|---|---|---|
| `as29s` status | 8.61 / 9.80 | 1.92 / 2.15 | 1.44 / 1.57 | **0.74** / 0.85 |
| `Zzl0ze` listing | 7.28 / 10.16 | 1.20 / 1.40 | 1.13 / 1.30 | **0.35** / 0.50 |
| `nzlxg` balance | 6.77 / 9.17 (n = 9) | 0.42 / 0.61 | 0.92 / 1.09 | **0.23** / 0.28 |

| p50 ratio | `as29s` | `Zzl0ze` | `nzlxg` |
|---|---|---|---|
| browser warm / direct cached (the pre-registered test) | **2.6x** | **3.4x** | **1.8x** |
| browser cold / direct fresh (one-shot call, reply only) | 6.0x | 6.4x | 7.3x |

Other measurements:

- **Cold client entry** (Chrome start through a ready page) is 6.17 s at p50 and 8.23 s at p95
  (n = 30). It is most of every cold read.
- **Cold teardown** is 31.4 s at p50 (n = 29). In 29 of 30 cold cycles the graceful
  `context.close()` ran into its 30 s bound (`_engine._CONTEXT_CLOSE_TIMEOUT_S`) and fell through
  to the force-close (`browser_teardown.context_close_error`). One real
  `gflow credits user --profile denon82` took **40.4 s** of wall time and logged the same
  warning. For a one-shot CLI read, the teardown costs more than the read.
- **Errors:** one cold `nzlxg` read raised `FlowAppError`, so that arm has n = 9. The warm and
  direct arms had none. No warm sample was under 100 ms, so no stale reply was caught.
- **Disk cookie read:** 0.15 s (2.05 s on the first run of the day, including imports).
- **A fresh `at` GET on an open connection:** 0.12 s. Most of the direct-fresh cost is a new TLS
  connection, not the token.

## Verdict against the pre-registered reading

The pre-registered test compares the warm browser with the cached direct read, at p50:

- **`nzlxg` (balance), 1.8x:** under 2x, so **not worth it for speed alone**. A warm page
  re-fetches the balance in 0.42 s.
- **`as29s` (status), 2.6x, and `Zzl0ze` (listing), 3.4x:** in between, so **worth it only with
  another reason**. No read reaches 5x against a warm page.

**The other reason is the cold path, which this spike measured.** A one-shot read pays a Chrome
start (about 6 s) plus a teardown (about 31 s today), against about 1 s direct. That is 6 to 7x
on time-to-reply and about 30x on wall time. A poller that keeps a warm page gains 2 to 3x; a
CLI call that has to start Chrome gains an order of magnitude. The case for the direct tier is
"no Chrome per call", not "faster than a warm page".

## What this does not establish

- **One profile, one network, one Windows machine, one afternoon.** These are absolute numbers
  for this setup, not a distribution across users.
- **The direct path's fallback cost.** When the shell carries no `SNlM0e` (#1221, not
  reproduced), the direct path must fall back to the browser. How often that happens is not
  measured.
- **Download time.** The signed-URL fetch is excluded: it is the same with or without a page.
- **The teardown timeout's cause.** It was observed through gflow's own `FlowApiClient` and
  the CLI. Why the graceful close does not finish within 30 s on this profile is not
  investigated here. It is a candidate follow-up issue.
- **A running (not finished) generation's `as29s`.** Only a finished clip was read.
- **Concurrent load.** Each sample ran alone.

## Wire table

`docs/FLOW_WIRE_RPCS.md` is not on `develop` yet (PR #1219). No row changes: this spike measures
time, not wire shape.

## Reproduce

```bash
uv run python scripts/dev/spike_read_latency.py denon82 10
```

The run takes about 25 minutes and opens 31 headed Chrome windows. Take the `browser-session`
lease first if other agents are registered. The JSON goes to
`scripts/dev/_spike_out/read_latency.json` (gitignored). It holds no token, cookie or id.
