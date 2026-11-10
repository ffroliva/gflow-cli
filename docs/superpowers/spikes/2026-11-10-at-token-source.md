# Spike: where does the page get its `at` token when `SNlM0e` is missing? (#1221)

**Date:** 2026-11-10 · **Profile:** `denon82` (Flow served `flow.google.com`) · **Base:** `develop`
ca069fca · **Cost:** $0 (no submit, upload, create or delete; reads only)
· **Script:** [`scripts/dev/spike_at_token_source.py`](../../../scripts/dev/spike_at_token_source.py)
· **Part of:** #1229 (S1)

## Question

The #1217 bundle spike (PR #1219) fetched the `flow.google.com` shell with the profile's cookies
at 07:30 and found no `SNlM0e`. A browser on the same profile then made authenticated
`batchexecute` calls at 07:33. What `at` does the page send, where does it come from, and can a
plain HTTP client get it the same way?

## Method

The pre-registered reading is in the script's docstring. Each run has three steps:

1. **httpx, before any browser.** This reads the profile's on-disk cookie jar as a page-free
   reader would find it. It GETs `/` and `/project/<pid>` three ways: no cookies, cookies with
   a minimal user agent, and cookies with Chrome's navigation headers. Each GET records
   whether `SNlM0e` is there and lists the `WIZ_global_data` key names (never values). Then it
   reads `nzlxg` with the scraped `at`, with a junk `at`, with an empty `at`, and with no
   cookies.
2. **The page.** Gflow's own `FlowApiClient` (headed, holding the profile lease) loads `/`, then
   `/project/<pid>`, and waits 15 s on each. A shared sequence counter stamps every
   `batchexecute` request and every text response as it happens. For each distinct `at` the
   page sent, the script checks:
   - every `WIZ_global_data` key (snapshot after each load);
   - every cookie;
   - every text response body, and whether it arrived before the first request that used the
     value.
3. **httpx again, after the browser closed.** Chrome rotates cookies on load
   (`accounts.google.com/RotateCookiesPage` appears among the page's documents). This step also
   sends a browser-minted `at` from httpx.

There were two runs: a first version of the script, then the version committed here. The second
adds the per-load mapping and the before-browser pass. The readings are the same, except that
the "did not reproduce" row was added before run 2.

## Observed

### The page (run 2)

| Measure | Value |
|---|---|
| `batchexecute` calls the page made (18 rpc ids, including `nzlxg`, `Zzl0ze`) | 29 |
| calls carrying `at` (all in the form body, none in the query) | 29 / 29 |
| distinct `at` values | 3, each 42 characters, shape `w28:d13` |
| calls whose `at` equals the `SNlM0e` of the newest `flow.google.com` document before it | **28 / 29** |
| flow.google.com documents carrying `SNlM0e` | 2 / 2 (root and project) |
| runtime `WIZ_global_data.SNlM0e` present after each load | 2 / 2, equal to the document's |
| cookies equal to or containing any `at` value | 0 |
| other responses carrying an `at` value | 0 (only the two documents, each before first use) |

Each full document load brings a new `at` (one value per load, used by the calls after it). In
run 1 the shell httpx fetched also carried an `at` different from the page's. That is consistent
with a token minted per document, not with a different source.

**One call in 29 is unexplained.** Its value is in no captured body, cookie or WIZ snapshot.
The likely carrier is a document whose body Playwright could not hand over, but this is not
measured.

### Plain httpx, no browser

Both passes (before and after the browser) gave the same results:

| GET | status | `SNlM0e` | WIZ keys |
|---|---|---|---|
| `/` and `/project/<pid>`, no cookies | 200 | absent | 47 |
| the same, cookies + minimal UA | 200 | present, `w28:d13` | 52 |
| the same, cookies + Chrome nav headers | 200 | present, `w28:d13` | 52 |

The five keys only a cookie-carrying shell has: `SNlM0e`, `W3Yyqf`, `WZsZ1e`, `oPEP7c`, `qDCSke`.

| `nzlxg` read from httpx | result |
|---|---|
| cookies + `at` scraped by httpx | **200**, 1 frame, balance parsed |
| cookies + an `at` the browser minted | **200**, 1 frame, balance parsed |
| cookies + empty `at` | 400 |
| cookies + junk `at` (control) | 400 |
| no cookies (control) | 401 |

## Verdict against the pre-registered reading

- **"Every shell this run sees carries `SNlM0e`" applies, so #1219's condition did not
  reproduce.** The shell carried `SNlM0e` in 10 of 10 cookie-carrying httpx GETs (2 in run 1,
  8 in run 2) and 4 of 4 browser documents across two runs. Where the page gets `at` when the shell has none is
  therefore **still unmeasured**.
- **While the shell carries it, the first row holds.** The page's `at` is the `SNlM0e` in the
  document it loaded (28 of 29 calls). A plain httpx client gets a working value from the same
  route without a browser, and the controls behave as expected. The value is needed: an empty
  or junk `at` gets 400. No cookie, other response or runtime-only key is the source.
- **The issue's premise mixed two clients.** The 07:30 "absent" came from httpx and the 07:33
  calls from a browser. This spike found no case where the *browser's* document lacked
  `SNlM0e`.

## What this does not establish

- **The absent state itself.** Not seen in either run. #1219's 07:30 shell recorded no WIZ key
  count, so it cannot be classified after the fact. The likely reading is an anonymous shell
  (the jar was not accepted at that moment), but that is a hypothesis, not a finding.
  **What would settle it:** the next time a cookie GET lacks `SNlM0e`, record its WIZ key
  count. With 47 keys and none of the five cookie-only keys, it is an anonymous shell, so the
  cookies, not the token, are what failed. With 52 keys minus `SNlM0e`, the token moved;
  re-run this script then, and its per-value table names the new carrier.
- **One call in 29** whose `at` had no captured carrier (see above).
- **Token lifetime** across loads and hours: that is #1222 (S2).
- **One profile, one host, Windows only, one afternoon.**

## Wire table

`docs/FLOW_WIRE_RPCS.md` is not on `develop` yet (PR #1219). Once it lands, its `at` row can cite
this spike: "`SNlM0e` in each served document (per load); scrapeable by a cookie-only GET; the
absent state has not been observed since 2026-11-10 07:30."

## Reproduce

```bash
uv run python scripts/dev/spike_at_token_source.py denon82
```

Take the `browser-session` lease first if other agents are registered. The raw JSON goes to
`scripts/dev/_spike_out/at_token_source.json` (gitignored). It holds no token, cookie or id:
values appear only as lengths, shapes and booleans.
