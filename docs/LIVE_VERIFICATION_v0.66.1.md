# Live Verification — v0.66.1 (migrated-origin fixes for #639/#643)

**Date:** 2026-09-03 · **Account:** ffroliva@gmail.com · **Platform:** Windows 11, Chrome strategy
**Credits spent:** **0** — one Imagen image (images are credit-free) plus read-only probes.

## What had to be proven

Two fixes, both on the migrated-origin path, and both verifiable live because the account is
inside Google's rollout:

1. `get_ui_driver` fails fast on `flow.google.com` instead of probing a DOM that cannot answer.
2. The account locale is recovered from `<html lang>` when the migrated URL carries no segment.

Plus the property that matters more than either: **the old host still works.**

## Layer 1 — the migrated path, measured on a real migrated load

```json
{
  "final_url": "https://flow.google.com/project/c5550ed7-...",
  "host_kind": "migrated",
  "get_ui_driver": { "raised": "FlowHostMigratedError", "ms": 0,
                     "exit_code": 36, "retryable": true, "names_host": true },
  "await_url_settled": { "result": null, "ms": 0 },
  "locale_recovery": { "html_lang": "en-GB", "from_url": null, "from_lang_attr": "en" }
}
```

| | before | after (measured) |
|---|---|---|
| `detect_ui_mode` poll window | ~8 s | skipped |
| crop selector cascade | ~24 s | skipped |
| `await_url_settled` | 4018 ms | **0 ms** |
| **time to exit 36** | **~36 s** | **0 ms** |
| locale on migrated origin | `null` (and demoted a learned locale) | **`en`** recovered from `<html lang>` |

`ui_driver.migrated_host_bail` is logged, so the fast path is observable rather than inferred.

## Layer 2 — no regression on the old host (full generation, exit 0)

The rollout still flaps on this account, and a `gflow image t2i --project <existing>` run landed
on the **old** host during verification:

```
EXIT=0   ELAPSED_MS=42168
status: ok | model: NARWHAL
dims: 768x1376 | tmp/lv661/images/2026-09-03/0add6af6-...-_1.jpg
```

Real image generated and written. The host guard is scoped to the migrated origin and does not
short-circuit the path that works.

## Layer 3 — offline

- 5 + 8 tests written **red first** across the two fixes.
- **A/B controls:** neutering the early bail fails exactly 3; neutering the locale fix fails
  exactly 8 — one per claimed behaviour, no test passing vacuously. No-regression tests pass in
  **both** directions.
- `1614 passed / 3 skipped`; ruff clean; pyright 78 = `develop` baseline.

## Recorded as NOT verified rather than omitted

- **Driving the migrated frontend.** Still impossible; #639 stays open. These fixes make the
  failure fast and honest, not survivable.
- **A non-English migrated locale end to end.** `html lang=pt` was measured on `denon82`'s
  migrated load, but the recovery path was exercised live only on `en-GB` → `en`. The `pt` case
  is unit-tested, not live-run.
- **The `en-GB` → `en` region reduction against a locale where region is load-bearing**
  (`zh-Hans`/`zh-Hant`). Only two locales observed; flagged in the docstring.
