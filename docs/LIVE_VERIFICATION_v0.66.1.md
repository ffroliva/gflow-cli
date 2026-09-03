# Live Verification — v0.66.1 (migrated-origin fixes for #639/#643)

**Date:** 2026-09-03 · **Account:** ffroliva@gmail.com · **Platform:** Windows 11, Chrome strategy
**Credits spent:** **0** — one Imagen image (images are credit-free) plus read-only probes.

> ## ⚠️ Correction (2026-09-03, same day) — Layer 1's latency numbers are not user-visible
>
> **Layer 1 below measured `get_ui_driver` in isolation, on a page already sitting on
> `flow.google.com`.** That precondition never holds on a real run: `project_editor_url`
> only ever builds a `labs.google` URL and the hop to the migrated origin is a
> *post-`goto`* redirect that neither settle path waits for, so on the CLI route the guard
> read a pre-redirect URL and declined.
>
> The reporter of #639 measured the real path on three consecutive v0.66.1 runs: **57.0 /
> 57.1 / 58.3 s**, terminating through `ui_automation_video.selector_probe_failed` — the
> slow path — with `ui_driver.migrated_host_bail` **absent from the timeline entirely**.
>
> So of the rows below: `"get_ui_driver": {"ms": 0}` and **`time to exit 36 — 0 ms` are
> true of the isolated probe and false of every CLI run.** The claim
> *"`ui_driver.migrated_host_bail` is logged, so the fast path is observable rather than
> inferred"* is the one the field timeline falsified — the event was never emitted, and
> nothing here noticed because the isolated probe emitted it.
>
> The same gap hid the locale fix: `client.py` returned before `_resolve_account_locale`
> on a `NOT_REDIRECTED` cache, so the `<html lang>` recovery Layer 1 exercised **directly**
> is unreachable through the client on exactly the profiles that need it. `profile_ffroliva`
> is latched that way today.
>
> Both are fixed for v0.66.2 — see
> [`docs/superpowers/plans/2026-09-03-migrated-host-gate/`](superpowers/plans/2026-09-03-migrated-host-gate/PLAN.md).
> **Layer 2 (no regression on the old host) stands unchanged and is still the load-bearing
> result here.** The lesson is recorded rather than the numbers quietly edited: a function
> measured in isolation is not a verification of the path users take.

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
| **time to exit 36** (isolated probe) | ~36 s | **0 ms** |
| **time to exit 36** (real CLI run) | ~57 s | **~57 s — UNCHANGED, see the correction above** |
| locale on migrated origin | `null` (and demoted a learned locale) | **`en`** recovered from `<html lang>` |

~~`ui_driver.migrated_host_bail` is logged, so the fast path is observable rather than inferred.~~
**Retracted.** It is logged by the isolated probe and by nothing on the CLI route — the field
timeline has no such event. Observability of a path you did not exercise is not observability.

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
