# Spike: can gflow observe a generation without a browser page? (#1192)

**Date:** 2026-11-09 · **Profile:** `denon82` (Flow served `flow.google.com`) · **Version:** 0.96.1
(`develop` ca069fca) · **Cost:** $0 (no submit, no upload, no credits, no quota)
· **Scripts:** throwaway, under the gitignored `tmp/`, not committed (see [Reproduce](#reproduce))

## Question

#1192 asks whether the read side of a generation (status, the finished file, the project's
listing, the balance) can run without opening a Flow page, so that `status`, `wait`,
`download` and `reconcile` (#1194, #1195, #1199) can poll cheaply. Its four questions:

1. Does `GET labs.google/fx/api/auth/session` still return an `access_token`?
2. Which `aisandbox-pa` read calls work with only `Authorization: Bearer`, no page, no cookies?
3. Can that token be refreshed without a page?
4. Is there an equivalent page-free read path on `flow.google.com`?

## Method

Three probes, each with its readings written into the script's docstring before it ran:

1. **Session URL.** The existing [`spike_session_oracle.py`](../../../scripts/dev/spike_session_oracle.py)
   (profile arm plus a cookie-less anonymous control).
2. **Token source.** Through gflow's own `FlowApiClient`, load `flow.google.com/` and one
   existing clip's editor route (`/project/<pid>/edit/<mid>`, ids from the local catalog).
   Every request was recorded: host, path, rpcid, status, and the *scheme word* of any
   `Authorization` header. Self-check: the listener did see `cookie` on the app's
   `batchexecute` POSTs, so "no Authorization header" is an observation, not a blind spot.
   Then plain `httpx`, no cookies, against two `aisandbox-pa` reads, with a no-auth and a
   junk-Bearer baseline. Then a replay of the app's own `as29s` request from `httpx`.
3. **No browser at all.** Cookies read from the profile's SQLite jar (`browser_cookie3`), the
   `at` token (`SNlM0e`), `f.sid` (`FdrFJe`) and `bl` (`cfb2h`) scraped from a plain `httpx`
   GET of `flow.google.com/`, and each read rpc built from catalog ids. The `as29s` reply was
   decoded with gflow's own `batchexecute.generation_record`; that decoder needs a
   generation-shaped record, so the `Zzl0ze` and `nzlxg` rows below record the HTTP status
   and what the frame held. Controls on the `as29s` and `nzlxg` calls: a junk `at`, and no
   cookies (`Zzl0ze` got none; see its row). Run against two clips.

## Observed

### Q1: the labs session URL

| URL | profile | anonymous |
|---|---|---|
| `labs.google/fx/api/auth/session` | **410**, JSON `{"error"}` | **410**, JSON `{"error"}` |
| `flow.google.com/fx/api/auth/session` | 200 `text/html` (the app shell) | 200 `text/html` |
| `flow.google.com/api/auth/session` | 200 `text/html` | 200 `text/html` |
| `labs.google/fx/tools/flow` | **308** → `flow.google.com` | — |

Unchanged since [2026-10-22](2026-10-22-labs-session-oracle-gone.md): no URL hands out an
`access_token` on this account.

### Q2/Q3: the SPA's Bearer

On the host Flow served, the app made **zero** requests to `aisandbox-pa.googleapis.com`
across the project list and a clip route. No request carried `Authorization: Bearer`, and no
response body, script or the shell contained `ya29.`. Hosts contacted: `flow.google.com`,
`flow-content.google`, `storage.googleapis.com`, `accounts.google.com`, and Google's font,
image, analytics and ads hosts.

| `aisandbox-pa` call (no cookies) | no auth | junk Bearer | real Bearer |
|---|---|---|---|
| `POST /v1/video:batchCheckAsyncVideoGenerationStatus` | 401 | 401 | **no token obtainable** |
| `GET /v1/credits` | 401 | 401 | **no token obtainable** |

`uploadImage` is a write and was not exercised. Token refresh (Q3) cannot be measured
without a token.

### Q4: `flow.google.com` reads without a page

All calls are `POST /_/AiSandboxAngularFrontend/data/batchexecute`, cookies from the profile
jar, `at` scraped from the shell. No browser launched in this probe.

| rpc | argument (`f.req` inner JSON) | result | junk `at` | no cookies |
|---|---|---|---|---|
| `as29s` (clip status) | `["<workflow_id>"]` | **200**, a generation record with status, signed video URL and `size_bytes` (2/2 clips) | 400 | 401 |
| `as29s` | `["<media_id>"]` | 200, **no** frame: the argument is the workflow id | — | — |
| `Zzl0ze` (project listing) | `["projects/<project_id>",null,null,null,[1]]` | **200**, lists the catalog's clip | not run | not run |
| `nzlxg` (credits) | `[]` | **200** | 400 | 401 |

| download | result |
|---|---|
| `GET` the signed `flow-content.google/video/<id>?Expires=…&Signature=…`, **no cookies**, `Range: bytes=0-31` | **206**, `ftyp` box |
| same, with cookies | 206, `ftyp` |

The signed URL carried an expiry about 6 hours out. The probe-2 replay of the app's own
`as29s` (positive control: the app's request returned 200 on the same clip) agreed: cookies
plus `at` 200, no `at` 400, no cookies 401.

## Verdict

| Path | Verdict |
|---|---|
| **A. `aisandbox-pa` REST with the SPA Bearer** | **No-go on the host Flow served.** No token is obtainable: the labs session URL answers 410 and the `flow.google.com` app holds no Bearer. Whether a labs-served account still could is **not observable here** (labs answers 308 on every local profile). |
| **B. `flow.google.com` `batchexecute` reads, no page** | **Go**, for status (`as29s`), listing (`Zzl0ze`), balance (`nzlxg`), and a ranged read of the signed URL with no cookie or header (bytes 0-31 only; a full download was not measured). The signed URL is itself a bearer credential until it expires: never log or share it. Credentials: the profile's Google cookies plus an `at` token re-read from the shell by a cookie-only GET, so "refresh" is one GET, no page. |

Submitting still needs the page (reCAPTCHA Enterprise, unchanged). Uploads were not
measured; they keep the browser path.

## What this does not establish

- **One account, one night.** N = 1 profile, 2 clips, 3 probe runs that agree. Not a
  stability measurement.
- **`SNlM0e` was missing from some shells.** It was scrapeable on 2026-09-03, absent from
  the shell on 2026-09-12 and 2026-09-16 (session notes; that write-up was never committed
  to this repo), and present again today. Spike #1221 (2026-11-10) later found that every
  signed-in shell carries it, and that a shell without it is the signed-out page (about
  39.7 KB vs 42.5 KB). So a missing token means the cookies were not accepted, not that the
  token flaps. The September absences can't be re-checked, but they fit that explanation. A
  page-free reader should treat a missing `at` as an auth problem and fall back to the
  browser path, never fail closed.
- **A clip that is still running** was not read page-free (that needs a submit). The
  `as29s` record's status code for a finished clip is `3`; the in-progress codes come from
  the existing in-run decoder, not from this probe.
- **Cookie lifetime without a browser.** Google rotates some session cookies only when a
  browser runs. How long a disk-read jar keeps answering 200 is unmeasured, and on Windows
  the jar can be locked while that profile's Chrome is open. gflow's
  `get_chrome_cookie_snapshot` does not solve that case: its Playwright fallback launches
  its own persistent context on the same profile, which fails while Chrome holds it, and it
  returns only `labs.google` cookies, not the Google cookies this probe sent to
  `flow.google.com`.
- **Linux / macOS.** Measured on Windows only.

## Reproduce

The scripts were throwaway and stay uncommitted: each one reads ids from the local catalog
and holds real cookies in memory. To re-run, write a probe that follows the Method above.
Keep the controls (junk `at`, no cookies), and never print a cookie, `at`, id or signed URL.
