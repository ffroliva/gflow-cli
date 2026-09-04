---
name: credit-free-route-abort-verification
description: GOLD debug technique — Playwright route.abort() captures a Flow submit payload WITHOUT spending a credit; verify wire behavior credit-free before any paid run
---

The single highest-leverage technique from the movie-consistency debug (2026-06-07):
**verify what the Flow UI will submit WITHOUT triggering generation or spending a
credit**, by intercepting the generate request and aborting it after reading its
body.

```python
captured = {}
async def _on_route(route):
    if not captured:
        captured["url"] = route.request.url
        captured["post_data"] = route.request.post_data  # the full submit JSON
    await route.abort()  # request never reaches Google -> NO credit charged
await page.route("**/video:batchAsyncGenerateVideo*", _on_route)
# ... drive the real attach + submit; then parse captured["post_data"]
```

`scripts/dev/spike_movie_attach_payload.py` (gflow-cli) is the reusable harness:
it drives the REAL transport methods (attach + `_send_prompt`), aborts the submit,
and prints `referenceEntities` / `referenceImages` / `videoModelKey` from the
captured request.

**Why it matters / how to apply:**
- Use this BEFORE any paid run to confirm a wire hypothesis (does the entity ride?
  does omni-flash route to R2V? does a model preserve refs?). It turned a
  multi-credit guessing loop into $0 iteration.
- It distinguishes **what the UI sends (request)** from **what the listener reads
  (response)** — the two have DIFFERENT shapes. The movie backstop bug was reading
  the request field `requests[].referenceEntities` against the RESPONSE body, which
  re-keys it to `media[].mediaMetadata...videoGenerationEntityInputs`. **Always
  verify a backstop/assertion against a REAL captured response, never a fabricated
  fixture** [[e2e-exposes-synthetic-fixture-bugs]].
- Pair it with the credit-free recon spikes (passive net capture, DOM dumps) in
  [[flow-credit-free-spike-harness]].
- Caveat: `route.abort()` proves the SUBMIT payload, not generation success — a
  final paid run still validates poll + download + the response-echo backstop.

Relates to [[movie-consistency-feature]], [[rest-path-capability-matrix]],
[[verification-ledger-5-layer]].
