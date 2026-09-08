# The migrated upload path works, and `maseQ` still holds — #719 does not reproduce

**Date:** 2026-09-08 · **Issues:** #719, #721 · **Cost:** $0 (uploads only; no submit, no Veo credit reachable)
**Probe:** [`scripts/dev/spike_migrated_upload_wire.py`](../../../scripts/dev/spike_migrated_upload_wire.py)
**Captures:** `scripts/dev/_spike_out/spike_migrated_upload_wire_*.json` (gitignored — they carry request bodies)

## Question

#719 reports that on `flow.google.com` **every** local-file upload fails with
`MediaUploadRejectedError` (exit 27) — *"no maseQ reply within 60s of choosing the file"* —
blocking both `i2v --initial-frame` and `r2v --ref`, and it asks for instrumentation before
any fix. It lists four candidates and says the logs cannot separate them.

## First: three of the four candidates were already ruled out, by the error class

Before spending a run, the shipped code answers part of it. `_upload_via_toolbar` raises a
**distinct** `UiSelectorDriftError` when the toolbar `+` is missing, when the menu renders
no `upload` entry, and when that entry opens no file chooser. A reporter holding
`MediaUploadRejectedError` therefore already knows the affordance was found, the menu
opened, the chooser fired, and `set_files` was called.

So #719's list of four collapses to: the rpcid was renamed · it went out and Flow did not
answer · nothing went out at all. Only the first is fixed by changing a constant.

## What was measured

The probe drives **the real driver method** — not a re-implementation — with a listener on
every request, so what it sees is what the shipped code sees.

| Profile | Host | Image | Result |
|---|---|---|---|
| `ffroliva` (funded, migrated) | flow.google.com | 1280×720 PNG, 4 321 B | ✅ **uploaded**, `media_id 8b3ff931-…` |
| `denon82` (migrated) | flow.google.com | 1280×720 PNG, 4 321 B | ✅ **uploaded**, `media_id d1c80c09-…` |
| `ffroliva` | flow.google.com | 1×1 PNG, 75 B | ⚠️ `maseQ` **HTTP 200**, no media id |
| `ci-probe` (**the reporting profile**) | flow.google.com | — | ⛔ never reached the editor — redirected to `/about` |

The successful wire, for the record:

```
t= 9.75  req  maseQ   POST  3003 B  flow.google.com
t=12.52  res  maseQ   200
```

### 1. `maseQ` still holds — the renamed-rpcid theory is dead

Seen on both accounts, answering 200, returning a media id the driver bound. The 2026-09-05
capture that established the name is still accurate. **This was the candidate most worth
ruling out**, because it is the only one a constant change would have fixed, and #719
ranked it first ("the most likely candidates, in order, are (4-with-a-renamed-rpcid)").

### 2. The upload path is not broken on the migrated host

Toolbar `+` → `upload` menu entry → file chooser → `maseQ` → media id, end to end, on two
independent migrated accounts, with a file of the same shape #719 reports failing (its
`tiny.png` was a 4.3 KB 1280×720 flat colour; this was a 4.3 KB 1280×720 flat colour).

### 3. A degenerate image produces a real but badly-named failure

A 1×1, 75-byte PNG gets **HTTP 200 with no UUID anywhere in the reply**, and the driver
reports *"maseQ answered 200 without a media id"*. That is literally true and reads like a
protocol break, when what happened is that Flow declined the image. Same family as #721:
a refusal reported as a malfunction.

### 4. The reporting profile cannot reach the editor at all

`ci-probe` navigates to `flow.google.com/project/<id>` and lands on **`/about`**, while
`gflow auth status --profile ci-probe` verifies the Flow session
(`compiledgrownth.official@gmail.com`) and `project list` works. The driver reports this as
`UiSelectorDriftError` — *"the settings trigger did not become visible within 30s on
https://flow.google.com/about"*. The URL is in the message, which is the only reason this
was diagnosable at all.

**Followed up the same day, and the landings are distinguishable.** A second $0 navigation
probe across both profiles:

| Navigation | Lands on |
|---|---|
| `ci-probe` → `labs.google/fx/tools/flow` | `accounts.google.com/…/signin/accountchooser` |
| `ci-probe` → its **own** project on flow.google.com | `flow.google.com/about` |
| `ffroliva` → its **own** project | `flow.google.com/project/<id>` |
| `ffroliva` → **`ci-probe`'s** project | `flow.google.com/404?reason=project` |

So `/about` is **not** "you cannot open this project" — that is `404?reason=project`, a
different URL. `/about` is where a **signed-out** browser lands, which the labs-root
redirect to the OAuth account chooser confirms independently.

That makes the split concrete: `ci-probe`'s **browser session is dead** while its NextAuth
token is still good — which is exactly why `auth status` reports verified and `project list`
returns three projects. The REST lane and the browser lane authenticate differently
(`flow-session-vs-google-sso-cookie`), and today only the browser lane's failure is
invisible until a run dies 30 s later calling it selector drift.

## What this does NOT establish

- **It does not prove #719 was wrong when filed.** It proves the path works *today, on two
  accounts that are not the reporting one*. The reporting profile's state has changed since
  — it cannot reach the editor at all now — so the original conditions are not reproducible
  here, and "works for me" is not a refutation.
- **#721's open question is still open.** Whether a credit shortfall *causes* upload failure
  or merely travelled with it cannot be answered while the only drained account available
  cannot load the editor. Both funded accounts uploading is consistent with the credit
  theory AND with it being irrelevant.
- **Why `ci-probe`'s browser session died**, or when. That it *is* signed out is now
  measured; whether it expired, was evicted, or never survived the last login is not.
- **Whether `ci-probe` is migrated or labs.** `LIVE_VERIFICATION_v0.70.0` says migrated and
  `v0.71.0` says labs, one day apart, and a signed-out profile redirects to the account
  chooser either way — so this probe cannot settle it. It needs re-testing after a login.
- **No `i2v`/`r2v` generation was run.** Only the upload leg. `_pick_frame_by_name` and the
  submit that follows are untested here, and #719 covers the whole chain.

## Follow-ups this suggests

1. Name Flow's refusal of an image, instead of `200 without a media id`.
2. Read the post-navigation URL and name what it says — `/about` is signed out,
   `404?reason=project` is no access, anything else is the real thing — instead of
   reporting selector drift. Third instance this week of a known state reported as
   unexplained drift (cf. #721 credits, #749 agent mode).
3. `gflow auth status` reports **verified** for a profile whose browser is signed out. It
   checks the lane that still works, and the lane generation actually needs is the one that
   is broken.
