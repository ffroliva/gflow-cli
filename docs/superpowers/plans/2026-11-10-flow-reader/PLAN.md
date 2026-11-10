# Page-free Credits Read Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature flow-reader` to find the next unchecked
> task. One ticket = one worktree from `origin/develop` = one PR. TDD: red first, watched red for
> the right reason. Run `/gflow:check` before every commit. Never merge. No AI attribution.

**Goal:** `gflow credits user` / `credits list` and MCP `gflow_get_credits` read the balance
without starting Chrome whenever the profile's cookies are accepted, and fall back to today's
browser read whenever they are not.

**Architecture:**
- A new `api/flow_wire.py` (plain functions, one `WireReadUnavailable(reason)` exception) holds
  the direct path: a cookie GET of the shell for `at`/`f.sid`/`bl`, then one batchexecute POST for
  `nzlxg`, decoded by the existing `CreditsInfo.from_nzlxg`.
- `services/credits.py::_fetch_once` calls it **before** any `FlowApiClient` exists. On
  `WireReadUnavailable` it runs today's block unchanged.
- The cookie read reuses `auth/cookies.py`'s fast path with a domain parameter and **no**
  Playwright fallback.
- Nothing in `api/transports/` or the generation port changes.

**Predict verdict:** CAUTION, 4.6/10 ([PREDICT.md](PREDICT.md)). Mitigations are folded into the
tasks below. Scenarios: [SCENARIO.md](SCENARIO.md).

**Gate before Ticket A starts:** the cookie spike's E2 (rotation conflict) shows **no session
loss**, and #1140 is in review. If E1 shows the jar is unreadable while Chrome runs, that is
expected (fallback row 5), not a blocker.

**Risk register:**

| Severity | Risk | Mitigation |
|---|---|---|
| Critical | The reader is placed inside `FlowApiClient`, so Chrome still launches (G2 fails) | the service-level call, plus a sentinel test that `FlowApiClient` is never constructed on the healthy path |
| Critical | A secret leaks via logs, errors or problem details | redaction widened and tested with fake values; the reason is an enum; no body text or `str(exc)` |
| Critical | Copied cookies vs Chrome's rotation logs the account out | spike E2 gate; e2e G9 (session valid after N wire reads); kill switch |
| High | Cookie store locked or undecryptable → every read pays wire + page | bounded wire attempt; immediate fallback; the fallback rate is logged and visible in doctor |
| High | macOS Keychain prompt on every read | the wire defaults off on darwin |
| High | Docs and the MCP description keep saying "opens the browser" | the claim sweep in Ticket C |
| Medium | Flow renames `nzlxg` or changes its shape | fallback `unparseable`; the nightly RPC-DRIFT canary (#1218, PR #1230) |

---

## File structure

### New files
```
src/gflow_cli/api/flow_wire.py
  direct read: shell GET (at, f.sid, bl) + nzlxg POST; reasons enum; allow-list; budget; single-flight at
tests/api/test_flow_wire.py
  one test per SCENARIO row 1–8, 13, 16, 20, 23, 26 (httpx.MockTransport, fake cookies)
tests/services/test_credits_wire_fallback.py
  rows 10, 11, 14, 15, 17, 18, 28 (sentinel FlowApiClient)
tests/features/credits_wire.feature + tests/features/test_credits_wire_steps.py
  offline Scenario Outline over the G3 reasons
tests/features/credits_wire_live.feature + tests/e2e/test_credits_wire_bdd.py
  @e2e @e2e_auth: healthy read (no Chrome, request count), cookie-free fallback, session survives
```

### Modified files
```
src/gflow_cli/auth/cookies.py      domain-parameterised fast read; CookieStoreUnreadable; no Playwright fallback on this path
src/gflow_cli/data/redaction.py    +__Secure-1PAPISID/3PAPISID, SIDCC, NID, AEC, at=, f.sid=
src/gflow_cli/config.py            wire_reads: bool (GFLOW_CLI_WIRE_READS), default on except darwin
src/gflow_cli/services/credits.py  wire first in _fetch_once; read_source / wire_fallback log events
src/gflow_cli/mcp/tools.py         gflow_get_credits description; gflow_auth_status wire-health field (Ticket B)
src/gflow_cli/services/doctor.py   wire-health finding (Ticket B)
src/gflow_cli/cli_credits.py       help text
.env.template, docs/CONFIGURATION.md, docs/MCP.md, docs/USAGE.md, docs/USER_GUIDE.md,
docs/ARCHITECTURE.md, KNOWN_ISSUES.md, skills/gflow-cli/SKILL.md (+ plugin copy), llms.txt,
docs/FLOW_WIRE_RPCS.md (adapter column, once #1219 lands), CHANGELOG.md, website/docs mirror
```

---

## Ticket 0: #1140, Chrome close no longer waits out the 30 s timeout (G4)

In flight (Bug Lane agent, branch `bugfix/chrome-close-hang-1140`).
- [ ] PR open with before/after `credits user` timings and the e2e output
- [ ] two xhigh reviews + one fix round, Sonar 0
- [ ] #1233 comment updated with the re-time

---

## Ticket A: wire credits read with page fallback (G1, G2, G3, G5, G7, G9)

Branch `feature/wire-credits-read`, one PR.

### A1. Red tests (no production code)
- [ ] `tests/api/test_flow_wire.py`, one test per G3 reason:
  - `signed_out_shell` (shell without `SNlM0e`)
  - `http_401`
  - `http_400`
  - `unparseable` (empty envelope, an `er` frame, `from_nzlxg` rejects)
  - `store_unreadable` (locked, DPAPI, v20, `KeyError`, missing file)
  - `network` (connect timeout, read timeout)
  - `redirect` (3xx is not followed)
- [ ] budget and shape:
  - exactly 1 GET + 1 POST
  - the second call in the same process reuses `at` (0 GETs)
  - the rpcid allow-list refuses anything but `nzlxg`
  - headers include `x-same-domain: 1`, origin/referer, and the profile's UA
- [ ] the wire stays off for the process after `signed_out_shell` / `http_401`
- [ ] `Set-Cookie` in a reply is never written anywhere
- [ ] the cookie read is always called with an explicit `cookie_file`, and its domain scope keeps
  only `flow.google.com` / `.google.com` cookies
- [ ] `tests/services/test_credits_wire_fallback.py`:
  - healthy wire → `FlowApiClient` never constructed (sentinel)
  - each reason → the page path ran, `credits.wire_fallback reason=<enum>` logged
  - both fail → the page's typed error surfaces
  - `--refresh` path
  - kill switch → no cookie read
  - darwin default off
  - `SecurityError` for a profile outside GFLOW_CLI_HOME before any read
- [ ] 5 concurrent reads → 1 shell GET (single flight)
- [ ] redaction: fake `__Secure-3PAPISID=`, `SIDCC=`, `NID=`, `AEC=`, `at=…&`, `f.sid=` all
  redacted by `redact_error_detail`
- [ ] watch each red for the right reason; paste the red summary in the PR

### A2. BDD scaffold
- [ ] the offline `credits_wire.feature` Scenario Outline over the 7 reasons, bound in
  `tests/features/`
- [ ] the live `credits_wire_live.feature` (3 scenarios from SCENARIO.md), bound from
  `tests/e2e/test_credits_wire_bdd.py`; `test_e2e_binding_guard.py` stays green

### A3. Cookie read (`auth/cookies.py`)
- [ ] `read_cookies_for(profile_dir, *, host="flow.google.com") -> dict[str, str]`:
  - the `browser_cookie3` fast path with an explicit `cookie_file`
  - RFC 6265 domain match for the host
  - every failure raises `CookieStoreUnreadable`
  - never reaches `_get_chrome_cookies_playwright`
- [ ] the existing `get_chrome_cookie_snapshot` behaviour is unchanged (its tests stay green)

### A4. `api/flow_wire.py`
- [ ] `class WireFallbackReason(StrEnum)` and `class WireReadUnavailable(Exception)` carrying
  only the reason
- [ ] `async def read_credits(profile_dir) -> CreditsInfo`:
  - `to_thread` for the cookie read
  - one `httpx.AsyncClient` (`follow_redirects=False`, connect 3 s / total 5 s)
  - shell GET → scrape `SNlM0e` / `FdrFJe` / `cfb2h`
  - POST `nzlxg`
  - `batchexecute` envelope parse → `CreditsInfo.from_nzlxg`
- [ ] the per-process `at` cache under an `asyncio.Lock`; process-wide disable after
  signed-out/401
- [ ] the UA: the profile's Chrome version string; read how the browser path derives it and reuse
  that, don't hard-code one
- [ ] import time: does not import Playwright (`python -X importtime` check in the PR)

### A5. Redaction (`data/redaction.py`)
- [ ] widen the cookie-name pattern
- [ ] add form-encoded `at=` and `f.sid=` patterns

### A6. Config (`config.py`, `.env.template`, `docs/CONFIGURATION.md`)
- [ ] `wire_reads: bool` ← `GFLOW_CLI_WIRE_READS`, default `True` on win32/linux and `False` on
  darwin
- [ ] the `.env.template` entry next to `GFLOW_CLI_FLOW_HOST`; the website mirror regenerated

### A7. Service (`services/credits.py`)
- [ ] `_fetch_once`: if `wire_reads`, try `flow_wire.read_credits` → `_success`; on
  `WireReadUnavailable` log `credits.wire_fallback` and run the unchanged `FlowApiClient` block
- [ ] log `credits.read source=wire|page`
- [ ] remove the stale "#795 … no fast path left" comment

### A8. CLI + MCP surface
- [ ] `cli_credits.py` help: "reads the balance directly when the saved session is accepted;
  otherwise opens the profile's browser"
- [ ] the `mcp/tools.py` `gflow_get_credits` `description=`, the field agents read (memory
  `mcp-agents-read-description-not-args`)
- [ ] the six mirror axes (`skills/check/SKILL.md` step 1b): no new option, no queued path; record
  that in the PR

### A9. Live proof (Iron Law) on `denon82`, $0
- [ ] `pytest -m e2e_auth tests/e2e/test_credits_wire_bdd.py tests/e2e/test_credits_mcp_e2e.py
  tests/e2e/test_credits_on_flow_bdd.py`, with the output pasted
- [ ] G1: cold `gflow credits user` × 5 (separate processes), p50 and the decrypt / GET / POST /
  parse breakdown pasted
- [ ] G5: the request count from the e2e pasted
- [ ] G9: `gflow auth status` valid after the run, pasted
- [ ] G7: one run with `GFLOW_CLI_WIRE_READS=0` showing `source=page`

### A10. Review
- [ ] `/gflow:check` (ten gates)
- [ ] fresh-agent `/code-review` xhigh + `ponytail-review` xhigh, then one fix round
- [ ] `/gflow:sonar` to zero

---

## Ticket B: wire health in `doctor` and `gflow_auth_status` (G6)

Branch `feature/wire-health`, after A. It reuses `flow_wire`, with no second implementation.
- [ ] red tests:
  - a `doctor` finding with outcomes `accepted` / `signed_out_shell` / `store_unreadable` /
    `disabled`, plus the build label
  - names and counts only (asserted against fake secrets)
- [ ] `services/doctor.py` finding
- [ ] the `cli_doctor.py` docstring becomes "… and, unless disabled, one read-only request to
  flow.google.com"
- [ ] the `gflow_auth_status` result gains `wire: {status, build}`, with the description updated
- [ ] e2e: `gflow doctor --json` and `gflow_auth_status` on `denon82` agree; output pasted
- [ ] `/gflow:check`, two xhigh reviews + fix round, Sonar 0

## Ticket C: docs (G8)

Branch `docs/wire-reads`, after A (it can ride with B if small).
- [ ] the claim sweep (PREDICT.md, UX persona list):
  - `cli_credits.py:73-75`
  - `docs/MCP.md:130`
  - `docs/USAGE.md:135`
  - `docs/USER_GUIDE.md:425`
  - `skills/gflow-cli/SKILL.md:64` + the plugin copy
  - `llms.txt:6`
  - KNOWN_ISSUES #795
  - `docs/ARCHITECTURE.md:100`
- [ ] the ARCHITECTURE.md "Reads without a browser" section: placement, fallback, the reasons
  enum, when a Protocol will be introduced (with #1194)
- [ ] a new KNOWN_ISSUES entry: the risk of copied cookies vs Chrome rotation, the fallback, the
  kill switch, measured status (E2, S2 #1222)
- [ ] `docs/FLOW_WIRE_RPCS.md`: a "Read directly by" column (`nzlxg` → `flow_wire`), once #1219
  is on develop
- [ ] CHANGELOG `[Unreleased]`; regenerate the website docs and plugin skills; doc gates green

---

## Close-out (GOAL R1–R3, after the owner merges A–C and #1140)
- [ ] `/gflow:release`, including its live-verify ledger with the G1 numbers
- [ ] a business-level post draft (local Markdown + HTML, opened locally), with every number
  traced to the ledger or a spike doc
- [ ] session-hardening and the reactivation prompt

## Definition of done
- [ ] G1–G9 each proven by pasted run output in a PR (G2 and G6 as amended in GOAL.md)
- [ ] `/gflow:check` green; Sonar 0 on every PR
- [ ] BDD covers every Critical and High row in SCENARIO.md
- [ ] CHANGELOG and docs updated; no stale "opens the browser" claim
- [ ] no `# TODO` without an issue link
