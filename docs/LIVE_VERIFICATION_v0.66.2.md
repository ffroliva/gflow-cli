# Live Verification — v0.66.2 (the migrated-host gate actually fires; the locale latch releases)

**Date:** 2026-09-03 · **Account:** ffroliva@gmail.com · **Platform:** Windows 11, Chrome strategy
**Credits spent:** **0** — one Imagen image (images are credit-free) plus 68 read-only navigations.

## What had to be proven, and the honest constraint

This release fixes two defects that v0.66.1's own verification missed, so the standard for it is
higher than usual: **v0.66.1 measured `get_ui_driver` in isolation on an already-migrated page
and reported `0 ms`, which was true of the probe and false of every CLI run.** See the
correction at the top of [LIVE_VERIFICATION_v0.66.1](LIVE_VERIFICATION_v0.66.1.md).

**The constraint this time is the inverse of last time.** Google's rollout flapped *back* on
both maintainer accounts during this work:

| profile | navigations | landed migrated | landed old host |
|---|---|---|---|
| `ffroliva` | 56 | **0** | 56 |
| `denon82` | 12 | **0** | 12 |

Measured with `scripts/dev/measure_migrated_host_flip.py` across three sweeps over ~2 hours
(full record: [`PROBE.md`](superpowers/plans/2026-09-03-migrated-host-gate/PROBE.md)).

So **the migrated path was not reachable from this machine and is NOT live-verified here.** It
is unit-tested and A/B-controlled only. That is stated in the NOT-verified section below rather
than folded into the green column — which is precisely the mistake being corrected.

What *is* live-verifiable today is the property that matters most: **the old host, which 100%
of real loads currently take, is unaffected.**

## Layer 1 — the locale latch releases, on a real load (fix B)

`profile_ffroliva/.gflow_locale` was **empty** — `NOT_REDIRECTED`, latched — before the run.
Under v0.66.1 that state returned before `_resolve_account_locale`, so the `<html lang>`
recovery #643 shipped could never run on it.

```
client.account_locale_resolved   locale=en  source=html_lang
client.account_locale_cached     locale=en  settle_skipped=true
client.account_locale_state      was=""     now="en"
```

Three things at once, all measured, none inferred:

1. **The locale was recovered from the document**, not the URL — `source=html_lang`.
2. **The settle stayed skipped** (`settle_skipped=true`) — the #587 win is intact. This is why
   deleting the early return (option B1) was rejected: on this account the cached "not
   redirected" is a *true* observation, and a bounded settle would be dead time.
3. **The latch released** — `was="" now="en"`. The on-disk cache now reads `en`.

## Layer 2 — the risk that release introduces, exercised rather than assumed

Recovering the locale changes what gflow navigates to: this profile previously built the **bare**
`labs.google/fx/tools/flow/project/<id>` and now builds `/fx/en/tools/flow/project/<id>`. If
`en` were the wrong segment, every navigation would bounce. Measured:

```
ui_automation.entering_existing_project  url=https://labs.google/fx/en/tools/flow/project/c5550ed7-…
ui_automation.url_stable_after_goto      settle_skipped=false     (+415 ms, no redirect)
ui_driver.ui_mode.attempt_exit_agent     (+446 ms)
ui_driver.bound                          mode=classic             (+669 ms)
```

`url_stable_after_goto`, not `url_redirected_after_goto`: Flow accepted the segment and did not
bounce it. **1.53 s** from entering the project to a bound driver.

## Layer 3 — no regression on the old host: full generation, exit 0

```
EXIT=0    total 36.6 s   (v0.66.1 baseline on the same path: 42.2 s)
tmp/lv662/images/2026-09-03/104b5664-…_1.jpg
768 × 1376 JPEG · 427.8 KB · magic bytes ff d8 ff
```

Real image generated and written. The guard runs at three new call sites on this exact path and
adds **no wait and no navigation**: `page.url` is a cached property and `flow_host_kind` is one
parse plus a dict lookup. No `ui_driver.migrated_host_bail` event appears — correct, the host is
`labs.google`.

## Layer 4 — offline

- Tests written **red first**: 4 red for the locale latch, 4 red for the host gate, before any
  production change.
- **A/B controls.** Neutering `raise_if_migrated` fails **exactly 7** — the flip case, the
  agent-dismissal case, the per-Page case, the log-event case, and the three v0.66.1 entry-case
  tests — and **no** old-host test. Restoring the pre-#639 early return fails **exactly 5** —
  the four latch cases plus the BDD scenario. Nothing passes vacuously in either direction.
- `1655 passed / 3 skipped` across `tests/api`, `tests/features`, `tests/mcp`, `tests/worker`.
- `ruff check` and `ruff format --check` clean on `src tests`; `pyright src` **78 errors =
  the `develop` baseline**, no new type errors.
- Repo hygiene, doc links, website-docs PII, and the `website/docs` mirror all green.

## Layer 5 — the MCP surface

No CLI leaf, option, or request-DTO field changed, so `tests/mcp/test_cli_parity.py` needs no
new mapping and `worker/codec.py` no new payload key — the fix is in the transport both surfaces
share. What *is* MCP-specific is the queue envelope, which is different code from the CLI's
`--json` path and carries the flag the reporter's orchestrator actually reads:

```
queue error record:  exit_code = 36,  retryable = True
```

Pinned by `tests/worker/test_daemon.py::test_migrated_host_error_crosses_the_queued_path`.
`docs/MCP.md` listed the retryable classes by name and omitted `FlowHostMigratedError`; fixed,
along with the `website/docs` mirror.

Side effect worth noting: `mcp/tools.py` builds project editor links from
`account_locale_for()`, which returned nothing on a latched profile and now returns the
recovered locale — those links become account-correct as a consequence of fix B.

## Recorded as NOT verified rather than omitted

- **The migrated path firing fast.** 68/68 navigations landed on the old host, so no migrated
  load was available to time. The gate is proven by unit tests and A/B control, **not** by a
  live run. The instrument is committed and ready; the honest next step is either a later flap
  or a run from the reporter's permanently-migrated account.
- **The achieved time-to-exit-36 on a real migrated run.** Unmeasured, and deliberately not
  estimated. The design does not depend on it: the guard sits at points the run already blocks,
  so it costs nothing regardless of when the redirect lands. No number is claimed here.
- **Driving the migrated frontend.** Still impossible. #639 stays open; this PR says `Refs`.
- **A non-English latched profile recovering live.** `denon82` reads `lang=pt` and is cached
  `"pt"` — not latched — so the latch-release path was exercised live only on `en`. The `pt`
  case is unit-tested.
- **The `en-GB` → `en` region reduction where region is load-bearing** (`zh-Hans`/`zh-Hant`).
  Unchanged from v0.66.1, still flagged in the docstring, still only two locales observed.
- **Full-suite coverage %.** The targeted suites were run without `--cov` (an unscoped
  `pytest --cov` OOMs this machine); the 80% floor is enforced by CI.
