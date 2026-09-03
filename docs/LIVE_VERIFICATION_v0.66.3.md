# Live Verification — v0.66.3 (`<html lang>` is read after hydration, not before)

**Date:** 2026-09-03 · **Accounts:** `denon82` (pt), `ffroliva` (en) · **Platform:** Windows 11, Chrome strategy
**Credits spent:** **0** — navigation and DOM reads only; nothing submits.

## What had to be proven

One fix ([#651](https://github.com/ffroliva/gflow-cli/issues/651)): the account locale must come
from `<html lang>` **after** Flow's app has set it, not from the `en` shell it serves first.

## Layer 1 — the defect, measured (this is what chose the design)

Sampling `document.documentElement.lang` after `page.goto(wait_until="domcontentloaded")` returns,
on a **pt** account, two consecutive loads (`scripts/dev/measure_html_lang_settle.py`):

| t (ms) | `lang` | `readyState` | DOM nodes |
|---|---|---|---|
| 887 | `en` | interactive | 136 |
| **1510** | `en` | **complete** | 300 |
| 2092 | `en` | complete | 249 |
| **2488** | **`pt`** | complete | 262 |
| 7398 | `pt` | complete | 251 |

Two negative results that mattered more than the positive one:

- **`readyState` is not a hydration signal for `lang`.** It reaches `complete` a full second
  *before* the flip. "Wait for complete, then read" — the obvious fix — would have shipped the
  same bug with more code.
- **DOM node count does not discriminate.** It oscillates 136 → 300 → 249 → 251 and settles
  within 2 of its pre-flip value.

Nothing cheap predicts the flip, so the fix **observes** it.

## Layer 2 — the fix, on a live page (COMPONENT measurement)

`scripts/dev/verify_lang_settle_fix.py`, part A. This calls the helper directly against a real
bootstrap page — **it is a component measurement and is labelled as one.**

```
[A · COMPONENT — helper on a live page]  profile=denon82
    naive early read : 'en'
    _settled_lang    : 'pt'   (+638 ms)
    verdict          : CAPTURED THE FLIP
```

## Layer 3 — the real bootstrap (END-TO-END measurement)

Part B, on the account that exercises the *timeout* branch — its locale equals the `en` shell, so
the attribute never changes:

```
client.account_locale_lang_unchanged  lang=en  waited_ms=4000  reason=TimeoutError
client.account_locale_resolved        locale=en  source=html_lang
```

Correct answer, and the cost is visible: **the 4 s bound, once per process** (bootstrap ~2.7 s →
~6 s on this account). That is the price of not guessing, and it is bounded. The event name makes
"the shell value was already right" distinguishable from "we timed out" in any field log.

**Layers 2 and 3 are reported separately on purpose.** Collapsing a component number into a
user-facing claim is precisely the error corrected at the top of
[LIVE_VERIFICATION_v0.66.1](LIVE_VERIFICATION_v0.66.1.md), and this release is not going to
repeat it one version later.

## Layer 4 — offline

- Tests written red first; **A/B control**: neutering `_settled_lang` fails **exactly 3** — the
  two new cases plus the probe-failure test, since the helper owns the error handling too.
- `1663 passed / 3 skipped` across `tests/api`, `tests/features`, `tests/mcp`, `tests/worker`.
- `ruff check` / `ruff format --check` clean on `src tests`; `pyright src` at the `develop`
  baseline; repo hygiene, doc links, website-docs PII and the `website/docs` mirror all green.
- CI on [#652](https://github.com/ffroliva/gflow-cli/pull/652): **15/15**, including the
  SonarCloud quality gate.

## Layer 5 — MCP

No CLI leaf, option, or request-DTO field changed. The fix is in `FlowApiClient`, which the MCP
worker constructs identically to the CLI, so both surfaces inherit it and no `mcp/tools.py`
change is implied.

## Recorded as NOT verified rather than omitted

- **A non-`en` account on the MIGRATED origin.** The fix is proven on the old host, where the
  race also occurs — that is what made it testable at all. The reporter's pt-BR migrated account
  is where it originally surfaced, and it has not been re-run there since the fix.
- **The 4 s bound against a cold first load.** One observed session had `lang` fail to flip
  within 4 s on its first navigation, yet flip in 638 ms two seconds later. Either Flow served
  English on that load (its URL settle also timed out, so no redirect either) or the bound is
  short cold. Unresolved; `client.account_locale_lang_unchanged` is the signal that will tell us.
- **The `was=pt now=?` demotion introduced in v0.66.2.** Folding only the URL-derived segment
  means a learned `pt` drops to PROVISIONAL whenever the URL settle times out. Self-healing on
  the next redirecting run, and the locale is re-derived per run, so nothing is permanently lost
  — but it is the same *shape* of demotion #643 complained about and deserves a decision.
- **Whether a wrong locale segment causes a redirect bounce** (`/fx/en/...` on a non-`en`
  account) or merely an English UI. Only the reporter's account can settle this.
- **Full-suite coverage %.** Run without `--cov` (an unscoped `pytest --cov` OOMs this machine);
  the 80% floor is enforced by CI.
