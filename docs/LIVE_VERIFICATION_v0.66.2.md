# Live Verification — v0.66.2 (the migrated-host gate actually fires; the locale cache stops lying)

**Date:** 2026-09-03 · **Account:** ffroliva@gmail.com · **Platform:** Windows 11, Chrome strategy
**Credits spent:** **0** — two Imagen images (images are credit-free) plus 72 read-only navigations.

## What had to be proven, and the honest constraint

This release fixes defects that v0.66.1's *own* verification missed, so the bar for it is higher
than usual: **v0.66.1 measured `get_ui_driver` in isolation on an already-migrated page and
reported `0 ms`, which was true of the probe and false of every CLI run.** See the correction at
the top of [LIVE_VERIFICATION_v0.66.1](LIVE_VERIFICATION_v0.66.1.md).

**The constraint this time is the inverse of last time.** Google's rollout flapped *back* on both
maintainer accounts during this work:

| profile | navigations | landed migrated | landed old host |
|---|---|---|---|
| `ffroliva` | 60 | **0** | 60 |
| `denon82` | 12 | **0** | 12 |

Measured with `scripts/dev/measure_migrated_host_flip.py` across three sweeps on `ffroliva` and
one on `denon82`, over ~2 hours (the raw per-navigation record is in the `scripts/dev/_spike_out/` JSON the
instrument writes; the planning artifacts were consolidated at release per the repo's
spec-to-memory convention).

So **the migrated path was not reachable from this machine and is NOT live-verified here.** It is
unit-tested and A/B-controlled only. That is stated in the NOT-verified section below rather than
folded into the green column — which is precisely the mistake being corrected.

What *is* live-verifiable is the property that matters most today: **the old host, which 100% of
real loads currently take, is unaffected** — and the locale behaviour, which is fully exercisable.

## Layer 1 — the locale cache stops conflating two different questions

`profile_ffroliva/.gflow_locale` was **empty** — `NOT_REDIRECTED` — before the run. Under v0.66.1
that state returned before `_resolve_account_locale`, so the `<html lang>` recovery #643 shipped
could never run on it.

```
cache before:  ''                                                    (NOT_REDIRECTED)
client.account_locale_resolved   locale=en  source=html_lang
client.account_locale_cached     locale=en  settle_skipped=true
cache after:   ''                                                    (NOT_REDIRECTED — unchanged)
```

Three things at once, all measured:

1. **The locale is recovered from the document**, not the URL — `source=html_lang`. The latch no
   longer blocks it.
2. **The settle stays skipped** (`settle_skipped=true`). The #587 win is intact — which is why
   deleting the early return outright was rejected: on this account "not redirected" is a *true*
   observation, confirmed 60/60.
3. **The cached state is unchanged.** This is the correction the council forced (see below): the
   cache answers *"does Flow redirect this account?"*, and a `<html lang>` attribute is no
   evidence of a redirect — every account has one.

### The regression the council caught, and the measurement that proved it

The first cut of this fix folded the recovered locale into the cache. Two independent reviewers
(D1 correctness, D4 tests) flagged it; `scripts/dev/measure_locale_probe.py` then reproduced it
without being asked:

```
BEFORE:  ffroliva  warm  7.41s   cached 'en'    !! warm arm slower than cold — investigate
                                 transport.url_settle_gave_up  timeout_ms=4000.0   (x2)

AFTER:   ffroliva  cold  6.99s   cached '?'
         ffroliva  warm  2.66s   cached ''      settle off, locale still 'en'
```

Writing the locale in flipped `cached != NOT_REDIRECTED`, which turned the settle back on
permanently and cost the full 4 s `URL_SETTLE_TIMEOUT_MS` on every future bootstrap — the exact
cost #587 exists to remove.

**Tracing it found a live defect in v0.66.1 itself, present on `develop` today.** #643's
`<html lang>` fallback returns a segment for an account Flow serves *bare* and never redirects, and
`next_locale_state` recorded that as evidence of a redirect. So **any** non-redirecting account
with a `lang` attribute has been paying 4 s per bootstrap since v0.66.1 — no latch required. The
fix folds only the **URL-derived** segment into the state; the lang-derived one is used in-process
for URL building and never persisted.

## Layer 2 — the risk this fix introduces, exercised rather than assumed

Recovering the locale changes what gflow navigates to: this profile previously built the **bare**
`labs.google/fx/tools/flow/project/<id>` and now builds `/fx/en/tools/flow/project/<id>`. If `en`
were the wrong segment, every navigation would bounce.

```
ui_automation.entering_existing_project  url=https://labs.google/fx/en/tools/flow/project/c5550ed7-…
ui_automation.url_stable_after_goto      same URL, +549 ms, settle_skipped=false
ui_driver.ui_mode.attempt_exit_agent     +507 ms
ui_driver.bound                          mode=classic
```

`url_stable_after_goto`, **not** `url_redirected_after_goto`: Flow accepted the segment and did
not bounce it. Confirmed on both live runs.

## Layer 3 — no regression on the old host: full generation, exit 0, twice

```
run 1 (pre-council-fix build)   EXIT=0   768×1376 JPEG · 427.8 KB · ff d8 ff
run 2 (final build)             EXIT=0   768×1376 JPEG · 413.5 KB · ff d8 ff
```

Real images generated and written; `ui_driver.bound  mode=classic` on both; no
`ui_driver.migrated_host_bail` event on either — correct, the host is `labs.google`.

**On wall-clock:** run 1 took 36.6 s and run 2 took 86 s, and **neither number is evidence about
this diff.** Run 2's extra time is Flow's own generation latency plus a 34.4 s
`browser_teardown.context_close_error` — a bounded Chrome-close timeout at `api/_engine.py:159`
that fires *after* all generation work, is already handled (warning → force close → exit 0), and
is untouched by this branch. A single-run timing delta sits well inside that variance, so the
"adds no wait" claim rests on the A/B control and on code shape (`page.url` is a cached property;
`flow_host_kind` is one `urlsplit` plus a dict lookup; `_media_panel_present` uses `.count()` and
does not wait) — **not** on the totals. Claiming "faster" from these runs would repeat exactly the
error this release corrects.

## Layer 4 — offline

- Tests written **red first**: 4 red for the locale latch, 4 red for the host gate, before any
  production change.
- **A/B controls**, re-derived independently by a council reviewer with its own harness:
  neutering `raise_if_migrated` fails **exactly 7** and **no** old-host test; restoring the
  pre-#639 early return fails **exactly 5**. Nothing passes vacuously in either direction.
- The council's must-fix added two more tests that would have caught the regression it found:
  a two-bootstrap cycle (one bootstrap cannot observe a cache flipping the settle back on), and
  a lang-only-locale case pinning that a `lang` attribute is not evidence of a redirect.
- `ruff check` / `ruff format --check` clean on `src tests`; `pyright src` at the `develop`
  baseline; repo hygiene, doc links, website-docs PII, and the `website/docs` mirror all green.

## Layer 5 — the MCP surface

No CLI leaf, option, or request-DTO field changed, so `tests/mcp/test_cli_parity.py` needs no new
mapping and `worker/codec.py` no new payload key — the fix is in the transport both surfaces
share (independently traced by the parity reviewer: `worker/daemon.py` and `mcp/tools.py` both
construct the same `FlowApiClient`). What *is* MCP-specific is the queue envelope, different code
from the CLI's `--json` path, carrying the flag the reporter's orchestrator actually reads:

```
queue error record:  exit_code = 36,  retryable = True
```

Pinned by `tests/worker/test_daemon.py::test_migrated_host_error_crosses_the_queued_path`. Noted
honestly: that test pins the write side; the `wait=True` read-back in `mcp/tools.py` is generic
code exercised by its neighbours, so the round trip is pinned end-to-end only by construction.

`docs/MCP.md` listed the retryable classes by name and omitted three of them
(`FlowHostMigratedError`, `UiModeUnavailableError`, `SyncPartialError`); the list now matches
`errors.RETRYABLE_ERRORS` exactly, mirror regenerated.

Side effect: `mcp/tools.py` builds project editor links from `account_locale_for()`, which reads
the *cached* state. Because the lang-derived locale is deliberately not persisted, those links are
unchanged by this branch — the earlier claim that they become "account-correct" was wrong and is
retracted here rather than shipped.

## Layer 6 — the migrated path, verified independently by the reporter

Everything above was measured on maintainer accounts that had flapped back to the old host. The
migrated path was verified by [@maipmacrothorax-75](https://github.com/maipmacrothorax-75) on a
permanently-migrated account — branch installed from git, reverted afterwards, zero credits,
posted to [#639](https://github.com/ffroliva/gflow-cli/issues/639).

| | v0.66.1 | branch, before the `mode_control` guard | v0.66.2 |
|---|---|---|---|
| total, per run | 57.0 / 58.3 / 57.0 s | 19.4 / 15.0 / 14.9 s | **4.08 – 4.26 s** |
| `ui_driver.migrated_host_bail` | **absent** | present, `at=detect_ui_mode` | present, **`at=mode_control`** |
| `mode_control.ensure_media_incomplete` | every run | every run | **never** |
| `ui_automation_video.selector_probe_failed` | every run | never | never |

Typical v0.66.2 timeline on a migrated load:

```
2.82  ui_automation.url_stable_after_goto
3.44  ui_driver.migrated_host_bail   at=mode_control   (+0.62)
4.09  error_raised                   FlowHostMigratedError, exit 36, retryable
```

**~0.6 s from the host becoming knowable to the abort**; ~4 s total, essentially browser launch
plus navigation. Nine runs on the final build, `at=mode_control` every time.

Two things this settles that no maintainer measurement could:

1. **The `at=` field earned itself.** The intermediate run bailed at `detect_ui_mode`, not
   `exit_agent_mode` — which is how the reporter identified, from the log alone, that the
   `exit_agent_mode` guard sat *after* the ~11 s dismissal rather than before it. A late review
   angle found the same gap independently; the log field is what made it visible from outside
   the codebase.
2. **57 s → 4 s is real**, and it is the difference between a caller's retry loop being viable
   and not.

**One unexplained anomaly, recorded because it was observed.** The first run after a fresh
install ended in `error_unhandled` at 3.42 s — after `setup_shared_page`, before
`entering_existing_project` — `exception_class: "Error"` (Playwright's base class), message
redacted to hashes. It did not reproduce; the other 8 runs on that build were clean exit 36. A
one-off that cannot be reproduced is not evidence, and it is here only so a second sighting has
something to match against.

## Recorded as NOT verified rather than omitted

- ~~**The migrated path firing fast.**~~ **Now verified — see Layer 6.** This entry originally
  said unverified because 72/72 navigations *from this machine* landed on the old host. That was
  true of this machine and false of the project: the reporter had already run it on a
  permanently-migrated account and posted the numbers to #639 before the tag was cut, and they
  were not read in time. Kept rather than deleted — "I could not measure it" and "nobody measured
  it" are different claims, and this doc conflated them.
- **Driving the migrated frontend.** Still impossible. #639 stays open; this PR says `Refs`.
- **A non-English profile recovering live.** `denon82` is cached `"pt"` (it genuinely redirects),
  so the lang-recovery path was exercised live only on `en`. The `pt` case is unit-tested.
- **The `en-GB` → `en` region reduction where region is load-bearing** (`zh-Hans`/`zh-Hant`).
  Unchanged from v0.66.1, still only two locales observed.
- **A defect this verification found, shipped in v0.66.2 and not fixed by it.** On the reporter's
  **pt-BR** account the locale resolves to `en`, while `document.documentElement.lang` reads `pt`
  on both `flow.google.com/` and `flow.google.com/project/<id>`, `navigator.language` is `pt-BR`,
  and the whole UI renders in Portuguese. The probe logs at ~1.63 s, before
  `entering_existing_project` at ~2.31 s — early enough that it may be reading the initial HTML
  shell before the app sets `lang`. **The mismatch is measured; the mechanism is a hypothesis and
  was not instrumented.** This is precisely the population #643 was written for, so the
  `<html lang>` fallback (shipped v0.66.1) can latch a locale the account does not use. Tracked
  separately — it is not a migration bug.
- **Full-suite coverage %.** The targeted suites ran without `--cov` (an unscoped `pytest --cov`
  OOMs this machine); the 80% floor is enforced by CI.
