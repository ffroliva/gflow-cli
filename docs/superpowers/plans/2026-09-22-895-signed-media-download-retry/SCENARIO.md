# Scenario: bounded retry for the signed-media download (#895)

**Feature:** retry the signed-media GET on transport errors, and replace the raw
`playwright.async_api.Error` that currently escapes with a typed `NetworkError`.

**Upstream:** `/gflow:issue-assessment 895` (CONFIRMED-BUG 9/10) → `/gflow:predict`
(**CAUTION 5/10**, `tmp/predict_895_retry_design.md`) → this.

**Scope note — Track A only.** The predict and the data-layer trace found a larger root
cause (a generated clip is not recorded until it is downloaded) and a missing status oracle.
Those are **Tracks B and C** with their own issues; this document covers the transfer fix
only.

---

## The three call sites are NOT identical

The predict treated them as one shape. They are not, and the difference changes both the
fix and the tests.

| | Site | Route | redirects | status guard | host allowlist |
|---|---|---|---|---|---|
| **S1** | `migrated_composer.py:2798` (`_fetch_mp4`) | signed `flow-content.google` CDN, direct | `max_redirects=0` | `>= 300` | ✅ `:2788` |
| **S2** | `migrated_recover.py:165` (`_fetch_verified`) | same, direct | `max_redirects=0` | `>= 300` | ✅ `:155` |
| **S3** | `ui_automation_video.py:1255` (`_download_video`) | labs `media.getMediaUrlRedirect` — **302s by design** | `max_redirects=5` | `>= 400` | ❌ **none** |

S3 cannot adopt `max_redirects=0` — the route *is* a redirect. Its 5-hop follow with no
allowlist on the landing host is an open-redirect surface of exactly the kind the
`max_redirects=0` posture on S1/S2 exists to prevent. **That is a finding, not a scenario
detail** (see #12 below).

---

## Coverage map

**Active:** D5 (concurrency — the retry holds `_generate_lock`), D6 (data layer — what a
retry writes), D7 (error propagation & exit codes — the core of the change), D8
(cross-platform — the reporter is on Windows), D9 (transport edge cases — redirects,
allowlist, expired URLs), D12 (observability — the leak channel), D13 (MCP parity).

**Partially active:** D1 (no auth change, but the signed URL's validity window is a
session-adjacent lifetime), D4 (a retry inside a multi-generation shell loop).

**Skipped, with reason:**
- **D2 WAF/reCAPTCHA** — `flow-content.google` authorizes by query signature, not cookies
  or a reCAPTCHA mint (`ui_automation.py:173-175`). The only WAF finding in the tree is
  `batchGenerateImages` 403 on `aisandbox-pa` (`KNOWN_ISSUES.md:933-968`), a generation
  route. Whether the CDN participates in the anti-bot stack is **unevidenced in either
  direction** — recorded here so the next reader does not re-derive it. Scenario 14 pins it.
- **D3 selector drift** — no DOM selector is read or written by this change.
- **D10 headless vs headed** — the GET runs through an already-established context;
  headedness is decided long before.
- **D11 input validation** — no new user input. The only new value is an internal attempt
  counter.

---

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D7 | Transport error on attempt 1, success on attempt 2 | **Critical** | Clip is downloaded; command exits 0; **no** error surfaced; one `migrated.download_retry` warning | Unit (fake request ctx) + E2E (`e2e_data`, route-abort) |
| 2 | D7 | Transport error on all attempts | **Critical** | `NetworkError`, **exit 6**, `retryable: true`, `remediation_hint` naming the real `media_id` and `gflow data download` | Unit + BDD |
| 3 | D7 | `playwright.TimeoutError` raised (subclasses `Error`) | **Critical** | **Not retried.** One 180 s wait, not three. Surfaces as it does today | Unit — *the ablation test: drop the exclusion and this must fail* |
| 4 | D7 | HTTP 403 (expired signed URL) on attempt 1 | **High** | **Not retried** (status, not exception). Fails immediately with a hint naming expiry + re-mint — **not** `WireFormatError`'s "retry with a simpler prompt" default (`errors.py:372-376`) | Unit + BDD |
| 5 | D7 | HTTP 3xx on S1/S2 with `max_redirects=0` | **High** | Returned as a response, caught by `>= 300`, **never** reaches the retry predicate | Unit |
| 6 | D9 | Body is a poster JPEG, not `ftyp` | **High** | The `ftyp` check stays **outside** the retry; loop falls through to `poster_url` as today; no retry burned on a wrong-artifact result | Unit (regression — `:2806`) |
| 7 | D9 | `size_bytes` mismatch after a *retried* success | **Medium** | The existing `migrated.download_size_mismatch` warning still fires (`:2807-2812`); a retry must not suppress it | Unit |
| 8 | D6 | Retry succeeds on attempt 2 — what is written? | **High** | **Nothing extra.** The download is not a catalog event until success; `record_completed_video` runs exactly once | Unit (assert no second `upsert_asset`) |
| 9 | D6 | Exhausted retry — catalog state | **High** | `assets.status` stays `"pending"`, `operations.status` → `'failed'`. **This is the known lie; Track B fixes it.** Assert it explicitly so Track B has a failing test to flip | Unit — *documents current behaviour, must be updated by Track B* |
| 10 | D5 | Retry runs while `_generate_lock` is held | **Critical** | Total elapsed must stay ≈180 s. With native `max_retries` the backoff is charged to the same budget; a Python wrap that retries `TimeoutError` gives 543 s and stalls **every** generation on the client (`ui_automation.py:1002`, `ui_automation_video.py:3498`) | Integration (assert bounded elapsed) |
| 11 | D12 | What is logged on each attempt | **Critical** | `log.warning("migrated.download_retry", media_id, attempt, of, error_class)`. **No `url=`** — structlog kwargs are never redacted (`observability.py:88-98`) and these paths log no URL today | Unit (assert kwarg allowlist) |
| 12 | D9 | **S3 follows 5 redirects to an unallowlisted host** | **High** | `_download_video` (`ui_automation_video.py:1255`) has no `_is_allowed_download_host` check. Adding retry must not deepen it. **Decide: add the allowlist, or record why not** | Unit + note in PR |
| 13 | D7/D12 | Exhaustion error `detail` content | **Critical** | Carries `type(exc).__name__` + attempt count. **Never `str(playwright_exc)`** — Playwright concatenates its server call log into the message (`_connection.py:422-423`) and `detail` reaches stderr (`observability.py:146-155`) and stdout (`json_output.py:56-68`) unredacted | Unit (assert no `Expires=`/`Signature=` substring) |
| 14 | D2 | 3 rapid CDN re-requests from one profile | **Low** | No WAF effect expected — but **unevidenced**. Do not assert either way; record the observation if E1's 20× probe shows any 403/429 | Observation during E1, not a test |
| 15 | D8 | Windows: VPN / proxy / AV inspection interrupts a large transfer | **High** | This is the reporter's most likely environment cause. The `data download` remediation names it. `ECONNRESET`/`socket hang up` come from Playwright's **Node** driver — identical strings on all platforms, so no OS branch | Unit (platform-agnostic assertion) |
| 16 | D8 | Output path with spaces / non-ASCII under `%LOCALAPPDATA%` | **Low** | Unchanged — the retry does not touch path handling | Existing coverage |
| 17 | D1/D9 | Signed URL expires *between* attempt 1 and attempt 3 | **Medium** | Lands as 403 → scenario 4. Total added elapsed is ~0.75 s native, so this is near-impossible in practice; assert the 403 path, not a timing race | Unit |
| 18 | D4 | Shell loop of N generations, one download resets | **Medium** | Only that iteration retries; no cross-iteration state. Retry must not leak attempt state between calls (no module-level counter) | Unit (two sequential calls, independent counters) |
| 19 | D13 | `gflow_download_media` MCP tool, exhausted retry | **Critical** | Today the bare Playwright error hits `_guarded`'s `except Exception` (`mcp/tools.py:211`) → `_masked_unexpected_dict` (`:175-190`) → *"Unexpected Error; details were logged server-side"*, **no `remediation_hint` key at all**. Typed → full Problem Details | Unit (MCP envelope shape) |
| 20 | D13 | `gflow_generate_video` MCP tool (queued), exhausted retry | **Critical** | Today `worker/daemon.py:476-484` replaces the message with `detail: "sha256:<hash>"` and emits **no `remediation_hint`, no `retryable`**. Typed → `daemon.py:459` already calls `to_problem_details()`, so both appear | Unit (daemon payload shape — **a different surface from `_guarded`**) |
| 21 | D13 | Agent re-calls `gflow_download_media` on `retryable: true` | **High** | A second browser under the per-profile lease → `ProfileLockedError`, **exit 11**. Tool description must say "already retries internally; re-call after a pause, not immediately" | Doc assertion + unit |
| 22 | D13 | MCP option/param parity | **Low** | **No new option, no new param.** `worker/codec.py` has no error-path code; mirror axis A untouched. State this explicitly in the PR rather than leaving it blank (#626 rule) | `tests/mcp/test_cli_parity.py` (unchanged, assert it stays green) |

**Severity:** Critical (data loss / billed twice / unrecoverable) · High (feature broken, workaround exists) · Medium (degraded UX, explicit error) · Low (cosmetic / edge-only)

---

## Must-cover before merge (Critical + High)

1. **#3 — `TimeoutError` is not retried.** The single highest-risk line in the change.
   Write it as an **ablation**: remove the exclusion and the test must fail
   (`ablate-the-feature-to-test-the-test`).
2. **#13 — no signed URL in `detail`.** Assert the rendered error contains neither
   `Expires=` nor `Signature=`.
3. **#11 — no `url=` in any log kwarg.** Assert against an allowlist of kwargs.
4. **#1 / #2 — the actual fix**, both outcomes.
5. **#10 — bounded elapsed** while `_generate_lock` is held.
6. **#19 / #20 — both MCP envelopes.** Two separate surfaces; the Iron Law applies to each.
7. **#4 — the 403 path** stops shipping "simplify your prompt".
8. **#5, #6, #8 — the three things retry must NOT change**: redirect posture, `ftyp`
   rejection, catalog writes.
9. **#12 — decide S3's allowlist** and record the decision either way.
10. **#9 — pin current catalog behaviour** so Track B has a test to flip.

## Deferred (Medium + Low — issues, not blockers)

1. **#14** WAF observation — record from E1, do not test.
2. **#17** expiry-mid-retry — covered by #4.
3. **#16** path handling — unchanged.
4. **#7, #18** — cheap, include if free; not blockers.

---

## Suggested BDD scenarios

Offline — the exit code, the error shape and the redaction are **our** code, so these bind
from `tests/features/`, untagged.

```gherkin
Feature: The signed-media download survives a transient reset

  Scenario: a reset on the first attempt does not lose the clip
    Given Flow has reported the generation is done
    And the signed media URL drops the connection once before serving the clip
    When the transport downloads the clip
    Then the clip is written to disk
    And the command succeeds
    And a download-retry warning names the attempt number

  Scenario: a reset that survives every attempt is a typed network failure
    Given Flow has reported the generation is done
    And the signed media URL drops the connection on every attempt
    When the transport downloads the clip
    Then the command fails with exit code 6
    And the error is marked retryable
    And the remediation names the media id and the recovery command
    And the error detail contains no signed-URL credentials

  Scenario: a slow server is not retried into a multiplied wait
    Given the signed media URL never responds
    When the transport downloads the clip
    Then the request is attempted exactly once

  Scenario: an expired signed link is not blamed on the prompt
    Given the signed media URL answers 403
    When the transport downloads the clip
    Then the request is attempted exactly once
    And the remediation names the expired link and the recovery command
    And the remediation does not mention simplifying the prompt
```

Live — that Flow still resets and still recovers can only be shown against the real CDN.
Zero credits: it re-fetches an asset that was already billed.

```gherkin
@e2e @e2e_data
Feature: Recovering a billed clip survives a connection reset

  Scenario: the recovery download retries a real aborted transfer
    Given a catalogued video with no local file
    And the first transfer attempt is aborted at the network layer
    When the clip is recovered by media id
    Then the recovered file carries MP4 magic bytes
    And the catalog records the local file
```

> ### ⚠️ Correction (2026-09-22, measured): the live scenario above is **not implementable
> as written**, and was not shipped.
>
> It planned to inject the reset with `page.route(..., r.abort("connectionreset"))` — the
> `route-abort-proves-a-submit-contract-free` pattern. **`page.route` does not intercept
> `page.request`.** Measured with a control in one run
> ([spike](../../spikes/2026-09-22-playwright-max-retries-econnreset.md) § second finding):
> the handler fired **1×** for `page.goto` and **0×** for `page.request.get`, which was
> served a normal 200. `page.request` is an `APIRequestContext` — its traffic goes through
> the Node driver, sharing the context's cookie jar but not its interception.
>
> A route-abort test here would have **passed while proving nothing**. This applies to
> every signed-media download in the codebase, since all of them use `page.request`.
>
> **What shipped instead**, and why it is stronger:
> - the **retry semantics** are proven against a real TCP RST from a local socket server,
>   A/B-controlled (arm B, retry disabled, fails on the identical fault) — E4 in the spike;
> - the **error contract** is proven by `tests/api/transports/test_signed_media_retry.py`
>   (13 tests) and `tests/mcp/test_download_error_envelope.py` (6), including the real
>   `playwright.async_api.Error` class, so a broken type predicate cannot pass;
> - **no regression against real Flow** is proven by running the existing
>   `tests/e2e/test_data_download_e2e.py` live (`e2e_data`, $0): **2 passed in 20.74 s**.
>
> The offline Gherkin was also **not** written: all four of its scenarios are asserted by
> the unit tests above, and a second copy in Gherkin would be duplication, not coverage.
> Recorded as a decision, not an omission.

---

## Known-issues cross-reference

| Entry | Relation |
|---|---|
| **#871** (signed-URL grace) | **Mis-cited by the issue, and by my own reply.** `RESULT_URL_GRACE_S = 20.0` (`migrated_composer.py:317`) and `SIGNED_URL_WAIT_S = 45.0` (`migrated_recover.py:54`) are waits for the URL to **appear in a batchexecute frame**, not TTLs on the URL. Nothing in the tree measures when a signed URL expires. The sub-second backoff is correct for a different reason: the driver's own 250/500 ms schedule. **Correction owed on #895.** |
| **#865 / #877** | `gflow data download` shipped v0.79.0; it is the recovery this error points at. Its GET is **S2** — fixed by the same change. |
| **#875** | Removed "retry with a simpler prompt" from a route that never took a prompt. Scenario 4 prevents re-attaching the same wrong advice class via `WireFormatError`'s default. |
| **#626** | The regenerate-`website/docs/` gate. `USAGE.md`, `MCP.md` are in the generated set — `generate_website_docs.py --check` goes red without it. |
| **#824** (open PR) | Adds a **fourth** download site, `agent_only_composer.py::download_video`, with `max_redirects=5` against the documented posture. Raise on that PR; do not fold in here. |
| `KNOWN_ISSUES.md:933-968` | The only WAF evidence in the tree — a generation route, not the CDN. Basis for skipping D2. |

---

## Open evidence gates (Phase 0 — block implementation choices, not this document)

- **E1** — 20× `gflow data download` probe. Decides whether retry is the remedy and whether
  attempt 2 succeeds. Also feeds scenario 14.
- **E2** — was `ECONNABORTED` observed or listed? Node reports `socket hang up` with
  `code: 'ECONNRESET'`, so native `max_retries` covers 2 of the 3 named conditions.
  **If E2 says "listed", native alone is sufficient and no Python wrap is written.**
- **E4** — ablate native `max_retries` against a killed socket to confirm it shares one
  timeout budget (source-read from the shipped driver, not yet measured).

**Next step:** Phase 4 Implementation Plan (`/gflow:plan`).
