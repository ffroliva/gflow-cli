# Cookie jar readability while Chrome runs, and the cookies the wire needs

**Date:** 2026-11-10 · **Refs:** #1229 (page-free reads wave) · **Cost:** $0 (navigation, shell
GETs and `nzlxg` balance reads only) · **Profile:** `denon82`, served flow.google.com ·
**Script:** [`scripts/dev/spike_cookie_jar_wire.py`](../../../scripts/dev/spike_cookie_jar_wire.py)
(its docstring holds the readings below; it was committed before run 1).

## Question

A planned `WireReader` reads the balance with no browser. It takes cookies from the profile's
Chrome SQLite jar, scrapes `at` (`SNlM0e`) from a cookie GET of `https://flow.google.com/`, and
POSTs `nzlxg` to batchexecute. This spike asked five things:

- **Q1** Which cookies does the jar hold for each domain? Which ones does the wire actually
  need?
- **Q2** Can the jar be read while Chrome runs on the profile?
- **Q3** Does the jar change after Chrome closes, and does the wire still work afterwards?
- **Q4** After Chrome rotates cookies, does a snapshot taken before Chrome opened still work?
  Does the browser session survive the wire reads?
- **Q5** Which encryption prefixes do the cookie values carry?

## Pre-registered readings

| Observation | Reading |
|---|---|
| Q1 baseline GET has `SNlM0e`, `nzlxg` 200 + parsed | the jar alone is enough for a page-free read |
| Q1 baseline GET has no `SNlM0e` | jar not accepted today: Q1 unmeasured, no ablation |
| no-cookie control has `SNlM0e`, or no-cookie POST is 200 | controls fail: Q1 settles nothing |
| junk-`at` control is 200 | `at` is not checked |
| full set fails at the end of ablation | session changed under the spike: ablation void |
| elimination ends with set S and S passes alone | S is *a* minimal set (order-dependent), not *the* minimal set |
| Q2 every disk probe fails with a sharing violation | Chrome holds the jar exclusively |
| Q2 sqlite `immutable=1` or copy succeeds | jar readable concurrently |
| Q2 browser_cookie3 "Unable to read database file" | a lock, not DPAPI |
| Q3/Q4 stale snapshot passes | rotation in a browser run does not invalidate the old jar (today) |
| Q3/Q4 stale fails, fresh passes | the reader must re-read the jar after any browser run |
| Q5 any `v20` | app-bound encryption present |

## Method

The run held the profile lease for every step, and the agentctl `browser-session` lease covered
the whole run. Each trial was a fresh `httpx.Client` with an explicit `Cookie` header and
`follow_redirects=False`: 1 GET of `/`, then at most 1 POST of `nzlxg` with the shell's `at`.
Response cookies were never fed back, so a pass cannot come from a cookie the server re-minted.
Requests were spaced at least 1.1 s apart. The run used 34 trials in total, 31 of them in Q1,
and stayed under the cap of 40.

The run went in four steps:

1. **Q1 + Q5, with no Chrome.** Read the jar with browser_cookie3, then read the sqlite
   metadata. Run a baseline trial with every cookie a browser sends to `flow.google.com` (domain
   `.google.com`, `flow.google.com` or `.flow.google.com`, path `/`). Run three controls. Then
   eliminate backward in name order: drop a name, and keep it dropped if the read still passes.
   Finally, run the final set alone and the full set again.
2. **Q2.** Open Chrome through `FlowApiClient`, go to `https://flow.google.com/`, wait for
   network idle, then idle 20 s more. With Chrome still open, probe the jar file directly (each
   probe listed in the Q2 table). Then run one wire trial.
3. **Q3/Q4, after Chrome closes.** Diff the sqlite metadata and the value hashes. Values were
   hashed in memory and never printed. Run a wire trial with the pre-Chrome snapshot, then one
   with a fresh read of the jar.
4. **Session survival.** Run `gflow auth status --profile denon82` and
   `gflow credits user --profile denon82 --json`. The second is a browser-backed read.

The run printed nothing secret: only names, domains, counts, statuses and booleans.

## Observed

### Q5: encryption, and whether the jar reads with no Chrome

| Measure | Value |
|---|---|
| Google-domain rows by prefix | `v10`: 62, `v20`: 0, other: 0 |
| browser_cookie3 read (no Chrome) | ok, 75 cookies |
| Google-domain cookies with an empty value after decrypt | 0 of 62 |
| Environment | not elevated; `shadowcopy` importable |

### Q1: inventory (names @ domain, all path `/`)

| Group | Count | Names |
|---|---|---|
| `.google.com` | 21 | AEC, APISID, HSID, NID, SAPISID, SEARCH_SAMESITE, SID, SIDCC, SSID, `__Secure-1PAPISID`, `__Secure-1PSID`, `__Secure-1PSIDCC`, `__Secure-1PSIDRTS`, `__Secure-1PSIDTS`, `__Secure-3PAPISID`, `__Secure-3PSID`, `__Secure-3PSIDCC`, `__Secure-3PSIDRTS`, `__Secure-3PSIDTS`, `__Secure-ENID` |
| `accounts.google.com` / `myaccount.google.com` | 7 | ACCOUNT_CHOOSER, LSID, `__Host-1PLSID`, `__Host-3PLSID`, `__Host-GAPS`, `__Host-GAPSTS` / OSID, `__Secure-OSID` |
| `flow.google.com` (+ `.flow.google.com`) | 6 | OSID, `__Secure-OSID`, `_ga`, `_ga_TEFY0PFXC5`, `_ga_X2GNH8R5NS` (×2) |
| `labs.google` | 8 | EMAIL, email, `__Host-next-auth.csrf-token`, `__Secure-next-auth.callback-url`, `__Secure-next-auth.session-token`, `_ga`, `_ga_TEFY0PFXC5`, `_ga_X2GNH8R5NS` |

A browser sends 26 cookies (25 names) to `flow.google.com`. None of the Google-domain cookies
has a path other than `/`.

### Q1: baseline, controls and minimal set

| Trial | GET | `SNlM0e` | POST `nzlxg` | Balance parsed |
|---|---|---|---|---|
| full set, start | 200 | yes | 200 | yes |
| control: no cookies | 200 (161,690 B shell) | **no** | 401 (sent with the good `at`) | no |
| control: junk `at`, full cookies | — | — | **400** | no |
| minimal set alone | 200 | yes | 200 | yes |
| full set, end | 200 | yes | 200 | yes (ablation not void) |

Elimination outcomes. Only 4 names were needed; every other name could be dropped:

| Dropped | Result without it |
|---|---|
| `OSID` (flow.google.com) | **302 to accounts.google.com**, no shell |
| `SID` | 200 signed-out shell (no `SNlM0e`) |
| `__Secure-1PSIDTS` | 200 signed-out shell |
| `__Secure-3PSID` | 200 signed-out shell |
| each of the other 21 names | read still passed |

**Minimal set found: `OSID` (flow.google.com), `SID`, `__Secure-1PSIDTS`, `__Secure-3PSID`.**

Set-Cookie names on the responses:

- **Full-set reads:** `SIDCC`, `__Secure-1PSIDCC`, `__Secure-3PSIDCC`, on both the GET and the
  POST.
- **Minimal-set reads:** the server also sent `NID`, `__Secure-1PSID`, `__Secure-3PSIDTS` and
  `__Secure-OSID`. It re-minted cookies the request lacked.
- **No-cookie arm:** `NID` only.

### Q2: with Chrome running on the profile

| Probe | Result |
|---|---|
| `open(Cookies, 'rb')` | `PermissionError` errno 13 |
| sqlite `mode=ro` | `OperationalError: unable to open database file` |
| sqlite `mode=ro&immutable=1` | `OperationalError: unable to open database file` |
| `shutil.copy2(Cookies)` | `PermissionError` **WinError 32** (sharing violation) |
| `shutil.copy2(Cookies-journal)` | `PermissionError` WinError 32 |
| `Cookies-wal` | does not exist |
| browser_cookie3, thread | **`RequiresAdminError`: "This operation requires admin."** |
| browser_cookie3, separate process | same `RequiresAdminError` |
| Playwright `context.cookies()` sendable to flow.google.com | 26 (same count as on disk) |
| wire trial, using the pre-open in-memory snapshot (the disk was unreadable) | GET 200 + `SNlM0e`, `nzlxg` 200, parsed |

browser_cookie3 tries every connection method in turn: read-only, `nolock`, `immutable`, a
legacy copy, then a shadow copy. Its own read-only and copy methods fail on the lock. It then
reaches its shadow-copy (VSS) method, which needs admin rights. So the error it raises names
admin, but the underlying cause is the lock.

### Q3 / Q4: after Chrome closed (run 1, contaminated)

> **Run 1's Q3/Q4 is contaminated.** Another agent's `e2e_auth` suite was running on `denon82`
> while this spike ran. The profile lease stops two Chrome sessions from overlapping. It does
> not stop a foreign session from running in the hand-over gaps between this script's phases:
>
> - **Q1 → Q2 gap:** run 1 recorded no timestamp for it.
> - **Q2 → Q3 gap:** about 3 s.
>
> Run 1 cannot rule out a foreign session in either gap. Its table is kept as a record, but
> the verdict rests on run 2 below.

| Measure | Value |
|---|---|
| Google-domain rows before / after | 62 / 62, none added or removed |
| `expires` and `last_update` changed | SIDCC, `__Secure-1PSIDCC`, `__Secure-3PSIDCC`, `__Secure-1PSIDRTS`, `__Secure-3PSIDRTS`, `__Secure-1PSIDTS`, `__Secure-3PSIDTS`, `__Host-GAPSTS` (accounts), `_ga`, `_ga_X2GNH8R5NS` (.flow.google.com) |
| `last_access` changed | 40 rows |
| values changed (sendable set, hashed) | SIDCC, `__Secure-1PSIDCC`, `__Secure-3PSIDCC`, `__Secure-1PSIDRTS`, `__Secure-3PSIDRTS`, `__Secure-1PSIDTS`, `__Secure-3PSIDTS`, `_ga_X2GNH8R5NS` |
| wire with the **stale** pre-Chrome snapshot | GET 200 + `SNlM0e`, `nzlxg` 200, parsed |
| wire with a fresh jar read | GET 200 + `SNlM0e`, `nzlxg` 200, parsed |
| `gflow auth status --profile denon82` | exit 0, "Flow session verified" (the migrated-host cookie fallback) |
| `gflow credits user --profile denon82 --json` (browser) | exit 0, `authenticated: true`, integer balance |

### Q3 / Q4: run 2 (`--q34`, held the profile on its own)

Run 2 held the agentctl `browser-session` lease from start to finish. The other agent had
released `denon82` and was doing offline work only. Run 2 recorded the timeline below (UTC):

| Step | Time |
|---|---|
| lease 1 acquired: snapshot + baseline read (passed: GET 200, POST 200) | 12:38:54 |
| lease 1 released | 12:39:00 |
| Chrome on `https://flow.google.com/` (via `FlowApiClient`, its own lease) | 12:39:12 |
| Chrome closed | 12:40:26 |
| lease 2 acquired: metadata diff, stale and fresh reads | 12:40:29 |
| lease 2 released | 12:40:34 |

The hand-over gaps were 12 s (spent on `FlowApiClient` start-up) and 3 s. No other agent held
the browser session during either gap.

| Measure | Run 2 |
|---|---|
| Q2 disk probes with Chrome open | same as run 1: errno 13, sqlite cannot open, WinError 32 on copy, `RequiresAdminError` |
| Google-domain rows before / after | 62 / 62, none added or removed |
| `expires` / `last_update` changed | the same 10 rows as run 1 |
| values changed (sendable set) | the same 8 names as run 1: SIDCC, `__Secure-1PSIDCC`, `__Secure-3PSIDCC`, `__Secure-1PSIDRTS`, `__Secure-3PSIDRTS`, `__Secure-1PSIDTS`, `__Secure-3PSIDTS`, `_ga_X2GNH8R5NS` |
| wire with the **stale** pre-Chrome snapshot | GET 200 + `SNlM0e`, `nzlxg` 200, parsed |
| wire with a fresh jar read | GET 200 + `SNlM0e`, `nzlxg` 200, parsed |
| `gflow auth status --profile denon82` | exit 0 |
| `gflow credits user --profile denon82 --json`, 3 runs in a row | **runs 1–2: exit 1, `UnexpectedError`** (stderr not captured); run 3: exit 0, `authenticated: true`, integer balance. Run 3's stderr shows only teardown warnings (`browser_teardown.context_close_error`, `TimeoutError`, `I/O operation on closed pipe`) |

### Follow-up: the two `UnexpectedError` reads

| Check | Result |
|---|---|
| Recorded evidence of runs 1–2 | none: no incident directory and no `operations` row near 12:41–12:42 |
| 3 fresh sequential `credits user` reads (12:45–12:47), full capture | 3/3 exit 0, `authenticated: true`, integer balance; stderr shows only teardown noise (`context_close_error`, `force_close_returned`, `TargetClosedError`) |
| replay of the exact sequence: `--q34` (run 3), then `auth status`, then 3 `credits user` (12:48–12:51) | run 3: stale snapshot accepted, fresh jar accepted, the same 8 values changed; `auth status` exit 0; reads 3/3 exit 0, `authenticated: true` |

All runs on this machine since the spike started:

- **Overall:** 2 failures in 10 browser-backed reads. Both were the first two reads after run 2.
- **Since then:** the last 7 reads all passed, including the 3 that replayed the exact
  sequence.

The failure did not reproduce (0/3 on replay). Per this spike's own pre-registered reading,
that settles nothing. Nothing shows that the stale-cookie wire read caused the failure, but
nothing rules it out either. The `origin/develop` baseline comparison was not run, because no
failure remained to compare against.

**What would settle it:** the next time this appears, capture the `UnexpectedError` traceback
(stderr), its timestamp, and whether a Chrome on the profile was still shutting down at that
moment.

## Verdict

- **Q1.** With no Chrome running, the jar alone gives a working page-free read. browser_cookie3
  decrypts every Google cookie, `SNlM0e` is in the shell, and `nzlxg` returns 200 with a parsed
  balance. The controls behave as expected: no cookies gives a signed-out shell and a 401, and a
  junk `at` gives a 400.
  - **Minimal set:** a set of 4 cookies is sufficient: `OSID` on flow.google.com, plus `SID`,
    `__Secure-1PSIDTS` and `__Secure-3PSID`.
  - **Not the only set:** elimination ran in name order, so a different order could find a
    different set. `__Secure-1PSID` was dropped because `__Secure-3PSID` covered it.
  - **The domain filter needs to change:** the existing `labs.google` filter in `_is_flow_cookie`
    keeps none of these 4 cookies.
- **Q2.** While Chrome runs, the jar cannot be read by any means a normal user has.
  - Chrome holds `Cookies` and `Cookies-journal` exclusively. A plain open, sqlite (including
    `immutable`) and a byte copy all fail with a sharing violation.
  - browser_cookie3 reports `RequiresAdminError` because it ends on its shadow-copy fallback; the
    cause is the lock, not DPAPI.
  - A wire read using cookies taken **before** Chrome opened still worked while Chrome was
    running.
  - **What the reader should do:** treat "Chrome is open on this profile" as a "cookie store
    unreadable" failure and fall back. Do not try to work around the lock.
- **Q3.** After Chrome closed, the jar was readable again. Ten timestamp-cookie rows had new
  expiry and update times, and their values changed. The row count did not change. The wire
  still worked with the fresh jar.
- **Q4.** Run 2 reproduced run 1's result with the profile held on its own (run 1 is
  contaminated; see above).
  - **Stale snapshot accepted:** the snapshot taken before Chrome opened still worked after
    Chrome had rotated 7 session cookies (the `*SIDCC`, `*PSIDRTS` and `*PSIDTS` families).
  - **Session survived:** after both runs the browser session was still valid. `auth status`
    passed, and a browser-backed `credits user` read returned `authenticated: true` with a
    balance.
  - **No STOP condition was met.**
  - **One open item:** in run 2, the first two browser reads exited with `UnexpectedError`.
    That failure did not come back on an exact replay (0/3), and the 7 reads after it all
    passed. The session was never lost. The cause is unmeasured, so this is an open lead (see
    the follow-up above), not a finding.
- **Q5.** All 62 Google-domain values are `v10`, and none are `v20`. App-bound encryption is
  not present on this profile today.
- **Rotation on wire reads.** Every wire response sets `SIDCC`, `__Secure-1PSIDCC` and
  `__Secure-3PSIDCC`. A reader that drops these cookies does not break the next read; every
  later read in this run passed with the original values. A reader that sends only the minimal
  set also gets the missing session cookies re-minted, which a cookie-dropping reader then
  discards.

## What this does not establish

- **Single snapshot in time.** This was one profile, on one run, on one Chrome build. The
  following are unmeasured:
  - How long a stale snapshot stays valid, beyond this ~2-minute window.
  - Whether dropping the `*SIDCC` re-mints over many reads eventually makes Google ask for a
    `RotateCookies` round trip.
- **Other minimal sets.** Any other elimination order is untested.
- **App-bound encryption (`v20`).** It is absent here. A profile written by a Chrome build that
  uses it would fail decryption, and this run does not show how that fails.
- **Admin rights.** This run was not elevated. An elevated process might read the jar through
  shadow copy while Chrome runs. That route is not a design option, but it is untested.
- **Shell size.** The full GET body was about 165 KB signed in and about 162 KB signed out.
  Those figures are whole-document bytes, not the inline-script bytes recorded as "~42.5 KB" in
  earlier spikes, so the two numbers do not compare.
- **The `auth status` pass.** It came through the migrated-host cookie fallback, which on its
  own proves little. The browser-backed `credits user` read is the evidence that the session
  survived.
- **Teardown error.** `credits user` ended with an unretrieved `TargetClosedError` future at
  teardown. That is the browser-close path (#1233), not this spike.
