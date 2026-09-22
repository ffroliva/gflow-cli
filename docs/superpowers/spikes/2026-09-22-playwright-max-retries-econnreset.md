# Playwright `APIRequestContext.get(max_retries=…)` vs ECONNRESET — measured

**Date:** 2026-09-22 · **Issue:** [#895](https://github.com/ffroliva/gflow-cli/issues/895)
**Script:** [`scripts/dev/spike_playwright_max_retries.py`](../../../scripts/dev/spike_playwright_max_retries.py)
**Raw result:** `scripts/dev/_spike_out/e4_playwright_max_retries.json` (gitignored)
**Cost:** $0 — localhost only. No Flow account, no browser, no credits.

## Why

#895's design rests on two claims that had only been **read from the shipped driver**
(`driver/package/lib/coreBundle.js`, `_sendRequestWithRetries`), never measured:

1. that `max_retries` retries a transport-level `ECONNRESET` on `APIRequestContext.get`;
2. that its backoff is charged to the *same* call's timeout budget.

Claim 2 decides whether `max_retries=2` on our 180 s download is a ~180 s worst case or a
**9-minute** one — and the download holds `_generate_lock` (`ui_automation.py:1002`), shared
with every other generation on the client. The council rated that Critical. An unmeasured
claim is a guess with formatting, so it was measured.

## Method

A local socket server answers the first *N* connections with a TCP RST (`SO_LINGER` 0 +
`close()`, which emits RST rather than FIN) and serves a valid 200 afterwards. Three arms,
so the retry is proven by a **control that fails**, not by one green run
(`[[ab-control-before-shipping-a-fix]]`).

Playwright pinned `>=1.61.0,<1.62.0` (`pyproject.toml:75`). Uses
`playwright.request.new_context()` — the same `APIRequestContext` implementation `page.request`
exposes, without paying for a browser.

## Result — conclusive

| arm | fail | `max_retries` | timeout | outcome | expected | elapsed | server conns |
|---|---|---|---|---|---|---|---|
| **A** recovers | once | 2 | 10 s | **SUCCESS** | SUCCESS | 1.03 s | **2** |
| **B** control | once | **0** | 10 s | **FAILURE** | FAILURE | 0.75 s | 1 |
| **C** budget | always | 2 | **5 s** | FAILURE | FAILURE | **1.57 s** | **3** |

- **A vs B is the controlled pair.** Same injected fault; the only difference is
  `max_retries`. With it, the transfer completes and the server sees a second connection.
  Without it, the call dies.
- **Arm B's error is the reporter's, verbatim:** `Error: APIRequestContext.get: read
  ECONNRESET`. #895 now has a faithful $0 local reproduction.
- **Arm C settles the budget.** Three attempts at a 5 s per-call timeout finished in
  **1.57 s**. Per-attempt timeouts would have given ~15 s. The 250 ms → 500 ms backoff
  measured here matches the driver source, and the whole sequence stays inside one budget.
- **`max_retries=N` means N retries, i.e. N+1 attempts** — arm C made 3 connections for
  `max_retries=2`, and Playwright's own message says `Failed after 3 attempt(s)`. That
  matches `_retry.py:39 MAX_ATTEMPTS = 3`, so the project's existing policy and the native
  one agree without tuning.

## What this decides for #895

- **Use native `max_retries=2`.** No Python retry wrapper, no second retry policy, no custom
  backoff, no new constant. The council's Critical risk — `playwright.TimeoutError`
  subclassing `Error` and tripling a 180 s wait — **cannot arise**, because the driver matches
  on `e.code === "ECONNRESET"` and never on a Python exception type.
- **Worst case on our download stays ≈180 s**, so the per-attempt timeout does not need to
  shrink and `_generate_lock` is not held longer than today.
- **The socket server is the regression harness.** Task 1's unit tests can inject the real
  fault instead of faking an exception, and the ablation (drop `max_retries`, assert failure)
  is arm B.

## Second finding: `page.route` cannot inject this fault

The council's review and the issue's own scenario doc both proposed proving the retry
end-to-end with `page.route(..., r.abort("connectionreset"))`. **That would never have
fired.** Measured in the same session, with a control in the same run:

| request | handler fired | outcome |
|---|---|---|
| `page.goto(url)` — *control* | **1×** | intercepted, aborted |
| `page.request.get(url)` | **0×** | **not intercepted** — 200, 14 B served |

`page.route` intercepts requests the *page* makes. `page.request` is an
`APIRequestContext`: its traffic goes through the Node driver, sharing the context's
cookie jar but not its interception. So a route-abort test around our download would have
passed **while proving nothing** — the worst kind of green.

This matters beyond #895: every signed-media download in this codebase uses
`page.request`, so none of them is route-injectable. Fault injection for these paths has
to happen at the socket (as here) or at the seam in a unit test. Worth knowing before
someone writes that test again.

## Scope and limits — what this does NOT show

- Measured on **loopback**. It shows the retry mechanism works on a real socket fault; it does
  not characterise how often `flow-content.google` resets in the wild. That is E1.
- **`ECONNABORTED` is not covered** by the driver, which matches `ECONNRESET` only. Node
  reports `socket hang up` with `code: 'ECONNRESET'`, so 2 of the 3 conditions #895 names are
  covered. Whether the reporter *observed* `ECONNABORTED` or listed it as a likely sibling is
  asked on the issue and still open — if observed, a thin wrap around the **existing**
  `post_with_retry()` is added, with `TimeoutError` excluded by type.
- Arm C's first error line carries no URL, but this spike does **not** establish that
  Playwright's full exception message is URL-free. The council's HIGH finding stands: never
  interpolate `str(exc)` into a Problem Details `detail`.
