# Spike: how Flow's RPC descriptors change between builds (#1223, #1224)

**Date:** 2026-11-10 · **Profile:** none (no cookies, no browser) · **Cost:** $0. Every
request is a GET of public JavaScript. · **Script:**
[`scripts/dev/spike_rpc_churn.py`](../../../scripts/dev/spike_rpc_churn.py) (reading
pre-registered in its docstring, committed before the first run) · **Builds measured:**
`boq_labs-ai-sandbox-frontend_20260929.10_p1` and `…_20261110.00_p0`

## Question

- **#1223:** across Flow builds (`bl`), how often do (a) RPC ids, (b) the feature-module
  names and (c) the descriptor set change?
- **#1224:** does an RPC descriptor carry anything stable enough to re-find a function
  after its id changes?

## What data existed

- **No per-build archive.** The nightly archive (#1230) writes `tmp/rpc-inventory/` in the
  checkout it runs in. Its one live write, for `20261108.05_p0`, was in a worktree that has
  since been removed. Only the counts quoted in PR #1230 and the
  [served-JS spike](2026-11-10-rpc-ids-in-served-javascript.md) remain: 261 descriptors and
  17/17 catalogued ids. The id list itself is gone.
- **Old script URLs.** Spike captures from September in `scripts/dev/_spike_out/`
  (gitignored) record the `/_/js/` URLs Flow's pages loaded for three builds:
  `20260903.13_p1` (en_GB), `20260909.10_p0` (en_US) and `20260929.10_p1` (en_GB).
- **Today:** five shell GETs all served `20261110.00_p0`, so no rollout was in progress
  while the spike ran.

## Method

For each build, the script sends a GET for the recorded script URL with `m=` set to the
four feature modules, the same swap the nightly canary makes. It then sends one GET per
module to place each id in its modules. When the swapped URL answered 404, it re-sent the
exact URL the old page had requested, once, as a control. The descriptor shape is learned
from where the 17 catalogued ids sit, not hard-coded. Replaying a September URL is the
edge of "traffic the app sends": the app did send that URL, but from an older page.

Three runs. Run 1 found that the 20260929 bundle held no `{K:"id",…}` descriptor. Its ids
sit in a constructor instead, so run 2 added that shape. Run 2's constructor regex matched
37 of the 219 calls, which made 230 ids look added. That was a parser artifact. Run 3 found
each options array by its method path, and its numbers are the ones below. The docstring
records both amendments.

## Results

| Build | Script URL today | Descriptor shape | Ids | Catalogued (17) | 4 feature modules |
|---|---|---|---|---|---|
| `20260903.13_p1` | **404** (control 404) | n/a | n/a | n/a | n/a |
| `20260909.10_p0` | **404** (control 404) | n/a | n/a | n/a | n/a |
| `20260929.10_p1` | 200, 6.8 MB | `new _.Gx("id",<req>,<resp>,[_.dm,!0,_.cm,"/Service.Method"])` (219), `new _.Hx(…)` (3, streaming) | 222 | 17/17 | all 200 |
| `20261108.05_p0` | not fetched; PR #1230 and the #1224 issue text | `{Xa:"id",hb:…,Ya:…,kb:…}` | 261 | 17/17 | all 200 |
| `20261110.00_p0` | 200, 7.1 MB | `{Wa:"id",hb:void 0,Za:!0,Xa:_.tC,kb:_.GDa}`, wrapped in `new _.kC(…)` | 267 | 17/17 | all 200 |

**20260929 → 20261110** (measured, about six weeks apart):

| Measure | Result |
|---|---|
| Id set | 222 → 267: **219 kept, 48 added, 3 removed** (`/FlowSharingService.GetSharedProjects`, `/InternalPeopleService.GetPhoneAcledProfilesByPhone`, `/AiSandbox.CheckFeatureAvailability`) |
| Catalogued ids | 17/17 in both builds |
| Feature-module names | `XRV0Af`, `wO1vlb`, `wj3Fuc`, `zxMy9c` answered 200 on both builds |
| Flag (`!0`/`!1`) | **219/219** kept. It is binary: about 45% `!0` and 55% `!1` on each build |
| Method path (`"/Service.Method"`) | **0/219**. The new bundle has none of the 222 paths. 4 of the 10 service names still occur, but not next to descriptors |
| Field keys, field values, variable name | **0/219** each |
| Module placement | 198/219 (90%) |
| Previous / next descriptor | 145/219 (66%) each; bundle order changed |
| Quoted non-id literals within 400 chars | 204/219 (93%) keep at least half |

The 17 catalogued ids map to their backend methods on 20260929. That table is now in
[`FLOW_WIRE_RPCS.md`](../../FLOW_WIRE_RPCS.md#backend-method-of-each-id-build-2026092910_p1).

## Verdict, against the pre-registered reading

- **Old builds (arm B):** the 20260929 URL answered 200 and the two earlier URLs answered 404,
  with the controls 404 too. Old builds can be recovered for at least six weeks but not
  for nine. The two 404s are also the two oldest locales captured, so locale and age are
  confounded. One reading holds either way: data more than about two months old cannot be
  recovered after the fact.
- **#1223 (a) RPC ids:** the 17 catalogued ids did not change across the measured span. The
  whole set moved 51/222 = 23%, which is above the 5% line, so the pre-registered reading
  is **real churn**. Almost all of it is additive: 98.6% of the old ids were kept.
- **#1223 (b) module names:** the same four names worked on both builds.
- **#1223 (c) descriptor set:** the format changed at least twice. Sometime between 20260929
  and 20261108 it went from constructor to object (and lost the method path). Between
  20261108 and 20261110 the key was renamed `Xa` → `Wa`. That second change comes from the
  issue text's example and PR #1230's live run, n=1.
- **Frequency is unmeasured.** Two measured builds six weeks apart, with an unknown number of
  builds in between, cannot give a rate per build. The nightly archive is what #1223 asked
  for, and it is still the only way to get one.
- **#1224:** of the candidates, only the flag clears 95% (219/219), and it is a single bit.
  The field keys, field values, variable names and the method path score 0%. The literals
  (93%) and module placement (90%) are **weak**: tiebreakers only. The neighbours (66%) are
  **weak** as well. The method path was a strong fingerprint while it existed, and Flow
  removed it. **Nothing in a descriptor is stable enough to re-find an id by itself.** A
  rename would have to be matched by flag + literals + modules + neighbours together, and
  only for ids that move alone.

## Effect on the nightly canary (#1218)

On `20261110.00_p0` the merged `RPC-DRIFT` check read **`PARSE-CHANGED`**, with 0/0
catalogued ids and 0 descriptors. Its regex hard-coded `{Xa:"`, so the first new build it
met archived nothing. This branch makes the check learn the key from where the catalogued
ids sit, falling back to the most common `{<key>:"<id>",` key. Live after the fix:
`OK`, 17/17, 267 descriptors. A change back to the constructor shape would still read
`PARSE-CHANGED`, which is the honest result.

## Limits

- Two measured builds. The 20261108 numbers are counts on record, not an id list.
- Module placement comes from one-module bundles, which include some shared code. An id
  "in" a module means it was in that module's bundle.
- The literal window (400 characters) overlaps neighbouring descriptors, which inflates
  that score.
- Flags and payload shapes were not compared with what the RPCs do on the wire. Payload
  drift stays with e2e and live-verify.

## Reproduce

`uv run python scripts/dev/spike_rpc_churn.py` writes each build's JSON record and bundle
to `scripts/dev/_spike_out/rpc_churn/` in the main checkout (gitignored). It needs the
September captures in `scripts/dev/_spike_out/` for the old-build arm.
