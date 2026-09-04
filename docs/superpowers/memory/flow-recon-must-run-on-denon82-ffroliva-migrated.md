---
name: flow-recon-must-run-on-denon82-ffroliva-migrated
description: "The ffroliva account is FULLY migrated to flow.google.com (7/7 loads, not a flap) — every UI-automation spike on it dies with FlowHostMigratedError. Run live DOM recon on denon82. Also: a capability claim without the frontend named is meaningless now."
---

**Measured 2026-09-03.** Running `scripts/dev/capture_video_model_capability_matrix.py`
on profile `ffroliva` failed with `FlowHostMigratedError` on **7 out of 7**
consecutive attempts (1 initial + 6 retries, fresh browser launch each time).
The same spike on profile `denon82` succeeded on **attempt 1**.

**Why:** the error text in `api/transports/_common.py::raise_if_migrated` says
*"the migration flaps per page load, so retrying often lands the old frontend."*
For `ffroliva` that is **no longer true** — it is not flapping, it is migrated.
Retrying is wasted wall-clock there.

**How to apply:**
- Any live DOM / UI-automation recon or live-verification → **use `denon82`**
  (pt-BR; expect localized text, ligature-keyed selectors still work — see
  [[flow-locale-leak-icon-ligatures]]). Do not burn attempts on `ffroliva`.
- `ffroliva` is still fine for REST-path work (`gflow project list` etc. returned
  50 projects normally) — the migration kills the *frontend gflow drives*, not the
  aisandbox REST surface. See [[rest-path-capability-matrix]].
- If a spike must run on `ffroliva`, expect exit 36 immediately and treat it as
  environmental, not selector drift.

**The bigger consequence — name the frontend, or the claim is meaningless.**
There are now two Flow frontends with different capability matrices: the
`labs.google` one gflow drives, and the migrated `flow.google.com` one it cannot.
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
