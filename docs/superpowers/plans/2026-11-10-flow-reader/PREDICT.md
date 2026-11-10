# Predict: page-free reads (FlowReader)

## Verdict: CAUTION
**Confidence:** 4.6/10. Persona average 6.6 (7, 7, 7, 7, 5), minus 2 because the Devil's Advocate
found a simpler path the others converged on. No hard STOP. One conditional STOP is still open
(cookie rotation, below).

## Summary

The wire read is sound and read-only. The proposed **shape** is not. A `FlowReader` Protocol with
`PageReader` and `WireReader` would serve one consumer today. If the reader lives inside
`FlowApiClient`, G2 cannot be met, because the client launches Chrome on entry. Listing and record
reads have no caller that runs without a page yet. The wave narrows to: fix the close hang
(#1140, duplicate #1233), then wire **credits only**, as a function with a fallback at the service
seam. It is gated by $0 experiments on cookie locking, cookie rotation and staleness.

## Persona findings

### Architect: CAUTION (7/10)
- **Chrome launches when the client is entered.** `FlowApiClient.__aenter__` starts Playwright
  (`api/client.py:500-502`). `_enter_setup` then takes the lease (`:770`) and launches Chrome and
  the page pool (`:788-806`). `get_credits` (`:1948`) only checks out a page that already exists.
  A reader inside the client cannot avoid Chrome.
- **The seam already exists.** `services/credits.py::_fetch_once` (`:50-58`) is the one place the
  client is opened. The CLI and MCP `gflow_get_credits` (`mcp/tools.py:1601-1614`) both go
  through it.
- **Recommended shape:** `api/flow_wire.py` with plain async functions and one
  `WireReadUnavailable(reason)` exception. The cookie read reuses `auth/cookies.py` with a domain
  parameter and no Playwright fallback. No Protocol until a second consumer needs to swap readers.

### Security / account risk: CAUTION (7/10)
- **Redaction gaps.** `data/redaction.py:33-35` misses `__Secure-1PAPISID`/`3PAPISID`, `SIDCC`,
  `NID`, `AEC`, the form-encoded `at=` and `f.sid`. structlog has no redaction step
  (`observability.py:101`).
- **Fallback reasons are a fixed enum,** never `str(exc)` or a response slice: `as29s` replies
  carry signed URLs.
- **Cookie scope.** The filter must domain-match `flow.google.com` only. Pass `cookie_file`
  explicitly (`None` reads the user's personal Chrome jar). Run the GFLOW_CLI_HOME boundary check
  first. Keep cookies in memory only. Never write them back.
- **Wire discipline.** An in-code rpcid allow-list (`nzlxg` now). `follow_redirects=False`. No
  retry on 4xx. At most one shell GET per process. The wire stays off for the rest of the process
  after a signed-out shell or a 401. The profile's own Chrome UA.
- **Account risk.** Google may answer a wire read with rotated cookies that Chrome never receives.
  Device Bound Session Credentials could also make copied cookies short-lived. Unverified, so it
  goes to experiment E2.
- **STOP conditions,** none in the proposal: writing to Chrome's cookie store; any rpcid outside
  the allow-list; following redirects with cookies; retrying 401/400; getting around app-bound
  encryption.

### Performance: CAUTION (7/10)
- **G2 requires choosing the reader at the service level,** before any client is built.
- **Cookie decrypt** is synchronous: call it via `asyncio.to_thread`, or it blocks the MCP event
  loop.
- **G1 needs the cold, uncached number.** The spike p50s cached the token.
- **Bound the wire attempt:** connect ≤ 3 s, total ≤ 5 s, no retries.
- **A fallback still pays the full close timeout until #1140 lands,** so #1140 first.
- **Inside a live generation, keep the passive page read.** A wire `as29s` there adds requests
  (against G5).
- **`at` cache:** per process, single flight under an `asyncio.Lock`.

### CLI and MCP UX: CAUTION (7/10)
- **Surfaces:** `gflow credits user` / `credits list` and MCP `gflow_get_credits`, all through
  `services/credits.py`. `ProfileLockedError` (`:74`) must not be masked.
- **G2 named the wrong listing.** `project list` / `data list projects` read the local catalog.
  The real Flow listing callers are `data sync` (CLI-only) and name resolution, which already uses
  `fetch_project_listing` (`api/client.py:3307`).
- **G6 can't be met as written.** `doctor` has no MCP twin (`tests/mcp/test_cli_parity.py:155`,
  #542).
- **Error path.** If both paths fail, raise the page path's error. No new exit code.
- **Kill switch:** `GFLOW_CLI_WIRE_READS` (bool), with `.env.template`, CONFIGURATION.md and the
  website mirror.
- **No `source` field in the JSON;** log `read_source` and `fallback_reason` instead.
- **macOS:** browser_cookie3 asks the Keychain and may prompt on every read. The wire stays off
  on darwin until measured.
- **Claims that become false:**
  - `cli_credits.py:73-75`
  - `mcp/tools.py:1588-1590`
  - `docs/MCP.md:130`
  - `docs/USAGE.md:135`
  - `docs/USER_GUIDE.md:425`
  - `skills/gflow-cli/SKILL.md:64` (and the plugin copy)
  - `llms.txt:6`
  - KNOWN_ISSUES #795 entry
  - `docs/ARCHITECTURE.md:100`
  - the `website/docs` mirror

### Devil's Advocate: CAUTION (5/10)
- **#1233 is most of the credits win.** It takes about 40 s down to about 7 s and fixes every
  browser command. The wire then saves about 6 s more per one-shot read (about 1.1 s total).
  Measure the wire against the fixed baseline.
- **The Protocol is premature,** and listing/record have no consumer: #1194, #1195 and #1199 are
  open with no PR.
- **Cookies only rotate when Chrome loads a page** (KNOWN_ISSUES "Browser session expires"). Every
  read that skips Chrome leaves the jar older, so the fallback rate may rise over time.
- **Experiments that could kill the wire before any code:** cookie lock during a run, the
  staleness curve, a rotation conflict.

## High-confidence risks (flagged by 2+ personas)
1. **Placement.** The reader must be chosen above `FlowApiClient`, at `services/credits.py`, or G2
   fails. (Architect, Performance, UX)
2. **Shape.** No Protocol yet; credits only; listing/record wait for their consumers. (Architect,
   Devil's Advocate, UX)
3. **Locked cookie store** while Chrome runs on the profile: classify it and fall back, never
   through the Playwright cookie fallback. (all five)
4. **Cookie rotation and staleness** without a browser: unmeasured, possibly account-affecting.
   (Security, Devil's Advocate)
5. **Kill switch plus redaction widening** are mandatory. (Security, UX)

## Conflicts resolved
- **Architect "function" vs the proposal's "Protocol":** function now; the Protocol arrives with
  the second consumer (#1194).
- **GOAL G2 / G6 vs UX:**
  - G2 narrows to credits (listing/record move to their consumer tickets).
  - G6 reports wire health in CLI `doctor` and in MCP `gflow_auth_status`, the existing MCP
    health surface, instead of porting all of doctor.
- **Performance "G1 reachable" vs Devil's Advocate "staleness may make it moot":** G1 is measured
  on the cold path, and the fallback rate is logged. The staleness curve (S2 #1222) runs alongside
  and decides whether to keep the wire on by default.

## Required mitigations before EXECUTE
1. **#1140 lands first** (Bug Lane, in progress). The wire is measured against the fixed baseline.
2. **Experiments E1–E3** ($0, cookie spike, in progress):
   - E1: is the jar readable while Chrome holds the profile?
   - E2: rotation conflict. The session must survive wire reads with a pre-rotation jar. **Session
     loss = STOP.**
   - E3: the minimal cookie set, and whether the cookies use v20 app-bound encryption.
3. **Placement:** the wire attempt goes in `services/credits.py::_fetch_once`, before
   `FlowApiClient` is constructed. The lease is taken only on fallback.
4. **The Security list:**
   - redaction patterns, tested with fake values;
   - an enum of reasons;
   - domain scope, an explicit `cookie_file`, the boundary check;
   - the rpcid allow-list;
   - no redirects;
   - the request budget;
   - wire off for the process after signed-out/401;
   - the kill switch;
   - `to_thread` for the decrypt;
   - httpx timeouts.
5. **macOS:** the wire is off by default on darwin until measured.
6. **The claim sweep** listed under UX.
7. **An e2e that confirms the browser session is still valid after a run of wire reads.**

## Recommended next step
Wait for E1–E3 and the #1140 PR. If E2 shows no session loss: `/gflow:scenario` for "wire credits
read with page fallback", then `/gflow:plan`.
