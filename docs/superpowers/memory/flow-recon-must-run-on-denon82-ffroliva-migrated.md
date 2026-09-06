---
name: flow-recon-must-run-on-denon82-ffroliva-migrated
description: "Both maintainer accounts are FULLY migrated to flow.google.com (one-way, not a flap) — there is no labs.google account left, so labs-side behaviour is cohort-external. Recon and live-verification run on either account through the migrated composer, which drives t2v and i2v-from-a-local-frame; unported forms still exit 36. Also: a capability claim without the frontend AND profile named is meaningless."
---

**Measured 2026-09-03.** Running `scripts/dev/capture_video_model_capability_matrix.py`
on profile `ffroliva` failed with `FlowHostMigratedError` on **7 out of 7**
consecutive attempts (1 initial + 6 retries, fresh browser launch each time).
The same spike on profile `denon82` succeeded on **attempt 1**.

**Why:** the error text in `api/transports/_common.py::raise_if_migrated` says
*"the migration flaps per page load, so retrying often lands the old frontend."*
For `ffroliva` that is **no longer true** — it is not flapping, it is migrated.
Retrying is wasted wall-clock there.

**Mechanism, settled 2026-09-04 ($0, two spikes, both on `ffroliva`).** It is not
DNS, not a server 302, not a missing session cookie, and it does not flap. `labs.google`
returns a normal **200** to a **fully authenticated** session (`has_labs_next_auth=True`,
`/fx/api/auth/session` → 200), and then the labs.google app itself runs
`window.location.replace('https://flow.google.com' + path + search)` from a `useEffect`
gated on **a server-assigned per-account boolean on the app's runtime config**. Measured
5/5 on the bootstrap URL; the 2026-09-03 "7/7" was the same one-way state. The
2026-09-03 flip captures (60/60 `labs.google` on this profile) were taken **before** the
account was flagged that evening — the "flaps per page load" text was an observation
straddling a one-time rollout, and is withdrawn. Consequences shipped: exit 36 is
**not retryable**; detection is event-driven off `framenavigated` (no wait, no URL
re-read race). Full write-up: `docs/superpowers/spikes/2026-09-04-migrated-host-handoff-mechanism.md`.
Two things NOT to re-derive: the `has_labs_next_auth: false` in the headless-httpx memory was a
property of that experiment's cookie filter, not of the account; and `pinhole` is Flow's i18n
namespace (`pinhole_about_flow` → "About Flow"), not a migration codename.

**How to apply** (rewritten 2026-09-05 after #679 — the original bullets predate gflow
having a migrated driver at all, and read as "the new host cannot be driven"):
- Live DOM recon and live-verification run on **either** account, through the migrated
  composer — that is *browser-driven* work; the REST bullet below is where a moved
  account still fails. Prefer `denon82` (pt-BR) when the question is locale invariance — localized
  text with ligature-keyed selectors is the point of that account (see
  [[flow-locale-leak-icon-ligatures]]); prefer `ffroliva` (en-GB) for a first capture.
  Verify a generation path on **both** before calling it done.
- A **video** spike on either account exits 36 only for a form the migrated composer has
  not ported (today: an end frame, a frame by media UUID or `@Name`, r2v). For a ported
  form, exit 36 is a real regression to investigate, not the environment. Every other
  surface — image, characters, scenes, extend, instructions, tools — has no migrated
  driver at all; how each one *fails* there is the next bullet, not this one.
- Both accounts are fine for **mint-free** REST-path work (`gflow project list`,
  `gflow data …`) — the migration changed the *frontend*, not the aisandbox REST
  surface. See [[rest-path-capability-matrix]]. A REST path that **mints a reCAPTCHA
  token first** (image t2i/i2i, upscale, extend) is *not* fine on a moved account: the
  mint runs on the pool's bootstrap page, which is the `flow.google.com` grid there, so
  it fails before any transport guard can classify it — surfacing as a bare exit-1
  `RecaptchaError` through v0.68.0 (#673). Measured by the session that fixed it; PR
  #678 turned that into the exit 36 it should always have been — the guard now runs at
  the mint too, so the failure is classified before `discover_site_key` is reached.
- **Labs-side** behaviour is what is now unreachable: no maintainer account is left on
  `labs.google`, so a labs-only claim is cohort-external — verify via a contributor or
  record it NOT verified.

**The bigger consequence — name the frontend, or the claim is meaningless.**
There are two Flow frontends with different capability matrices, and gflow now drives
**both**: `labs.google` through `ui_automation`, and `flow.google.com` through the
migrated composer (text-to-video since #664, image-to-video from a local start frame
since #679; the rest of the matrix still exits 36 there).
"Flow's UI shows X" no longer identifies a fact. This is exactly how external
PR #650 was first misread. It asserted Veo 3.1 gained 4s/6s/8s duration tabs and
relaxed `supports_duration()` to a constant `True`, and the migrated frontend looked
like the obvious explanation. **It was not** — on 2026-09-04 the reporter produced a
credit-free capture on `labs.google`, the same frontend gflow drives, showing the
tabs on a third profile. Different **cohort**, not a different frontend. Naming the
host is necessary but NOT sufficient; name the profile too
([[video-model-capability-matrix]], [[flow-capabilities-are-cohort-dependent]]).

**Reviewer heuristic that follows:** when a PR relaxes a Flow capability gate, the
first question is *"which host were you on?"*, before any code discussion. See
[[unreproducible-bug-hand-to-reporter]] and [[pr-must-verify-on-affected-surface]].

**2026-09-05 08:14Z — denon82 is moved too (3/3 loads land on flow.google.com, still authenticated). Both maintainer accounts are on the new host; there is NO labs.google account left for labs-side recon or verification. Labs-only behaviour (the labs duration guard, labs selectors) is now cohort-external — verify via a contributor or record NOT verified. The migrated composer (#664) is the driven path for both accounts.

**2026-09-05 20:43Z — this file's advice was over-broad and is corrected above.** It told
agents to expect exit 36 "immediately" on `ffroliva` and described the migrated frontend as
one gflow "cannot" drive. Both were true when written (2026-09-03/04, before #664) and false
after it: v0.67.0 drove t2v there, and #679 drove i2v from a local start frame — live-verified
on **both** accounts, `docs/LIVE_VERIFICATION_v0.69.0.md`. What survives unchanged is the
measurement this file exists for: the handoff is one-way per account, and naming the host
without naming the profile still does not identify a fact.
