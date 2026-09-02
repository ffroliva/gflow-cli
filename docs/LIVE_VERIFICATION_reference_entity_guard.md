# Live verification — the `referenceEntity` guard (#615, #618, #620)

**Date:** 2026-09-02 · **Profile:** `denon82` · **Cost:** zero credits (two image
generations; images are daily-capped, not billed — see
[E2E_TESTING](E2E_TESTING.md)) · **Transport:** `ui_automation`, headed Chrome

## What was in question

`_intercept_reference_entities` strips character entities the caller did not ask
for, so a "poisoned" entity left in the Flow composer cannot smuggle itself into
an unrelated generation. [#615](https://github.com/ffroliva/gflow-cli/issues/615)
reported that it had never run.

[PR #618](https://github.com/ffroliva/gflow-cli/pull/618) fixes two causes: the
route glob could not match the real namespaced endpoint, and registration moved
from `page.route` to `page.context.route`. It was held unmerged on one question
no offline test can answer:

> `BrowserContext.route` intercepts requests owned by a **dedicated Web Worker**,
> but **not** a **Service Worker**. If Flow delegates via a Service Worker, the
> fix is a no-op that passes every test and changes nothing in production.

## Why this could be settled at all

The guard used to log **only** when it stripped something. "Never ran" and "ran,
nothing to strip" were byte-identical silence, so no run could distinguish them.
[PR #637](https://github.com/ffroliva/gflow-cli/pull/637) adds
`ui_automation.batch_request_intercepted`, emitted on every intercepted request
from a `finally` — every exit, including the parse-error branch. **Absence of that
event is now evidence**, which is what made this an experiment rather than an
argument.

## The A/B

Identical test, identical account, identical prompt. One variable: whether #618's
route fix is present. Both branches carry #637's signal, or the control could not
be read.

| Branch | Route registration | Result |
|---|---|---|
| `bugfix/entity-smuggling-e2e-asserts-620` (**control**, #618 absent) | `page.route("**/batchGenerateImages")` | **FAILED** — `The referenceEntity guard never ran` |
| `scratch/e2e-615-620-proof` (#618 + #637) | `page.context.route(_GENERATION_ROUTE_RE)` | **PASSED** — guard fired |

```
control : 1 failed in 51.25s   (image generated; no batch_request_intercepted)
with fix: 1 passed in 67.72s   (image generated; guard observed the submit)
```

## Ledger

1. **The bug reproduces live.** The control run generated an image successfully
   and the guard never saw the request. #615 is confirmed on the affected
   surface, not merely reasoned about.
2. **The fix works live.** The only changed variable is #618's matcher + level.
3. **It is a dedicated Web Worker, not a Service Worker.** `context.route`
   observes these requests. This is the answer #618 was held for; no
   `service_workers="block"` is needed.
4. **The rewrite does not corrupt the request.** Both runs produced a real image,
   so `route.continue_(post_data=...)` forwards a body Flow still accepts — a path
   no mock exercises and the one that would have broken generation outright.
5. **The test discriminates.** It failed when the guard was dead and passed when
   it was alive. It is not another test that cannot fail — which is precisely what
   #620 was filed about.

## Offline corroboration

The matcher half needs no account. Playwright's own `glob_to_regex_pattern`:

```
'**/batchGenerateImages'      -> ^(.*/)batchGenerateImages$
    https://…/projects/{id}/flowMedia:batchGenerateImages   -> NO MATCH
'**/batchAsyncGenerateVideo*' -> ^(.*/)batchAsyncGenerateVideo([^/]*)$
    https://…/v1/video:batchAsyncGenerateVideoText          -> NO MATCH
```

Note the **video** guard was equally dead. #615 names only the image endpoint;
#618's substring regex covers both.

## Residual limitations

- **Only the browser-driven path is covered.** Direct-wire routes issued through
  Playwright's `APIRequestContext` are not routable at all and bypass the guard
  structurally — [#619](https://github.com/ffroliva/gflow-cli/issues/619). Harmless
  today (no direct-wire route sends `referenceEntities`) but it is fail-open by
  construction, and any claim that "the guard protects generations" carries that
  footnote.
- **Verified on the image path.** The video call site
  (`ui_automation_video.py`) shares the same context manager and the same matcher,
  which the offline check covers, but it was not exercised live here.
- **One account, one cohort, one locale** (`denon82`, classic UI, `hl=en`). Flow's
  UI has an A/B history ([#174](https://github.com/ffroliva/gflow-cli/issues/174));
  a different cohort could delegate differently.
