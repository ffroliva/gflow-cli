# `no maseQ reply` is two different bugs, and `maseQ` is not one of them

**Date:** 2026-09-08 · **Issues:** #719, #721, #756 · **Cost:** $0 (uploads only; no submit, no Veo credit reachable)
**Probe:** [`scripts/dev/spike_migrated_upload_wire.py`](../../../scripts/dev/spike_migrated_upload_wire.py)
**Captures:** `scripts/dev/_spike_out/spike_migrated_upload_wire_*.json` (gitignored — request bodies, signed URLs)

> **This document was rewritten after its own first conclusion turned out to be wrong.**
> The first version was titled *"the upload path works — #719 does not reproduce"*, on the
> strength of three successful uploads across two accounts. A fourth run failed, and the
> credit-less account failed twice out of twice. Three green runs did not establish a
> working path; they established an intermittent one. The original wording is preserved in
> the git history of this file rather than quietly replaced.

## Question

#719 reports that on `flow.google.com` every local-file upload fails with
`MediaUploadRejectedError` (exit 27) — *"no maseQ reply within 60s of choosing the file"* —
and asks for instrumentation before any fix, ranking a renamed rpcid as the likely cause.

## First: three of #719's four candidates were already ruled out, by the error class

`_upload_via_toolbar` raises a **distinct** `UiSelectorDriftError` when the toolbar `+` is
missing, when the menu renders no `upload` entry, and when that entry opens no file chooser.
Anyone holding `MediaUploadRejectedError` already knows the affordance was found, the menu
opened, the chooser fired and `set_files` was called. That part of the issue's framing was
wrong, and it still is.

## Results — 7 runs, 3 profiles

The probe drives **the real driver method**, not a re-implementation, with a listener on
every request.

| Profile | Credits | Runs | Outcome |
|---|---|---|---|
| `ffroliva` | funded | 4 | **3 uploaded**, 1 × `no maseQ reply` |
| `denon82` | funded | 1 | uploaded |
| `ci-probe` | **none (non-paying)** | 2 | **2 × `no maseQ reply`** |

### `maseQ` is NOT renamed — #719's top candidate is dead

Four successful uploads across two accounts, `maseQ` POST → 200 → a media id the driver
bound. The 2026-09-05 capture that established the name is still accurate. This mattered
most because it was the only candidate a constant change would have fixed.

### The failure has two distinct shapes, and they are not the same bug

**Shape A — credit-less account: the client never sends the upload at all.** 2/2 on
`ci-probe`. After `set_files`, no `maseQ` request under any name, to any host, by any
method — the only POSTs in the whole window are one `WuwhI` (2 027 B, *smaller* than the
4 321 B image, so not carrying it) and a `play.google.com` telemetry beacon. The driver then
waits 60 s for a request the page decided not to make.

**Shape B — funded account, intermittent: the request goes out and the reply is lost.**
1/4 on `ffroliva`. The `maseQ` POST carries **8 675 B** (the image), and then:

```
t=11.86  req  maseQ    POST  8675 B   flow.google.com     <- the upload goes out
t=14.50  req  WuwhI    POST  4649 B
t=14.64  req  cPZSdc / nzlxg / o30O0e / NfrxTb / Yizz8d / KV2T2d ...
t=15.17  req  HTrJv / LPzVkd / Zzl0ze / ngNC2 / tRARke / qJcgMc / mrlkwd / yBhWQ
                                                          <- the whole project-load
                                                             inventory, 2.6 s later
```

No `maseQ` response ever arrives — neither this probe's listener nor the driver's own saw
one. A successful run issues **4** POSTs in this window; this one issued **24**, and they
are the same set a fresh project load makes. That looks like the page re-initialising
under the in-flight upload, ~2.6 s after it was sent — which is just before the ~2.8 s the
successful runs took to answer.

### A tempting discriminator, ruled out

`ve2Lsc` returns `[null,["REGION"]]` and `WuwhI` returns `[]` — **on both accounts**,
including every successful upload. Neither is the difference between working and failing.
Worth stating explicitly because `["REGION"]` is a known eligibility blocker on this project
(#623) and is exactly the kind of thing that reads as a cause when it is background noise.

## What is NOT established

- **Why the credit-less client declines to send.** No credits, non-paying tier, and a
  storage entitlement all co-vary on this one account and cannot be separated with it.
  Uploads are otherwise free operations, so "no credits" is not an obvious mechanism.
- **That the t=14.5 burst is a reload.** It is the *shape* of one — the full project-load
  rpcid set, at once. No navigation event was captured, and nothing in
  `_upload_via_toolbar` navigates. Cause unknown.
- **The rate.** 1 failure in 4 on one funded account, on one afternoon. That is enough to
  say "intermittent" and not enough to say how often.
- **Whether A and B share a root cause.** They present identically to the user (the same
  exit 27, the same message) and are different on the wire.
- **Anything past the upload leg.** `_pick_frame_by_name` and the submit that follows were
  not exercised; #719 covers the whole chain.

## Consequences for the code

1. The message is wrong in the way that matters. *"the upload never reached Flow or was
   dropped"* is right for shape A and misleading for shape B, and the remediation hint —
   re-encode the image to strip metadata — is wrong for both. #719 already showed the file
   is irrelevant across three files; this shows it across seven runs of one file.
2. **The driver can tell A from B for free.** It already watches responses; watching whether
   the *request* was ever issued costs nothing and splits the two shapes at the point of
   failure, instead of leaving both as one 60 s timeout.
3. Shape B argues for a retry, shape A argues against one — which is precisely why they must
   be distinguished before either is implemented.
