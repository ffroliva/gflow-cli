# Live verification — v0.71.1

> What was exercised against **real** Flow for this release, and — just as importantly —
> what was **not**.

**Date:** 2026-09-08 · **Profiles:** `ffroliva` (migrated `flow.google.com`, funded),
`denon82` (migrated), `ci-probe` (migrated, non-paying, no credit) · **Cost: $0** — every run
below stops before a submit; no Veo credit is reachable from any of them.

> **Host note.** All three profiles are on the migrated host. `LIVE_VERIFICATION_v0.71.0`
> said `ci-probe` was on **labs**; that was wrong and is corrected in this release. Measured:
> `labs.google/fx/tools/flow` on that profile redirects to `flow.google.com/` — the one-way
> migration redirect.

---

## 1. Agent-mode recovery names which of three things happened — VERIFIED ✅

`pytest -m e2e -k agent_mode tests/e2e/test_migrated_host_e2e.py`, profile `ffroliva`,
project `300f5260-…`. `e2e_auth`, $0. **Run twice** — once before the review fixes and again
after, because the first run no longer covered the changed code.

The test carries its own A/B control: it drives the account **into** agent mode through the
chip a user would click, then neuters `AGENT_MODE_CHIP` so the recovery cannot fire.

```
15:20:56  migrated.navigate          flow.google.com/project/300f5260-…
15:20:58  migrated.editor_ready                       <- healthy start
             (test clicks the chip INTO agent mode; READY_ANCHOR goes hidden)
             control arm  -> UiSelectorDriftError "did not become visible"
15:21:53  migrated.agent_mode_exited  issue_ref=#749  <- fix arm
15:21:53  migrated.editor_ready                       <- classic composer back
1 passed in 104.93s
```

Five layers:

1. **Control discriminates.** The neutered arm raised `did not become visible` — the
   *ordinary drift* branch — not an agent-mode message. Pinned with `match=` this release, so
   it can no longer pass on any drift, including one raised by the recovery firing wrongly.
2. **Recovery fired.** `migrated.agent_mode_exited` present, once.
3. **State restored.** `READY_ANCHOR` visible again, asserted after the fix arm.
4. **No false positive on the happy path.** Neither `editor_ready` logged the new
   `migrated.agent_mode_chip_pressed_while_ready` warning — the expected negative,
   confirming the live account does hide the trigger in agent mode as measured.
5. **Account left clean.** The test now restores agent mode in a `finally`; the chip was
   `aria-pressed='false'` afterwards, so no later run inherits the state.

## 2. The one-time upload-terms dialog — VERIFIED ✅, and unrepeatable hereafter

Probe: `scripts/dev/spike_migrated_upload_wire.py`, which drives the **real**
`_upload_via_toolbar` rather than a re-implementation, with a listener on every request.
Finding: [`no maseQ reply` is two different bugs](superpowers/spikes/2026-09-08-migrated-upload-fails-two-ways.md).

**The order was forced.** The dialog is one-off per account, so accepting it destroys the
failing state permanently. The guard was written and verified against the live dialog
**before** anything was clicked; the account owner then authorised the click explicitly.

| # | Run on `ci-probe` | Result |
|---|---|---|
| 1–3 | pre-fix upload | `no maseQ reply within 60s` — the misleading message |
| 4 | upload **with the guard** | a dialog opened during the upload window was named instead of the file being blamed |
| 5 | the spike's `--accept-terms` (off by default) → click → retry, same session | **`media_id 8914400f-2c3c-41da-84a9-2e26a356d24e`** |
| 6 | plain upload, fresh session | **uploaded, `dialog: None`, 4 POSTs** |

Five layers:

1. **The dialog is the blocker.** Run 4 named it while it was still on screen, from a live
   DOM read: *"Rights to use this image — Make sure you have the necessary rights to any
   content or files that you upload…"*.

   > **Wording note.** Run 4 predates the review pass and emitted *"…no maseQ request was
   > ever sent — Flow is holding the upload behind its one-time upload-terms confirmation"*.
   > The shipped build hedges that claim, because a dialog count is evidence for a guess and
   > not a fact, and it now observes the request rather than inferring it:
   > *"a dialog opened after the file was chosen and no maseQ request left the page — most
   > likely Flow's one-time upload-terms confirmation (host=migrated)"*. The behaviour under
   > test is identical; only the sentence changed.
2. **Accepting unlocks the upload.** Run 5 returned a real media id, immediately, in the same
   session, on the account that had failed 3/3.
3. **It never returns.** Run 6, fresh browser session, `dialog: None` and a healthy 4-POST
   window — versus the 2-POST window of a blocked run.
4. **The wire agrees.** `maseQ` POST observed leaving the page on every successful run and on
   **none** of runs 1–4; `saw_expected_rpc` false throughout the blocked state.
5. **Cross-account control.** `ffroliva` and `denon82`, which had uploaded before, never saw
   the dialog and uploaded the same 4 321-byte 1280×720 PNG successfully — so the variable is
   consent, not the file, not credits, and not the rpcid.

> **Not repeatable from here.** All three available accounts have now accepted the dialog.
> Re-verifying the guard's *firing* branch needs a Google account that has never uploaded on
> `flow.google.com`. That is a named external blocker, not a skipped step; the guard's
> non-firing branch — where the regression risk actually lives — is covered offline by a
> control test and by the existing `e2e_video` i2v/r2v paths.

## 3. `maseQ` is not renamed — VERIFIED ✅

Six successful uploads across three profiles, each a `maseQ` POST → HTTP 200 → a media id the
driver bound (`8b3ff931`, `d1c80c09`, `423176b3`, `8914400f`, `8787e007`, …). The 2026-09-05
capture that established the rpcid still holds. This mattered because a renamed rpcid was
#719's top-ranked hypothesis and the only one a constant change would have fixed.

## 4. Flow's queue is readable at $0 — VERIFIED ✅ (instrument only)

`scripts/dev/spike_migrated_queue_read.py`, `ffroliva`, project `c5550ed7-…`, two runs ten
minutes apart with identical results, and a third ~20 h later.

- **69 records, all on `Zzl0ze`.** An idle project load shows 15 distinct rpcids and
  `jwpduf`/`as29s` are among **none** — those are progress polls, seen only because the driver
  only ever watches a page mid-generation.
- **Two record shapes.** Length 8 is a video generation with the model arm at `[7][0]`;
  length 7 is an image with no status and no model. Read with one shape, all 34 images report
  `status: null`, indistinguishable from a failed generation.
- **Two `abra_r2v_8s` records sat at status 6 (`submitted`)** with no media URL, and were
  **still** `submitted` when re-read ~20 h later, while a later run on the same project
  reached status 3.

Finding: [the migrated queue is readable on `Zzl0ze`](superpowers/spikes/2026-09-08-migrated-queue-is-readable-on-zzl0ze.md).

This ships as an instrument only; no user-facing command reads the queue yet (#741).

---

## Recorded as NOT verified

- **#719's second failure shape.** On a funded, already-consented account, ~1 upload in 4
  sends the `maseQ` request (8 675 B) and never receives a reply, with the entire project-load
  rpcid inventory firing 2.6 s later (24 POSTs versus 4 on a healthy run). Unfixed, unexplained,
  and #719 stays open for it. This release only makes it *distinguishable*: the driver now
  watches the request, so this reports *"the request left the page and Flow did not answer in
  time"* rather than sharing a message with the consent case.
- **Whether that 2.6 s burst is a page reload** ([#719](https://github.com/ffroliva/gflow-cli/issues/719)).
  It has the shape of one — the full project-load rpcid set, at once — but no navigation event
  was captured and nothing in `_upload_via_toolbar` navigates. **Blocker:** the shape occurs on
  ~1 run in 4, so catching it with navigation instrumentation attached needs repeated paid-path
  runs on a consented account; the instrument to do it is in the repo
  (`scripts/dev/spike_migrated_upload_wire.py`).
- **The labs.google behaviour of either fix** ([#639](https://github.com/ffroliva/gflow-cli/issues/639)).
  Neither the agent-mode chip nor the upload-terms dialog was observed on labs.
  **Blocker:** Google has migrated every account available here — `ffroliva`, `denon82` and
  `ci-probe` all redirect from `labs.google/fx/tools/flow` to `flow.google.com/` — so there is
  no labs cohort to test against. Both fixes are scoped to `migrated_composer` and cannot
  affect the labs driver.
- **`gflow image` / scenes / extend / instructions on the migrated host**
  ([#639](https://github.com/ffroliva/gflow-cli/issues/639)). Untouched by this release and
  still exit 36. **Blocker:** not implemented rather than unverifiable — PR #750 addresses the
  image half and was excluded from this release because it is branched pre-#751 and conflicts
  across six files.
