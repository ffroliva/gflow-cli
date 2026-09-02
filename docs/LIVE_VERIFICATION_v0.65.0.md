# Live verification — v0.65.0

**Date:** 2026-09-02 · **Profile:** `denon82` · **Transport:** `ui_automation`, headed
Chrome · **Cost:** zero credits (image generations only; images are daily-capped, not
billed — see [E2E_TESTING](E2E_TESTING.md))

## What this release changes, and how each part was verified

| Change | Surface | How verified |
|---|---|---|
| `referenceEntity` guard now runs (#615, #620) | generation (image + video) | **Live, A/B-controlled** — see below |
| `chain` / `movie` reject `duration` × `model` before spending (#634, #635) | validation, pre-submit | Offline + negative controls (see *Why no live run* below) |
| `i2v --duration` with no `--model` exits 2, not 1 (#630) | validation, pre-submit | Offline; original repro re-run by hand in #632 |
| Image e2e cost model corrected | docs / markers | Not a runtime change |

## The guard — live A/B

Full ledger:
[LIVE_VERIFICATION_reference_entity_guard](LIVE_VERIFICATION_reference_entity_guard.md).
Summary, three runs, same account and prompt, one variable:

| Configuration | Result |
|---|---|
| without the fix (**control**) | `FAILED — The referenceEntity guard never ran` |
| with the fix (stacked branches) | `PASSED` |
| **on merged `develop` @ `2af6c08`** | **`PASSED`** |

```
control        : 1 failed in 51.25s   (image generated; no batch_request_intercepted)
with fix       : 1 passed in 67.72s
merged develop : 1 passed in 44.97s
```

The control is what gives the passes meaning. Without it a green tick proves only that a
test ran — which is precisely the defect #620 was filed about.

**Established by the run, not by argument:**

1. **#615 reproduces live.** The control generated an image successfully while the guard
   never saw the request.
2. **Flow delegates to a dedicated Web Worker**, not a Service Worker. `context.route`
   suffices; no `service_workers="block"` needed. This is the question that held the fix.
3. **`route.continue_(post_data=...)` does not corrupt the body** — every run produced a
   real image. Had the rewrite mangled the request, the fix would have broken generation
   outright, and no mock exercises that path.

## Why the duration guards were not live-run

They are pure-Python validation that **refuses before any transport call**. Exercising the
old behaviour end-to-end would mean deliberately spending credits to watch a chain crash
mid-run — the exact failure the change exists to prevent. They are covered by:

- A red state that reproduces the money bug in a test: the chain guard tests give link 0 a
  working result, so without the fix the log reads `chain_link_completed index=0` before
  the `ValueError` — i.e. link 0 rendered and billed.
- Negative controls pinning what must **not** change (a per-link Veo override still
  generates; `omni-flash` + duration is still accepted in movie; duration with no model is
  still accepted).
- CLI-level `--dry-run` rejection tests.

Recorded here rather than silently omitted, per step 4b.

## Residual limitations

- **Direct-wire routes bypass the guard structurally**
  ([#619](https://github.com/ffroliva/gflow-cli/issues/619)) — `APIRequestContext` is not
  routable at any level. Harmless today; now also in [KNOWN_ISSUES](../KNOWN_ISSUES.md).
- **The guard was live-verified on the image path.** The video call site shares the same
  context manager and matcher (covered offline against the real URL), but was not
  exercised live.
- **One account, one UI cohort, one locale** (`denon82`, classic, `hl=en`). Flow's UI has
  an A/B history ([#174](https://github.com/ffroliva/gflow-cli/issues/174)).
