# Is `flow.google.com/about` transient? — unanswered, and that is the finding

**Date:** 2026-09-10 · **Profile:** `ci-probe` · **Cost:** $0 (no generation)
**Script:** [`scripts/dev/spike_about_redirect_stability.py`](../../../scripts/dev/spike_about_redirect_stability.py)
**Raw:** `scripts/dev/_spike_out/about_redirect_stability_20260910_102147.json` (gitignored)
**Refs:** [#756](https://github.com/ffroliva/gflow-cli/issues/756)

## Why it was asked

Routing the `/about` landing to `FlowAppError` (exit 31) hands it that class's retry
semantics, because `is_retryable` was class-level. So the code would assert, on every
occurrence, that a retry is worth making — an assertion nobody had measured. #756
measured the *redirect* and explicitly declined to measure its *cause*; "does it
repeat?" is a narrower question and looked answerable, because `ci-probe` is the
profile that produced the original report and it is on this machine.

## The reading was fixed before the run

Written into the script's docstring **before** it was executed, so the outcome could
not be reinterpreted to suit the change it was gating:

| Outcome | Reading |
|---|---|
| N/N `/about` | stable for this account — a retry is doomed; must not be retryable |
| mixed | it flaps — a retry can win; retryable is defensible |
| 0/N `/about` | does not reproduce; settles **nothing** — absence of a reproduction is not evidence of transience |

## What was observed

Five sequential `MigratedComposer.ensure_editor` calls against project
`1e4efe0d-…` on `ci-probe`:

| # | Result | Landed | Elapsed |
|---|---|---|---|
| 1 | `editor_ready` | `/project/1e4efe0d-…` | 1.86 s |
| 2–5 | `editor_ready` | `/project/1e4efe0d-…` | 0.02–0.03 s |

`/about` landings: **0 of 5.** The session authenticated normally
(`flow_session_cookie_present=True`, `expired=False`, `google_sapisid_present=True`,
51 context cookies).

## Verdict

**The redirect no longer reproduces on `ci-probe`, and retryability is therefore
UNMEASURED.** It disappeared sometime between 2026-09-08 and 2026-09-10.

That is consistent with "it was transient" *and* with "an account or session state
changed underneath it" — a re-auth, a project-access grant, a cohort move. Nothing
here distinguishes those, so nothing here licenses a retry claim in either direction.

## What was done with it

`GFlowError` gained a per-instance `retryable` override (the same class-default /
instance-override shape `remediation_hint` already had), and the `/about` raise site
passes `retryable=False`. **That is not a finding that retrying fails.** This shape
raised exit 23 before, which was already non-retryable, so `False` preserves the
existing answer instead of inventing a new one under cover of an exit-code change.

## Not measured

- **Why the redirect happened at all**, in either direction. Out of scope by design;
  #756 warns against a fix that asserts an unmeasured cause.
- ~~**Whether a *second* attempt wins during a live occurrence.**~~ **ANSWERED
  2026-09-11 — see [`2026-09-11-about-redirect-is-stable-for-an-account.md`](2026-09-11-about-redirect-is-stable-for-an-account.md).**
  A live occurrence was caught on `denon82` while measuring something else, this script
  was re-run unmodified at `--attempts 5`, and it came back **5/5 `/about`** over ~3
  minutes on the account's own project with a healthy session. Per the reading
  pre-registered above, that is *stable*: a retry is doomed, and `retryable=False` is now
  the measured answer rather than the preserved one.
- **Any other account.** One profile is one account; per
  `flow-capabilities-are-cohort-dependent`, an account is not a cohort.
