# `networkidle` on Flow's bootstrap costs ~3× — it does not hang, and it must stay

**Date:** 2026-09-16 · **Issue:** [#836](https://github.com/ffroliva/gflow-cli/issues/836)
· **Probe:** `scripts/dev/spike_networkidle_bootstrap_cost.py` · **Cost:** $0 (navigation only)
· **Profile:** `denon82` (served `flow.google.com`)

## Question

`ui_automation.py:1118` navigates with `wait_until="networkidle"`, 474 lines above a
comment in the same file forbidding exactly that. #836 proposed a mechanism:

> If Flow holds a long-poll or a websocket open, `networkidle` never fires and
> `setup_own_context` burns the **full 45 s on every bootstrap**.

The issue was explicit that this was unmeasured — *"No evidence yet that it is currently
firing the timeout on any account… Measuring it is part of the fix."* This measures it.

## Rung 1 first — and it already weakened the mechanism

Per the spike ladder, existing captures before a new probe. Two spikes in this repo
already bear on it:

- [`2026-09-14-two-domain-protocol-survey.md`](2026-09-14-two-domain-protocol-survey.md)
  — **0 WebSocket events in 8/8 observations where the app actually loaded.**
- [`2026-09-14-video-poll-is-a-fixed-client-timer.md`](2026-09-14-video-poll-is-a-fixed-client-timer.md)
  § 4 — nothing held open on the video path.

The one long-held response in the corpus, `ogiZ0b`, spans an **image generation** — not
the bootstrap navigation. So the proposed mechanism had no support at this call site
before a single new byte was measured. That is the difference between "it hangs" and
"nobody checked".

## Method

A/B, each arm in its own freshly launched persistent context so neither warms the other,
mirroring `setup_own_context`'s launch arguments (same channel, viewport, locale, args,
`navigator.webdriver` init script).

- **A** — `goto(wait_until="domcontentloaded")`, then wait for a structural app anchor.
  This is the pattern `:1592` prescribes and `:1596` already uses.
- **B** — `goto(wait_until="networkidle", timeout=45_000)`, the production call verbatim.

Arm order alternates each round, so a warm HTTP cache cannot systematically favour one.
Two sessions, 5 navigations per arm, 10 total.

## Observed

| arm | run | total | goto | timed out | requests |
|---|---|---:|---:|---|---:|
| A domcontentloaded | 1 | 1229 ms | 643 ms | no | 87 |
| B networkidle | 1 | **6597 ms** | 6597 ms | no | 150 |
| B networkidle | 1 | 2602 ms | 2602 ms | no | 109 |
| A domcontentloaded | 1 | 1217 ms | 324 ms | no | 109 |
| A domcontentloaded | 2 | 881 ms | 244 ms | no | 130 |
| B networkidle | 2 | 2554 ms | 2554 ms | no | 126 |
| B networkidle | 2 | 2669 ms | 2669 ms | no | 143 |
| A domcontentloaded | 2 | 913 ms | 269 ms | no | 115 |
| A domcontentloaded | 2 | 871 ms | 247 ms | no | 148 |
| B networkidle | 2 | 2688 ms | 2688 ms | no | 134 |

**A mean 1022 ms · B mean 3422 ms · B/A ≈ 3.3× · ceiling hits 0/5.**

## Verdict

**The stated mechanism is refuted. The defect is real anyway.**

1. **`networkidle` fires, every time. 0/5 reached the 45 s ceiling.** There is no hang on
   this account, and the "45 s on every bootstrap" claim in #836 is not what happens.
   The `try/except` that only logs is therefore not hiding a timeout today.
2. **It still costs ~3.3× more than the prescribed pattern** — 3422 ms vs 1022 ms mean,
   on every single bootstrap of every command.
3. **It is far more variable.** A spans 871–1229 ms (a 358 ms band). B spans 2554–6597 ms
   (a 4043 ms band, 11× wider). The 6597 ms outlier was the first navigation of a cold
   session; B settles at ~2.6 s warm. Variance is the part that makes a whole-page
   heuristic a bad readiness gate — the number you get depends on traffic you do not
   control.
4. **Request counts do not explain it.** Both arms issue 87–150 requests; B is not
   waiting on more work, it is waiting for the *tail* of the same work to go quiet.
   Angular re-renders incrementally, which is precisely what `:1592` says.

## Then the fix was attempted, and the test suite refuted #836 outright

Removing the argument at both sites turned `tests/api/transports/test_navigation_settled.py`
red — a ratchet from **#580/#584** that neither this spike nor the issue had consulted.
Its module docstring:

> `page.goto(wait_until="domcontentloaded")` returns BEFORE Flow's locale redirect lands
> — measured 591–797 ms with the redirect after. Whatever runs next operates on a page
> about to be navigated away: overlay clicks land on a leaving page, and `page.evaluate`
> raises "Execution context was destroyed".

And both sites are in that test's `_ABSORBED_BY_EXISTING_WAIT` set, *by name*, with the
reason written down: *"`networkidle` waits for the network to go quiet, which a redirect
cannot do without being observed."* The exemption is a recorded decision, not an oversight.

**#836 reads two different call sites as one contradiction.** The comment at what is now
`:1607` governs the **gallery** navigation, which is followed by `_settle_if_redirecting`.
The bootstrap at `:1118` cannot use that settle: it is gated on `self._account_locale`,
and the bootstrap navigation is the very call that *resolves* the locale — so at that
moment it is `None` and the settle is a no-op. The unguarded `await_url_settled` is no
help either; on a migrated host it short-circuits to `None` (`_common.py:554`, added by
#643 after the wait burned 4018 ms per navigation returning that same `None`).

So nothing cheaper currently absorbs the bootstrap redirect. `networkidle` is buying
something real, and the ~2.4 s is its price.

## Verdict on the fix: do not apply it

The measured cost stands and is worth recording. The remedy in #836 does not: it trades
~2.4 s for the #580/#584 class of failure, where DOM work starts against a page that is
still navigating. That class is worse than slow — it is intermittent and account-shaped.

A cheaper correct fix may exist — a bootstrap-specific settle that waits for *either* the
localised URL shape or the migrated shape, rather than for whole-page quiet — but it needs
a labs-served account to measure, and every profile on this machine is served
flow.google.com. Naming that as the blocker rather than guessing.

## What this does NOT settle

- **One account, one host.** `denon82` is served `flow.google.com`. An account served
  labs was not measured, and per the host-membership rule that is a different
  observation, not a safe inference. A cohort whose page *does* hold something open
  would hit the ceiling this probe never saw — the failure mode is real in principle,
  just not present here.
- **`bearer.py:254`** (the experimental transport's 60 s `networkidle`) was not exercised.
  It is in the same exemption set for the same reason.
- **Whether a bootstrap-specific settle would be cheaper.** Needs a labs-served account;
  all profiles here are served flow.google.com.
- **Ten navigations on one machine, one network.** The ratio is stable across them;
  the absolute numbers are not portable.
- **Nothing about generation.** The probe never submits, so it says nothing about the
  long-held `ogiZ0b` the corpus already documents on the image path.
