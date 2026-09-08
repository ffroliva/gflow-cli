# Flow's queue is readable at $0, and it is on `Zzl0ze` — not `jwpduf`

**Date:** 2026-09-08 · **Issues:** #741, #719, #723 · **Cost:** $0 (navigation and passive listening; nothing typed, nothing clicked, nothing submitted)
**Probe:** [`scripts/dev/spike_migrated_queue_read.py`](../../../scripts/dev/spike_migrated_queue_read.py)
**Captures:** frame dumps written by `--dump` (gitignored; they carry signed CDN URLs)

## Question

#741 says a queued Flow generation is indistinguishable from a failed one, because
nothing in gflow reports server-side state once a run exits. #719 says every local-file
upload on the migrated host dies with `no maseQ reply within 60s`, and explicitly asks
for the one measurement that would settle it: **log every `batchexecute` rpcid seen in
the window, not just a match on `maseQ`.**

Both need the same instrument — something that watches the page's own traffic and reports
what actually arrived. Does one work, and what does the migrated host put on the wire when
nobody is generating?

## What was observed

Profile `ffroliva`, project `c5550ed7-…` (a real working project), two runs 10 minutes
apart at 20 s and 30 s. Identical results both times.

**69 records, and every one of them arrived on `Zzl0ze`.**

| kind | status | count |
|---|---|---|
| image | *(none — images carry no status)* | 34 |
| video | `done` (3) | 33 |
| video | `submitted` (6) | 2 |

Full rpcid inventory on an idle project load — 15 distinct ids, **none of them `jwpduf`
or `as29s`**:

```
NfrxTb ×2 · o30O0e · Yizz8d · nzlxg · cPZSdc · KV2T2d · LPzVkd · ngNC2
HTrJv · yBhWQ · mrlkwd · tRARke · Zzl0ze · qJcgMc · ve2Lsc
```

### Three findings

**1. `Zzl0ze` carries the listing; `jwpduf`/`as29s` are progress polls.** The driver
already knows the second pair, because it only ever watches a page that is mid-generation.
An idle project reads its entire history from `Zzl0ze` on load, and nothing polls
afterwards. Any `gflow video status` built for #741 should read `Zzl0ze`, not extend the
poll loop.

**2. Records come in two shapes, and conflating them invents failures.** Length 8 is a
video generation and carries the model arm at `[7][0]` (`abra_r2v_8s`, and the status at
`[5][8][0]`). Length 7 is an image: no such arm, no status, no model. Read with one
shape, all 34 images report `status: null`, which looks exactly like a failed generation.
The first run of this probe did precisely that. The probe now labels `kind`.

**3. Two `abra_r2v_8s` records sat at status 6 (`submitted`) ~18 hours after submission.**

```
2026-09-07 20:28  89ecd7e3-f1eb-4deb-968a-93626a5501c3  abra_r2v_8s  status 6
2026-09-07 20:39  6fb4d448-e3b3-4bc5-93b0-e5c59f8f71da  abra_r2v_8s  status 6
   (a later run on the same project, 2026-09-08 08:40, reached status 3)
```

This is #741's premise, in the project it was filed from: two generations in a
non-terminal server-side state that **no gflow command can see**, long after the CLI that
submitted them gave up. They are the right shape and the right model key to be the #723
entity-bound r2v casualties.

## What was NOT measured

- **What status 6 means after 18 hours.** "Queued and progressing", "stuck", and
  "rejected without a terminal state" are all consistent with one read. A `--watch` run
  across a submit would separate them; this probe only samples. Do not read these two rows
  as proof the jobs are alive, only as proof that gflow cannot see whichever they are.
- **Whether `Zzl0ze` is the listing on labs.google too.** Every account here is migrated.
- **Nothing about `maseQ` or the upload path.** No upload was performed. This spike
  establishes that the instrument #719 asked for works and what an idle baseline looks
  like; the upload run itself is the next step, and its value is entirely in the diff
  against the baseline above.
- **Whether the size at `[5][13]` is the asset or a thumbnail.** Two images reported an
  identical 97311 bytes. Plausible for same-dimension thumbnails, but unconfirmed, so do
  not build a size check on it.

## Why this matters beyond #741

`no maseQ reply within 60s` is a timeout, and **a timeout is evidence about the probe, not
the feature**. With the rpcid inventory in hand, the next occurrence separates four
possibilities that today all present identically: the attach control was never found; it
was clicked and no chooser opened; the file was set and no request went out; or the
request went out under a name the driver no longer recognises. Only the last of those is
fixed by changing a constant, and today there is no way to tell it from the other three.
