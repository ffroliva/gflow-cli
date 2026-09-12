# The `/about` redirect is decided client-side, in ~200 ms, without asking Flow anything

- **Date:** 2026-09-11
- **Script:** [`scripts/dev/spike_about_redirect_cause.py`](../../../scripts/dev/spike_about_redirect_cause.py)
- **Arms:** `denon82` (redirects) vs `ci-probe` (opens) — an **A/B**, not an inspection
- **Cost:** $0 — navigations and response *metadata* only. No bodies, no headers, no generation.
- **Raw:** `scripts/dev/_spike_out/spike_about_redirect_cause_20260911_{154808,192135}.json` (gitignored)
- **Refs:** [#756](https://github.com/ffroliva/gflow-cli/issues/756)

## Why it was askable at all

#756 measured the redirect and explicitly declined to measure its cause. The
[2026-09-10 spike](2026-09-10-about-redirect-stability.md) could not investigate one
because the redirect had stopped reproducing. As of today it reproduces **stably** on
`denon82` ([5/5](2026-09-11-about-redirect-is-stable-for-an-account.md)), and a stable
reproducer is the condition under which a cause becomes measurable. That state is
perishable — `ci-probe` went from reproducing to not in two days with nobody watching —
so it was measured while it was there.

One clue was already in hand: `gflow project list` on `denon82` returns 50 projects
**including** the one that redirects. The backend grants access while the frontend
declines to open it.

## Pre-registered readings

| Outcome | Reading |
|---|---|
| document 3xx with `Location: /about` | server-side; the app never boots |
| document 200, then a client-side navigation | the app boots and decides; the differing rpcid is the lead |
| an rpcid in both, non-2xx only on `denon82` | that call is the decision point |
| the arms differ in no request at all | not on the wire; record as unmeasured |

## What was observed

| | `denon82` (fails) | `ci-probe` (control) |
|---|---|---|
| document response | **200**, no `Location` | **200**, no `Location` |
| document **resolved** URL | `/project/<id>` | `/project/<id>` |
| any 3xx response at all | **none** | one — an avatar on `lh3.google.com` |
| first navigation | 146 ms, `/project/<id>` | 90 ms, `/project/<id>` |
| second navigation | **338 ms, `/about`** | 700 ms, same `/project/<id>` |
| total responses | 7 | 43 |
| Flow requests: `batchexecute` | **0** | 20, first at **275 ms** |
| Flow requests: **anything else** | **1 — the document itself** | 15 |
| non-Flow (fonts, GTM, analytics, OneGoogle, Play) | 6 | 8 |
| non-2xx from any Flow endpoint | **none** | none |

Every response is classified, not just the `batchexecute` ones: "zero `batchexecute`
calls" is a statement about one route shape and would leave any other Flow request
unexamined. Classified in full, the failing arm's **only** request to `flow.google.com`
is the document. The single non-2xx anywhere is `play.google.com/log` 401 — telemetry, a
different origin, present regardless.

Two runs, ~3.5 hours apart, identical on every row above.

## Verdict

**Not a server-side redirect.** `page.goto` resolves to the *final* main resource after
following any server redirect, so a `200` with no `Location` is on its own consistent
with both a client hop and a 302 to `/about` — the status alone settles nothing. Two
things do: the document's **resolved URL is `/project/<id>`**, not `/about` (a server
redirect would have resolved to the destination), and there is **no 3xx response
anywhere** in the failing arm. So this is the app deciding, client-side, 192 ms after its
own first navigation commits.

**And it decides without asking Flow.** The failing arm's only request to
`flow.google.com` is the document itself — zero `batchexecute`, and zero of anything
else. Not "called and was refused", *never called*. There is no 401, no 403, no 404 to
point at, because there is no request. The sixteen rpcids the control makes (`Zzl0ze`,
`as29s`, `tRARke`, …) are simply absent, as are its other fifteen Flow calls.

The cross-arm timing is what makes that a finding rather than a race: the control had
already made its **first** rpc at 275 ms, before the failing arm hopped at 338 ms. Two
runs, so this is suggestive rather than airtight — but combined with zero calls in the
5.5 s *after* the hop, the app plainly is not waiting on a Flow answer to decide.

**It is account-wide, not project-specific.** A second `denon82` project
(`5200b87d-…`) redirects identically, 2/2.

So the signal the app acts on is already present at page load — embedded bootstrap data,
a cookie, or client-side storage — and not fetched.

## What this does NOT establish

**Which signal.** Naming it means reading the document's embedded bootstrap payload,
which on this origin is exactly where bearer tokens and account data live. Not read here.
#756's warning stands: **do not encode a remedy that asserts a mechanism this cannot
see.** A lead is not a cause.

**Whether it ever clears, or why it started.** Five attempts over three minutes plus two
more on another project is stability at that scale, not forever.

**Any wider population.** Two accounts have shown it — `denon82` now, `ci-probe` before
2026-09-10 — and an account is not a cohort.

**What would settle it:** a HAR capture of the failing document through
[`scripts/dev/har-spike/`](../../../scripts/dev/har-spike/README.md), diffed against a
working one, with `extract_har_summary.py` producing the redacted summary. That harness
exists for exactly this and is the tiebreaker rung. It was not run here because the
finding above already changes what the next person should look at, and because a raw
capture of this page must not leave the machine.

## What changes as a result

Nothing in the code. `retryable=False` was already correct and is now measured
([the stability spike](2026-09-11-about-redirect-is-stable-for-an-account.md)); the
message already names the redirect and stops. This narrows the search for whoever picks
up #756: it is not a permissions call being refused, so looking for one is a dead end.
