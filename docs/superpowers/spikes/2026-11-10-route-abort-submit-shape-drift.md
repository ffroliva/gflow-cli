# Spike: a route-aborted submit gives a stable body-shape fingerprint at $0 (#1228)

**Date:** 2026-11-10 · **Profile:** `denon82` (40 Veo credits) · **Cost:** $0. Credits read
40 before, between every path and after. No billed request got a response.
· **Script:** [`scripts/dev/spike_1228_route_abort_drift.py`](../../../scripts/dev/spike_1228_route_abort_drift.py)
· **Build label seen:** `boq_labs-ai-sandbox-frontend_20261110.00_p0` (every run)

## Question

The served-JS check ([#1217 spike](2026-11-10-rpc-ids-in-served-javascript.md)) proves an id
still exists. It cannot tell whether the request the page builds for that id still has the
same shape. Can an unattended run let the page build each billed submit, abort it before it
leaves the browser, and compare the body shape run to run, without billing anything?

## Pre-registered reading

Written in the script's docstring and committed (`9baf6ee7`) before the first live run:

- every run of a path captures its expected rpc, N>=3 fingerprints are identical and
  credits are unchanged: stable, the path can join the canary;
- a run that captures nothing is **unmeasured**, not a pass;
- fingerprints that differ across runs: not canary-ready, name the varying slot;
- a different rpc than expected: a state leak, reported as a finding;
- any fail-closed abort of an unresolved request: a finding;
- any leak or credit drop: stop at once.

## Method

Each run opens a fresh browser on the profile (the transport takes the profile lease) and
calls one production migrated-host driver directly on the page: `run_images`, `run_video`,
`run_extend`, `upscale_image_migrated`, `upscale_video_migrated`. It does not go through
`FlowApiClient`, so there are no retries and no catalog rows. The page mints its own
reCAPTCHA token; nothing is emulated and nothing is sent that the page did not send.

Billing guard:

1. A context route on every `batchexecute` aborts the request when any billed id
   (`YhhmEf eb1hJf MZZa6b nprQif ogiZ0b fZytfe SPrCad p0UkFb`) is in the URL `rpcids`, heads
   any `f.req` frame, or appears anywhere in the URL or decoded body. It also aborts,
   fail-closed, a POST whose id does not resolve or whose body does not decode (gzip is
   decompressed first).
2. `Route.continue_` is wrapped with the same predicate. A page route takes precedence over
   a context route, and `_guard_image_submit` (`migrated_composer.py:1402`) ends in
   `continue_()`, so a reference-carrying image submit would bypass (1). i2i was not run.
3. A tripwire: a response to any request carrying a billed id is a leak and stops the run.
4. `gflow credits user` read between paths with no browser open; any change stops it.

The predicate was self-tested offline before any browser opened: a URL-only id, a body-only
id (the Omni 1.2 `nprQif` case), an id in the second frame, an empty body and an undecodable
body. The fingerprint was tested the same way: changed values keep the hash, and an added
slot, a removed slot or a different model-key pattern change it.

**Fingerprint.** The inner `f.req` JSON reduced to classes: `N` null, `B` bool, `I` int,
`F` float, `U` UUID, `S` any other string, `E:<literal>` for an `UPPER_SNAKE` enum, and
`K:<key>` for a `lower_snake` model key with every digit run masked to `9`. Plus the query
parameter names, the form field names, the trailing frame slots and where the id rode (URL,
body or both). Never a value, an id, a token or a prompt. The hash is the first 16 hex of
SHA-256 over that structure.

A navigate-only control run (project page, 20 s, no click) aborted nothing: every request
the page sent on load resolved to a free id.

## Observed

| Path | rpc | Runs captured | Fingerprint | Seconds per run | Id carried in |
|---|---|---|---|---|---|
| t2i (`NARWHAL`, default aspect) | `ogiZ0b` | 3/3 | `d54b2262cb4ee746` ×3 | 13.8, 13.9, 13.1 | URL and body |
| t2v (`veo_3_1_lite`, portrait) | `YhhmEf` | 3/3 | `97b4e08cc0790271` ×3 | 14.2, 13.1, 13.1 | URL and body |
| i2v (`veo_3_1_lite`, portrait, local start frame) | `eb1hJf` | 3/3 | `2ed406289469bcf1` ×3 | 22.3, 21.0, 19.0 | URL and body |
| image upscale 2K | `SPrCad` | 3/3 | `402e517f0d9c5709` ×3 | 10.6, 10.0, 10.1 | URL and body |
| video extend | `fZytfe` | **0/7, unmeasured** | none | 35 to 45 | never sent |
| video upscale 1080p | `p0UkFb` | **0/4, unmeasured** | none | 5.7 | never sent |

Every captured request was a single frame with query keys
`_reqid bl f.sid hl rpcids rt source-path` and form fields `at f.req`. No run captured an
unexpected rpc, no request was aborted fail-closed, and no billed request got a response.
Credits: **40 before, 40 after**, unchanged at every read in between.

The four shapes are recorded in
[`FLOW_WIRE_RPCS.md`](../../FLOW_WIRE_RPCS.md#request-shape-fingerprints-1228).

**Extend, unmeasured.** On three clips (two `veo_3_1_lite`, one `omni_1_2_flash`) the clip
editor's extend menu item (`keyboard_double_arrow_right`) never became clickable, so the
page never built `fZytfe`. The first run on the first clip raised `TransportTimeoutError`
instead of the click timeout: that is the misleading `Zzl0ze` message #1244 describes. The
repo's own $0 e2e, `test_e2e_extend_submit_contract_at_zero_cost`, fails the same way on the
untouched `develop` tree on this profile (35.9 s, click timeout on the same locator), so the
miss is not the spike's. This is #1244: the item is disabled on this account, cause not
proven (the low balance is the open hypothesis). The account where #1244 saw extend pass is
off-limits for this run.

**Video upscale, unmeasured.** `upscale_video_migrated` raised `UpscaleUnavailableError`
("1080p video option is not available or disabled on this account") before any click, on
the same clip all four times. The page never built `p0UkFb`.

## Verdict against the pre-registered reading

- **t2i, t2v, i2v, image upscale: stable.** Each captured its expected rpc 3/3, the three
  fingerprints were identical, and credits did not move. They can join the canary.
- **Extend and video upscale: unmeasured, not passed.** Both are blocked on this profile
  by Flow disabling the menu item, which is a named external blocker (the account), not a
  result about the wire.
- `MZZa6b` (r2v) and `nprQif` (start+end) were not in this spike's set and were not run.
  i2i was deliberately not run, because its production page route ends in `continue_()`.

## What a nightly canary job would need

- **Run time.** About 15 s per run for t2i and t2v, 20 s for i2v, 10 s for image upscale,
  each with a fresh browser: about 1 minute for one run of the four. A `gflow credits` read
  between paths took about 5 s here (the direct read, no browser), so reading before,
  between and after keeps one nightly pass under 2 minutes.
- **reCAPTCHA.** The page mints its own token for each submit it builds. That is at least
  one mint per night for each of t2i and t2v, whose `Needs reCAPTCHA` is `yes` in the table.
  For i2v and image upscale the table says `unknown`; this spike did not count mints.
- **Profile state.** A profile served flow.google.com, signed in, with one pinned project,
  one finished image in a project for the upscale, and the profile lease free. Each i2v run
  uploads its start frame (`maseQ`, free), so the pinned project gains one image per night.
  This spike left three such uploads in the project it used. A path whose menu item Flow
  disables on the account (extend and video upscale here) reports unmeasured, never green.
- **Comparison.** Store the per-rpc hash with the build label. A changed hash with an
  unchanged `bl` is worth a look the same morning; a changed hash with a new `bl` is the
  expected moment for drift. The skeleton, not just the hash, has to be archived so the
  changed slot can be named.
- **Failure modes the job must classify.** A run that captures nothing (selector drift,
  disabled item, slow mint) is unmeasured, not red. A capture with a different rpc is a
  state leak. A fail-closed abort means the page sent something the predicate could not
  read. A leak or a credit change is a stop. A model key whose pattern changes changes the
  hash, so the job must pin the model it asks for: the sticky settings panel would
  otherwise make the key, and the hash, flap.
- **Billing safety is the job's, not the driver's.** The guard has to sit at the context
  route and at `Route.continue_`, matching URL and body. A URL-only predicate billed two
  clips on 2026-11-08 (memory: `omni12-submit-rpc-rides-the-body-only`).

## Not measured

- Stability across nights and across builds: all twelve captured runs were within one hour
  on one build.
- Whether a real shape change is caught live. The detector control is offline only.
- `fZytfe` and `p0UkFb` shapes (blocked), `MZZa6b`, `nprQif`, and i2i.
- Mint counts per path.
- `UPPER` strings without an underscore (the image model name `NARWHAL`) fold into `S`, so
  a change of image model name would not change the t2i hash. The key pattern is only
  fingerprinted for `lower_snake` keys.
