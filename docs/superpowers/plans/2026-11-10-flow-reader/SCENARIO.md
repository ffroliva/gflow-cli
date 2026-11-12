# Scenario: wire credits read with page fallback

Scope after predict ([PREDICT.md](PREDICT.md)):
- `services/credits.py::_fetch_once` tries `api/flow_wire.read_credits(profile_dir)` **before**
  any `FlowApiClient` exists.
- On any `WireReadUnavailable(reason)` it runs today's page path unchanged.
- Surfaces: `gflow credits user`, `gflow credits list` and MCP `gflow_get_credits` (all three go
  through the service), plus wire health in CLI `doctor` and MCP `gflow_auth_status`.

## Coverage map

| Dimension | Active | Why |
|---|---|---|
| D1 Auth & session | **yes** | cookies read from disk, `at` token, signed-out shell, rotation, `--refresh` interplay |
| D2 WAF / reCAPTCHA | **yes** (account risk) | no reCAPTCHA is involved; the request shape and request budget are the risk |
| D3 Selectors | no | no DOM is touched on the wire path; the page path is unchanged |
| D4 Batch & resume | no | a single read, nothing billed, nothing resumed |
| D5 Concurrency | **yes** | MCP concurrent calls, a single-flight `at`, the profile lease only on fallback |
| D6 Data layer | no | no catalog writes; credits are not recorded |
| D7 Errors & exit codes | **yes** | the wire never surfaces; the page path's error wins |
| D8 Cross-platform | **yes** | DPAPI and a locked file on Windows, the macOS Keychain, the Linux basic store |
| D9 Transport | **yes** | envelope and shape drift, redirects, timeouts |
| D10 Headless/headed | no | the wire path launches no browser |
| D11 Input validation | **yes** | the profile path must stay inside the GFLOW_CLI_HOME boundary |
| D12 Observability | **yes** | `read_source` / `fallback_reason` events; redaction of the new secrets |
| D13 MCP parity | **yes** | `gflow_get_credits` (direct, not queued), `gflow_auth_status` health field, tool descriptions |

## Scenario table

Fallback (F) means: log `credits.wire_fallback reason=<enum>`, then run today's page path, which
returns the same result shape as today. Every F row has an offline test. Rows marked **e2e** are
also exercised live.

| # | Dim | Scenario | Sev | Expected behaviour | Test |
|---|---|---|---|---|---|
| 1 | D1 | Shell has no `SNlM0e` (signed-out page; cookies not accepted) | **Critical** | F `signed_out_shell`; the wire stays off for this process | Unit + **e2e** (cookie-free arm) |
| 2 | D9 | batchexecute answers 401 | **Critical** | F `http_401`; no retry; the wire stays off for this process | Unit |
| 3 | D9 | batchexecute answers 400 (stale or bad `at`) | **Critical** | F `http_400`; no retry | Unit |
| 4 | D9 | 200 but no `wrb.fr` frame for `nzlxg`, or `CreditsInfo.from_nzlxg` rejects the shape | **Critical** | F `unparseable`; never coerces to 0 credits | Unit (fixtures: empty envelope, an `er` frame, a moved slot) |
| 5 | D8 | Cookie store locked (Chrome holds the profile; WinError 32 / sqlite locked) | **Critical** | F `store_unreadable`; **never** the Playwright cookie fallback | Unit + spike E1 evidence |
| 6 | D8 | Cookies undecryptable (DPAPI failure, v20 app-bound, Linux keyring `KeyError`) | **Critical** | F `store_unreadable` | Unit |
| 7 | D9 | Connect/read timeout, DNS or TLS error | **Critical** | F `network`, bounded (connect ≤ 3 s, total ≤ 5 s); no retry beyond one on connect error | Unit |
| 8 | D9 | 3xx to accounts.google.com on the shell GET or the POST | **Critical** | redirect **not followed**; F `redirect` | Unit |
| 9 | D1 | Cookie file missing (profile never logged in) | High | F `store_unreadable`; the page path's own auth error is what the user sees | Unit |
| 10 | D7 | Wire fails **and** the page path fails | High | the **page path's** typed error (AuthExpired, ProfileLocked exit 11, FlowAppError 31) is raised; no wire error leaks | Unit |
| 11 | D1 | `--refresh` with the wire signed out | High | wire → F → page AuthExpired → renew → retried read, which may use the wire again | Integration (fakes) |
| 12 | D1 | Read with a jar snapshotted **before** Chrome rotated its cookies | **Critical** | the browser session survives (G9); outcome from spike E2. Session loss is a STOP for the wave | spike E2 + **e2e** (session valid after N wire reads) |
| 13 | D1 | The response carries `Set-Cookie` | High | dropped, never written to Chrome's store; noted at debug level by **name** only | Unit |
| 14 | D5 | 5 concurrent MCP `gflow_get_credits` calls | High | 1 shell GET (single-flight `at` under an `asyncio.Lock`), 5 POSTs | Integration (counting transport) |
| 15 | D5 | A generation holds the profile lease while credits are read | High | the wire read takes no lease, so it succeeds if the jar is readable; otherwise F → page → `ProfileLockedError` exit 11 as today | Unit |
| 16 | D2 | Request budget | **Critical** | ≤ 1 GET + 1 POST per cold read; only allow-listed rpcids (`nzlxg`); profile's Chrome UA; `x-same-domain: 1`, origin/referer flow.google.com | Unit (allow-list refuses others) + **e2e** request count |
| 17 | D2 | Kill switch `GFLOW_CLI_WIRE_READS=0` | High | no cookie read, no wire request; the page path only | Unit |
| 18 | D8 | macOS | High | the wire defaults off on darwin (no Keychain prompt) until measured | Unit (platform patched) |
| 19 | D11 | `profile_dir` outside GFLOW_CLI_HOME | High | `SecurityError` before any cookie read (same check the browser path uses) | Unit |
| 20 | D11 | browser_cookie3 called without `cookie_file` | **Critical** | impossible by construction: `cookie_file` is always explicit (otherwise it reads the user's personal Chrome jar) | Unit (asserts the kwarg) |
| 21 | D12 | Secrets in logs / errors / problem details | **Critical** | redaction covers `__Secure-1PAPISID`, `__Secure-3PAPISID`, `SIDCC`, `NID`, `AEC`, `at=`, `f.sid=`; the fallback reason is an enum, never `str(exc)` or body text | Unit (fake values through `redact_error_detail` and the log processor) |
| 22 | D12 | Observability | Medium | `credits.read source=wire|page`, `credits.wire_fallback reason=<enum>` | Unit (capture log) |
| 23 | D5 | Cookie decrypt in the MCP server | Medium | runs in `asyncio.to_thread`; does not block the loop | Unit |
| 24 | D13 | MCP `gflow_get_credits` | High | same result shape; the tool description no longer says "opens the browser" | **e2e** (existing `test_credits_mcp_e2e.py` asserts zero Chrome launches when healthy) |
| 25 | D13 / G6 | Wire health in `doctor` and `gflow_auth_status` | High | reports `accepted / signed_out_shell / store_unreadable / disabled` plus the build label; names and counts only | Unit + **e2e** |
| 29 | D2 / D9 | Google pushes back: HTTP 429 (with or without `Retry-After`) or 503 on the shell GET or the POST | **Critical** | **No fallback** (a page load would send the same RPCs to a throttled account). Raise the existing typed `RateLimitError`, carrying `retry_after` from `api/_retry.parse_retry_after` (capped at 60 s). The **profile** is throttled until then: later reads re-raise without sending a request or building a page, and the wire is tried again after the deadline. Other 5xx (500/502/504) are not back-pressure: F `http_error`, nothing disabled. No retry inside the read. *Amended after review (PR #1236): the first version disabled the wire process-wide, so read 2 onward silently went to the page.* **Unobserved so far:** 0 of ~62 spike calls returned 429/5xx | Unit, including a second read |
| 30 | D9 | A 200 whose envelope carries an `er` frame, or a `wrb.fr` with a null payload and a numeric status slot | High | F `unparseable`; log the numeric status code only (no body), so a real rate-limit code (e.g. gRPC 8 RESOURCE_EXHAUSTED) becomes visible before we claim to handle it | Unit (fixtures) |
| 26 | D9 | Build label changes between the shell GET and the POST | Low | the POST uses the `bl` it scraped; a 400 → F | Unit (covered by row 3) |
| 27 | G1 | Cold `gflow credits user` latency | High | p50 ≤ 3 s over 5 runs; broken down into decrypt / GET / POST / parse | **live timing** (PR) |
| 28 | G2 | No Chrome is launched when the wire is healthy | **Critical** | `FlowApiClient` is never constructed | Unit (sentinel) + **e2e** |

## Must-cover before merge (Critical + High)
Rows 1–21, 24, 25, 27 and 28. Every G3 failure class (rows 1–8) has an offline test, and the
cookie-free e2e arm (row 1) proves the fallback live.

## Deferred
- Rows 22, 23 and 26 are covered inside the slice but are not acceptance gates.
- Listing (`Zzl0ze`) and record (`as29s`) wire reads are deferred to #1194/#1195/#1199.

## Suggested BDD scenarios

Live, bound from `tests/e2e/test_credits_wire_bdd.py`:

```gherkin
@e2e @e2e_auth
Feature: Credits are read without a browser when the profile's cookies are accepted

  Scenario: A healthy profile reads its balance without launching Chrome
    Given the e2e profile is signed in to Flow
    When I run "gflow credits user --json"
    Then the balance is a non-negative integer
    And no Chrome process was started for the profile
    And at most one page GET and one batchexecute POST were sent

  Scenario: Cookies Flow does not accept fall back to the browser read
    Given the wire read is forced to send no cookies
    When I run "gflow credits user --json"
    Then the balance is a non-negative integer
    And the log records a wire fallback with reason "signed_out_shell"

  Scenario: The browser session survives a run of wire reads
    Given I have read the balance over the wire 5 times
    When I run "gflow auth status"
    Then the profile's Flow session is reported valid
```

Offline, bound from `tests/features/test_credits_wire_steps.py`: one `Scenario Outline` over the
G3 reasons (`signed_out_shell`, `http_401`, `http_400`, `unparseable`, `store_unreadable`,
`network`, `redirect`). Each scenario asserts that the page path ran and that the reason was
logged.

## Known-issues cross-reference
- **"Browser session expires" (cookies rotate only on a Chrome page load):** row 12. The wire never
  rotates cookies; the page fallback does. Mitigated, not resolved. S2 #1222 measures the rate.
- **"Chromium cookie database locks":** row 5. Resolved by falling back, never by retrying or by
  the Playwright cookie fallback.
- **#795 credits entry:** its claim that the read happens in the browser becomes "browser only on
  fallback". Swept in the docs task.
- **#1140 / #1233 close hang:** every fallback pays it until #1140 lands, which comes first.
