# Does a retry win during a LIVE `/about` occurrence? — 5/5 no. Stable.

- **Date:** 2026-09-11
- **Script:** [`scripts/dev/spike_about_redirect_stability.py`](../../../scripts/dev/spike_about_redirect_stability.py) (unchanged — `--profile denon82`)
- **Profile / project:** `denon82` · `4ccb3222-…` — **the account's own project**
- **Cost:** $0 — five navigations, no generation
- **Raw:** `scripts/dev/_spike_out/about_redirect_stability_20260911_141153.json` (gitignored)
- **Refs:** [#756](https://github.com/ffroliva/gflow-cli/issues/756)

## The question the previous run left open

[`2026-09-10-about-redirect-stability.md`](2026-09-10-about-redirect-stability.md) got
**0 of 5** — the redirect had stopped reproducing on `ci-probe` — and recorded that as
*unmeasured* rather than as transience. It then named, precisely, what would settle it:

> **Whether a *second* attempt wins during a live occurrence.** This is the question that
> actually settles the flag, and it needs someone to catch the redirect while it is
> happening. Re-run this script with `--attempts 5` at that moment and the answer falls
> out.

While measuring something else entirely (the #780 consent bar), `denon82` turned out to be
redirecting. That is the moment. The script was run **unmodified**, so the reading it
pre-registered still governs.

## Pre-registered readings — inherited, not rewritten

| Outcome | Reading |
|---|---|
| N/N `/about` | stable for this account — a retry is doomed; must not be retryable |
| mixed | it flaps — a retry can win; retryable is defensible |
| 0/N `/about` | does not reproduce; settles **nothing** |

## What was observed

| # | Result | Landed | Elapsed |
|---|---|---|---|
| 1 | raised, `is_about=True` | `/about` | 31.2 s |
| 2 | raised, `is_about=True` | `/about` | 35.1 s |
| 3 | raised, `is_about=True` | `/about` | 35.3 s |
| 4 | raised, `is_about=True` | `/about` | 34.9 s |
| 5 | raised, `is_about=True` | `/about` | 35.0 s |

**5 of 5**, over roughly three minutes of consecutive attempts.

The session was healthy throughout — `flow_session_cookie_present=True`,
`flow_session_cookie_expired=False`, `google_sapisid_present=True`, 68 context cookies —
and the project is **the account's own**, listed by `gflow project list` on that same
profile. So this is neither an expired session nor a project the account cannot see.

## Verdict: STABLE, and the retry flag is now measured

`retryable=False` at `_common.py::raise_if_known_landing` was previously a **preserved
default** — the code comment and `FlowAppError`'s docstring both said so in as many words,
because the 2026-09-10 run could not measure it. It is now the measured answer: a retry is
doomed for an account in this state, and five of them cost a user **~3 minutes** to learn
nothing.

Both comments are corrected in the same change as this document. They were accurate when
written and became false the moment this ran, which is exactly the kind of claim this
repo's Iron Law is about.

## What is still NOT measured

**Why the redirect happens**, in either direction. Unchanged and deliberately so — #756
warns against a fix that asserts an unmeasured cause, and nothing here names one. What is
now *excluded* is narrower but real: not an expired session, not a missing project, and
not transience.

**Whether it ever clears for this account**, and on what timescale. Five attempts over
three minutes is stability at that scale, not forever. `ci-probe` went from reproducing
(2026-09-08) to not (2026-09-10) without anyone watching the transition.

**Any wider population.** Two accounts have now shown it — `denon82` today, `ci-probe`
before 2026-09-10 — and one account is still not a cohort.

## A consequence worth naming

This is what blocked the #780 consent-bar spike from measuring the covering geometry on a
**second** account's editor: `denon82` and `pr389fresh2` both land here, so neither can
mount a composer at all. That gap is recorded in
[`2026-09-11-migrated-cookie-bar-blocks-the-composer.md`](2026-09-11-migrated-cookie-bar-blocks-the-composer.md)
and now has a named cause rather than an unexplained one.
