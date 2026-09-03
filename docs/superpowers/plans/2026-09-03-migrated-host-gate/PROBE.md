# Probe evidence — migrated-host flip timing (#639-A / #639-B)

**Instrument:** `scripts/dev/measure_migrated_host_flip.py` (new, this session)
**Date:** 2026-09-03 · **Machine:** Windows 11, Chrome strategy · **Credits:** 0 (navigation only)
**Raw:** `scripts/dev/_spike_out/migrated_host_flip_20260903_16{5632,}*.json`, `..._170004.json`, `..._170005.json`

## What was measured

After each `page.goto(<labs.google project URL>, wait_until="domcontentloaded")`, `page.url` was
sampled every 25 ms for 10-20 s, recording every change and the offset at which
`flow_host_kind` first answers `"migrated"`.

## Result — 72 navigations, two profiles, zero migrated loads

| profile | cached locale state | resolved | URL built | navs | landed migrated | post-`goto` URL change | `html lang` |
|---|---|---|---|---|---|---|---|
| `ffroliva` | `''` (= `NOT_REDIRECTED`, latched) | `None` | `labs.google/fx/tools/flow/project/<id>` (bare) | 60 | **0** | **none, ever** | `en` |
| `denon82` | `'pt'` | `'pt'` | `labs.google/fx/pt/tools/flow/project/<id>` | 12 | **0** | **none, ever** | `pt` |

`goto` returned in **449-1036 ms** across all 72. Three sweeps on `ffroliva` plus one on `denon82`, spread over ~2 hours.

## What this establishes

1. **The rollout has flapped back on both maintainer accounts.** Yesterday `ffroliva` served the
   migrated frontend (see `docs/LIVE_VERIFICATION_v0.66.0.md`); today it is 60/60 old host. The
   reporter's account went 6/6 migrated yesterday. The flap is real and per-account.
   → **Today the old-host no-regression path IS live-verifiable here and the migrated path is NOT.**
   That is the inverse of yesterday, and the inverse of what the #639 assessment assumed.

2. **Defect B reproduces live on the maintainer's own primary profile.** `profile_ffroliva/.gflow_locale`
   is empty = `NOT_REDIRECTED`; every client bootstrap logged
   `client.account_locale_cached locale=None settle_skipped=True`, i.e. `_resolve_account_locale`
   — and with it the whole #643 `<html lang>` recovery — never ran.
   And `html lang` reads **`en`** on that very page: the value B2 would recover is right there.

3. **A bounded settle (A1) would be pure dead time here.** Not one of the 72 navigations produced a
   post-`goto` URL change, on either the bare or the localised URL shape. There is nothing to wait
   for on a non-migrated load, and no predicate can distinguish "no redirect coming" from "redirect
   not yet arrived" without paying the bound. This is the measured form of the #587 regression
   (`_common.py:181-185`) that `URL_SETTLE_TIMEOUT_MS` and the `NOT_REDIRECTED` cache exist to avoid.

4. **`ffroliva`'s latch is *correct about redirects*.** The bare URL is served as-is and never
   redirected — so `NOT_REDIRECTED` is a true observation for that account. The defect is not the
   observation; it is that the cached observation also switches off the locale *read*. That is
   precisely B2's thesis, and it is why B1 (delete the early return) is the wrong shape: it would
   reintroduce a 4 s settle on an account measured 60/60 to have nothing to settle.

## What this does NOT establish

- **When the flip actually lands on a migrated load.** Zero migrated loads were sampled, so the
  central timing number is still unmeasured. Any design that depends on it (a bounded wait's
  bound; whether `page.url` is already migrated when `goto` returns) remains **LIKELY, not
  CONFIRMED**. The instrument is ready; it needs a migrated load to run against — either a later
  flap on these accounts, or the reporter's permanently-migrated account.
- Whether navigating directly to `flow.google.com/project/<id>` forces the migrated frontend
  (the question that decides if A3 destroys the retry workaround).
