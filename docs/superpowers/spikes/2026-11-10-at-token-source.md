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

The pre-registered reading is in the script's docstring, which marks the rows and probes added
between runs. Each run has three steps:

1. **httpx, before this run's browser.** This reads the profile's on-disk cookie jar. It is not
   a never-browsed state: earlier runs had already rotated it. The step GETs `/` and
   `/project/<pid>` three ways: no cookies, cookies with a minimal user agent, and cookies with
   Chrome's navigation headers. Each GET records:
   - whether `SNlM0e` is present;
   - the `WIZ_global_data` key names (never values);
   - `inline_bytes`, the same measure #1219 kept for its 07:30 shells.

   Then it reads `nzlxg` with the scraped `at`, with a junk `at`, with an empty `at`, and with
   no cookies.
2. **The page.** Gflow's own `FlowApiClient` (headed, holding the profile lease) loads `/`, then
   `/project/<pid>`, and waits 15 s on each. A shared sequence counter stamps every
   `batchexecute` request and every text response as it happens. `WIZ_global_data` is
   snapshotted at client entry and after each load. For each distinct `at` the page sent, the
   script checks:
   - every WIZ key;
   - every cookie;
   - every text response body, and whether it arrived before the first request that used the
     value.
3. **httpx again, after the browser closed.** Chrome rotates cookies on load
   (`accounts.google.com/RotateCookiesPage` is among the page's documents). This step also sends
   a browser-minted `at` from httpx.

There were three runs on the same profile within an hour. Run 1 used a first version of the
script. Run 2 added the per-load mapping and the before-browser pass. Run 3 added the
entry-time snapshot, the body/query split and `inline_bytes`. The tables below are from run 3;
runs 1 and 2 agreed on every shared measure.

## Observed

### The page (run 3)

| Measure | Value |
|---|---|
| `batchexecute` calls the page made (18 rpc ids, including `nzlxg`, `Zzl0ze`) | 29 |
| calls carrying `at` in the form body / in the query string | 29 / 0 |
| distinct `at` values | 3, each 42 characters, shape `w28:d13` |
| calls whose `at` equals the `SNlM0e` of the newest `flow.google.com` document before it | 28 / 29 |
| the remaining call: its `at` equals the `SNlM0e` of the page loaded at client entry, before the listeners attached | 1 / 1 |
| flow.google.com documents carrying `SNlM0e` | 2 / 2, plus the entry page |
| cookies equal to or containing any `at` value | 0 |
| other responses carrying an `at` value | 0 (each carrier is a flow.google.com document that arrived before first use) |

Each full document load brings a new `at`, used by the calls after it. This explains run 1's
observation that httpx's shell carried an `at` different from the page's: the token is minted
per document, not drawn from a different source.

### Plain httpx, no browser (run 3, both passes identical)

| GET (`/` and `/project/<pid>`) | status | `SNlM0e` | WIZ keys | `inline_bytes` |
|---|---|---|---|---|
| no cookies | 200 | absent | 47 | 39,752 to 39,798 |
| cookies, minimal UA | 200 | present | 52 | 42,503 to 42,813 |
| cookies, Chrome nav headers | 200 | present | 52 | 42,505 to 42,813 |
| *#1219 at 07:30, cookie arm* | 200 | *absent* | *not recorded* | ***39,716*** |
| *#1219 at 07:30, no-cookie arm* | 200 | *absent* | *not recorded* | *39,668* |

The five keys only a cookie-carrying shell has: `SNlM0e`, `W3Yyqf`, `WZsZ1e`, `oPEP7c`, `qDCSke`.

| `nzlxg` read from httpx | result |
|---|---|
| cookies + `at` scraped by httpx | **200**, 1 frame, balance parsed |
| cookies + an `at` the browser minted | **200**, 1 frame, balance parsed |
| cookies + empty `at` | 400 |
| cookies + junk `at` (control) | 400 |
| no cookies (control) | 401 |

## Verdict against the pre-registered reading

- **First row: found, and reachable without a browser.** The page's `at` is the `SNlM0e` of the
  document it loaded, 29 of 29 calls with the entry-time page counted. A cookie-only httpx GET
  gets a working value from the same route, `nzlxg` answers 200, and the controls behave. The
  value is required: an empty or junk `at` gets 400. No cookie, other response or runtime-only
  key is the source.
- **The "did not reproduce" row also applies:** no run saw a signed-in shell without
  `SNlM0e`.
- **The 07:30 "absent" shell was an anonymous shell.** Its size, 39,716 bytes, sits in today's
  anonymous range (39,752 to 39,798) and about 2.8 KB below every signed-in shell (42,503 to
  42,813). Its own no-cookie arm differed from it by 48 bytes. The cookies the bundle spike
  sent were not accepted at that moment, so the server served the signed-out shell, which never
  carries `SNlM0e`. The token had not moved. The browser at 07:33 had a session, and it rotates
  cookies as it loads.
- **So the answer to #1221:** the page gets `at` only from `SNlM0e` in the document it loads.
  When a cookie-only GET finds no `SNlM0e`, the cookie jar was not accepted, and the token
  source has not changed. A direct reader should treat that as "session not accepted": fall back
  to the browser, which can rotate cookies, and never fail closed.

## What this does not establish

- **Why the jar was not accepted at 07:30.** Expiry, a pending rotation, or something else: not
  measured. #1222 (S2, token and session lifetime) is the place to measure it.
- **The size comparison crosses builds.** #1219 recorded build `20261108.05_p0`; today's build
  was not recorded. The signed-in/anonymous gap is about 2.8 KB, against a 48-byte spread
  between arms, but a build that reshapes the shell would move both.
- **How often a disk jar goes unaccepted.** One sighting, by another script.
- **One profile, one host, Windows only, one afternoon.**

## Wire table

`docs/FLOW_WIRE_RPCS.md` is not on `develop` yet (PR #1219). Once it lands, its `at` row can cite
this spike: "`SNlM0e` in each served document (one per load); a cookie-only GET gets it; a
shell without it is the signed-out shell (WIZ keys 47 vs 52)."

## Reproduce

```bash
uv run python scripts/dev/spike_at_token_source.py denon82
```

Take the `browser-session` lease first if other agents are registered. The raw JSON goes to
`scripts/dev/_spike_out/at_token_source.json` (gitignored). It holds no token, cookie or id:
values appear only as lengths, shapes and booleans.
