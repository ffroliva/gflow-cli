# Signed-Media Download Retry Implementation Plan (#895)

> **For agentic workers:** Run `/gflow:status --feature 895-signed-media-download-retry` to
> find the next unchecked task. Implement one task at a time. Run `/gflow:check` before
> every commit.

**Goal:** A finished, billed Veo clip is no longer discarded when its signed-media transfer
hits a transient connection reset — and when the transfer genuinely cannot complete, the
user is told the clip exists and how to recover it, instead of `Unexpected error … exit 1`.

**Architecture:** No new module, no new error class, no new exit code, no new config. The
retry is Playwright's own `APIRequestContext.get(max_retries=…)` where that suffices; the
existing `api/_retry.py::post_with_retry` is the only fallback considered. The raw
`playwright.async_api.Error` that escapes today is translated to the existing `NetworkError`
(exit 6) at each of the three download sites. `max_redirects`, the status guards and the
`ftyp` check are untouched and stay **outside** the retried block.

**Predict verdict:** **CAUTION — 5/10** (`tmp/predict_895_retry_design.md`). The CAUTION is
on the mechanism originally proposed, not on the fix; this plan implements the corrected
shape the council converged on.

**Scenario input:** `SCENARIO.md` in this directory — 22 scenarios, 10 must-cover.

**Out of scope (tracked separately):**
- **Track B** — a generated clip is not recorded until it is downloaded (the root cause).
- **Track C** — expose the free "was it generated?" status oracle.
- **Track D** — make `gflow data download` reconcile the catalog.
- **Track E** — hygiene (dead `update_asset_status`, hardcoded `flow_workflow_id=None`,
  `VideoRow` has no status, `upsert_asset` conflict-target mismatch).

**ADR check:** `PLAN.md` § 5 ADR #9 (*"No event sourcing … YAGNI for a local CLI"*) and
ADR #2 (DDD/CQRS deferred) — neither is contradicted by this plan. Both constrain **Track
B**, where the chosen design (a second callback on the existing `VideoStartedCallback` seam)
is compatible and a true event store would be an ADR reversal. Recorded here so Track B's
predict starts from it.

## Risk register

| Severity | Risk | Mitigation |
|---|---|---|
| **Critical** | `playwright.TimeoutError` subclasses `Error`; retrying it turns one 180 s wait into three while `_generate_lock` is held, stalling every generation on the client | Native `max_retries` retries only `e.code === "ECONNRESET"`. If a Python wrap is ever needed, exclude `TimeoutError` **by type**. Task 1 pins it with an ablation test |
| **Critical** | Signed URL leaks to stderr/stdout via `str(playwright_exc)` in `detail` — Playwright concatenates its server call log into the message, and `detail` is emitted unredacted | Carry `type(exc).__name__` + attempt count only. Assert no `Expires=`/`Signature=` in the rendered error |
| **Critical** | A `url=` log kwarg would be a **new** leak — structlog kwargs are never redacted and these paths log no URL today | Kwarg allowlist, asserted in a test |
| **High** | A helper owning both the GET and the status check invites a later `resp.status in RETRY_STATUSES` edit, rebounding a retried request through a CDN open redirect | Retry wraps **only** the GET; `max_redirects` hard-coded, never a parameter; status check stays at the call site |
| **High** | S3 (`ui_automation_video.py:1255`) follows 5 redirects with **no host allowlist** | Task 4 decides: add the allowlist or record why not. Not silently deepened |
| **High** | Shipping as `WireFormatError` would drop `retryable=` (raises `TypeError`) and re-attach the "simplify your prompt" advice #875 removed | `NetworkError`, exit 6 — asserted |
| **Medium** | `website/docs/` mirror goes stale → red CI (the #626 failure) | Task 7 regenerates it; `/gflow:check` runs `generate_website_docs.py --check` |

---

## File structure

### New files — **as shipped**
```
tests/api/transports/test_signed_media_retry.py      13 tests
  Fake request context, real `playwright.async_api.Error`: the retry argument at each
  site, the NetworkError translation, the URL-leak guards, and the three behaviours the
  retry must NOT change (redirect posture, ftyp rejection, status-not-retried).
tests/mcp/test_download_error_envelope.py             6 tests
  Both MCP surfaces (`_guarded` and the daemon queue), each with a control asserting
  what an UNTYPED failure still degrades to, plus the tool-description clause.
scripts/dev/spike_playwright_max_retries.py
  E4: A/B-controlled measurement against a local socket emitting a real TCP RST.
docs/superpowers/spikes/2026-09-22-playwright-max-retries-econnreset.md
```

**The four planned BDD files were NOT written.** Recorded as a decision, not an omission
(full reasoning in `SCENARIO.md` § Correction):

- the **live** feature planned to inject the fault with `page.route(..., r.abort(...))`.
  Measured: `page.route` fires for `page.goto` (1×) and **never** for `page.request.get`
  (0×, 200 served). It is an `APIRequestContext`, outside page interception. That test
  would have **passed while proving nothing** — so it was replaced by running the existing
  `tests/e2e/test_data_download_e2e.py` live (2 passed, 20.74 s, $0) for no-regression,
  and the spike's arm A for the retry semantics;
- the **offline** feature's four scenarios are each asserted by the unit tests above. A
  Gherkin restatement would be duplication, not coverage.

### Modified files
```
src/gflow_cli/api/transports/migrated_composer.py
  S1 _fetch_mp4:2798 — max_retries on the GET; NetworkError translation; retry log event.
  Also: remediation_hint on the existing >= 300 branch (:2800).
src/gflow_cli/api/transports/migrated_recover.py
  S2 _fetch_verified:165 — same treatment; remediation_hint on :167.
src/gflow_cli/api/transports/ui_automation_video.py
  S3 _download_video:1255 — same treatment (labs route, max_redirects=5 stays);
  host-allowlist decision recorded.
src/gflow_cli/mcp/tools.py
  gflow_download_media description: internal-retry clause (:1440-1446).
docs/USAGE.md · docs/MCP.md · CHANGELOG.md · website/docs/ (regenerated)
```

---

## Task 0 — Evidence gate (no code)

**What:** Settle the two open questions that change the implementation, before writing it.

**Steps:**
- [x] **E2** — asked the reporter on #895 whether `ECONNABORTED` was observed or listed
      (posted 2026-09-22). *Node reports `socket hang up` with `code: 'ECONNRESET'`, so
      native `max_retries` covers 2 of the 3 named conditions.*
- [x] **E1 — DONE (2026-09-22). 20/20 clean; no reset reproduced here.**
      20× `gflow data download 9ad33c78-… --profile ffroliva --out tmp/reset-probe`
      (the flag is `--out`, **not** `--out-dir` — the probe command in the issue and the
      roadmap was wrong). $0, no credits. ~25 s per run.

      The raw loop printed 19 OK / 1 FAIL, **but the one failure was self-inflicted, not a
      reset.** Run 18 died at 09:58:50 with a `NameError` — the probe was running against
      the live working tree during the window where `migrated_recover.py` already called
      `get_signed_media` and its import was not yet wired. A control re-run of that exact
      media id, after the import landed, succeeded. *Lesson worth keeping: do not run a
      live probe against a tree you are editing — the probe measured my editor, not the
      network.*

      **What this does and does not establish.** It does not reproduce the reporter's
      fault: 0 resets in 20 attempts on this machine, against the **recovery** path, on
      this network — versus their 2-in-6 on the **generation** path. Different path,
      different network. So the reset looks environment-specific (the VPN / proxy / AV
      class the remediation now names), not a universal Flow behaviour.

      It therefore does **not** trigger the Devil's Advocate STOP condition
      (*"if 20/20 pass, the fault is in-run page state and retry papers over a race"*) —
      that condition assumed the probe exercised the same path. It did not. The race
      hypothesis was separately disproven by ordering: `submit_and_observe` runs its
      listener-removal `finally` (`migrated_composer.py:2510`) before `download` is called
      (`:2946`), and `grep page.route` over the file is empty.

      The design's core claim — *does attempt 2 succeed?* — is answered by **E4** instead,
      against a real socket reset, with a control. That is the stronger evidence anyway.
- [x] **E4 — DONE, CONCLUSIVE (2026-09-22).**
      [`spikes/2026-09-22-playwright-max-retries-econnreset.md`](../../spikes/2026-09-22-playwright-max-retries-econnreset.md)
      · script `scripts/dev/spike_playwright_max_retries.py`. A/B-controlled against a local
      socket server that emits a real TCP RST:

      | arm | fail | `max_retries` | timeout | outcome | elapsed | conns |
      |---|---|---|---|---|---|---|
      | A | once | 2 | 10 s | **SUCCESS** | 1.03 s | **2** |
      | B *(control)* | once | **0** | 10 s | **FAILURE** — `read ECONNRESET` | 0.75 s | 1 |
      | C | always | 2 | **5 s** | FAILURE — `Failed after 3 attempt(s)` | **1.57 s** | **3** |

      - Native `max_retries` **does** retry `ECONNRESET` on `APIRequestContext.get`.
      - The control proves it: same fault, retry disabled, call dies.
      - **The timeout budget is SHARED** — 3 attempts at a 5 s timeout took 1.57 s, not ~15 s.
        So `max_retries=2` on our 180 s call is a ~180 s worst case, **not 9 minutes.**
      - `max_retries=N` ⇒ N+1 attempts, matching `_retry.py:39 MAX_ATTEMPTS = 3`.
      - **Arm B reproduces the reporter's exact error string on localhost** — no account, no
        browser, no credits. That socket server is Task 1's harness and its ablation.

**Decision this gates — RESOLVED:**
- **Ship native `max_retries=2`.** No Python wrap, no second retry policy, no custom backoff,
  no new constant. The council's Critical `TimeoutError` risk **cannot arise**: the driver
  matches on `e.code === "ECONNRESET"`, never on a Python exception type.
- **Only if E2 comes back "observed"** → add a thin wrapper around the **existing**
  `post_with_retry()` with `TimeoutError` excluded by type, as a follow-up commit. Native-only
  is correct and shippable regardless.

---

## Task 1 — Unit test scaffold (red)

**What:** Pin every behaviour the retry must and must not have, before it exists.

**Files:** `tests/api/transports/test_signed_media_retry.py`

**Steps:**
- [x] Build a fake request context whose `.get()` raises a scripted sequence then returns a
      stub response (status + body), so no browser is involved.
- [x] Parameterize across all three sites where the shape allows.

**Tests created (red):**
- [ ] `test_reset_on_first_attempt_still_downloads` — S#1: succeeds, bytes written.
- [ ] `test_reset_on_every_attempt_raises_network_error` — S#2: `NetworkError`, exit 6,
      `retryable is True`, remediation contains the real media id.
- [ ] `test_timeout_is_not_retried` — S#3. **Ablation:** delete the exclusion and this test
      must fail. Add that instruction as a comment in the test.
- [ ] `test_http_403_is_not_retried` — S#4: one attempt; remediation names expiry, and does
      **not** contain "simplify".
- [ ] `test_3xx_never_reaches_the_retry_predicate` — S#5.
- [ ] `test_poster_fallback_and_ftyp_check_unchanged` — S#6.
- [ ] `test_size_mismatch_warning_survives_a_retry` — S#7.
- [ ] `test_error_detail_carries_no_signed_url_credentials` — S#13: assert neither
      `Expires=` nor `Signature=` appears in the rendered Problem Details.
- [ ] `test_retry_log_kwargs_are_allowlisted` — S#11: capture structlog events; assert the
      kwarg set and that `url` is absent.
- [ ] `test_attempt_counter_does_not_leak_between_calls` — S#18.
- [ ] `test_catalog_writes_are_unchanged_on_retry_success` — S#8.
- [ ] `test_catalog_state_after_exhaustion_is_pending_and_failed` — S#9. **Documents today's
      lie so Track B has a failing test to flip.** Comment it as such.

---

## Task 2 — BDD scaffold (red)

**What:** The Gherkin from `SCENARIO.md`, bound by surface.

**Files:** the four new BDD files listed above.

**Steps:**
- [ ] Offline feature → `tests/features/`, **untagged** (exit codes, error shape and
      redaction are our code).
- [ ] Live feature → `tests/e2e/`, `@e2e @e2e_data` (zero credits — it re-fetches an
      already-billed asset).
- [ ] One feature file per binding module — `tests/features/test_e2e_binding_guard.py`
      enforces this in four directions offline.
- [ ] Live step uses `page.route(..., lambda r: r.abort("connectionreset"))` for the first
      N attempts, then releases.

**Tests created (red):** the four offline scenarios + one live scenario from `SCENARIO.md`.

---

## Task 3 — The retry (S1 + S2)

**What:** Make the two migrated-host GETs survive a transient reset.

**Files:** `migrated_composer.py:2798`, `migrated_recover.py:165`

**Steps:**
- [x] Add `max_retries=2` to both calls. **Do not touch** `max_redirects=0`, `timeout=180_000`,
      the `>= 300` guard or the `ftyp` check.
- [ ] Only if Task 0 says so: wrap with `post_with_retry()` excluding `TimeoutError` by type.
      Write the justification in the PR body either way (Security persona's requirement).
- [x] Emit `log.warning("migrated.download_retry", media_id=…, attempt=…, of=…, error_class=…)`.
      **No `url=`.** No success event — `migrated.download` (`:2848`) already records it.

**Tests:** Task 1's S#1, #3, #5, #6, #7, #11, #18 go green.

---

## Task 4 — Typed error + remediation (all three sites)

**What:** Replace the escaping `playwright.async_api.Error` with `NetworkError`, and stop the
403 branch blaming the prompt.

**Files:** all three sites, incl. **S3** `ui_automation_video.py:1255`

**Steps:**
- [x] Catch the transport error at each site; raise `NetworkError(detail=…,
      remediation_hint=…, route=…)`. `detail` carries `type(exc).__name__` + attempt count,
      **never** `str(exc)`.
- [x] Remediation, generation path (credits spent): *"The clip was generated and is safe in
      Flow — only the download failed… Recover it for free with `gflow data download <id>`.
      Do not re-generate."* Ship the **real** id.
- [x] Remediation, `data download` path (not circular — say what a re-run changes): *"Nothing
      was lost and nothing was billed… Re-run this command: each attempt restarts the
      transfer… If it keeps failing, a VPN or corporate proxy interrupting large transfers is
      the usual cause."*
- [x] Extend `remediation_hint` to the existing `>= 300` / `>= 400` branches so an expired
      signed link names expiry and re-mint instead of `WireFormatError`'s prompt default.
- [x] **S3 decision:** `_download_video` follows 5 redirects with no `_is_allowed_download_host`
      check. Either add it or record the reason in a code comment **and** the PR body. Do not
      leave it undecided.

**Tests:** Task 1's S#2, #4, #13 go green; the offline BDD goes green.

---

## Task 5 — CLI surface

**No CLI change.** No new option, no new flag, no new exit code — `NetworkError`/exit 6 is
already in `EXIT_CODE_MAP` and already documented. **Stated explicitly rather than omitted**,
per the #626 rule.

---

## Task 6 — MCP surface mirror (**not optional**)

**What:** The two MCP twins currently flatten this failure *worse* than the CLI does. Typing
the error fixes both; the tool description must then stop an agent from stacking its own retry.

**Files:** `src/gflow_cli/mcp/tools.py`

**Steps:**
- [x] Add to `gflow_download_media`'s description (`:1440-1446`): *"Already retries the
      transfer internally; a `retryable` failure means re-call after a pause, not
      immediately."* (Without it, an agent re-calling immediately opens a second browser under
      the per-profile lease → `ProfileLockedError`, exit 11.)
- [x] Confirm no docstring in `tools.py` becomes false — checked at scenario time for
      `:1440-1446` and `:990-1001`; re-check after the change.
- [x] Assert mirror axis A explicitly: no new option/param, `worker/codec.py` has no
      error-path code. Record in the PR body.

**Tests:**
- [x] `test_download_media_exhaustion_returns_problem_details` — S#19: today `_guarded`'s
      `except Exception` (`:211`) → `_masked_unexpected_dict` (`:175-190`) strips the
      remediation entirely. Assert it is present after.
- [x] `test_queued_generate_video_exhaustion_carries_remediation` — S#20: today
      `worker/daemon.py:476-484` returns `detail: "sha256:<hash>"` with no `remediable`/
      `retryable`. **A different surface from `_guarded`** — assert the daemon payload shape.
- [x] `tests/mcp/test_cli_parity.py` stays green (S#22).

---

## Task 7 — Docs

**Files:** `docs/USAGE.md`, `docs/MCP.md`, `CHANGELOG.md`, `website/docs/` (generated)

**Steps:**
- [x] `USAGE.md` § `gflow data download` — the internal retry, and that it exits **6**, not 1.
- [x] `USAGE.md` § Exit codes row 6 — its blanket *"Check connectivity"* is now wrong for the
      generation site, where the right advice is `gflow data download`. Add the *"read the
      error's own remediation_hint first"* qualifier that row 3 already uses.
- [x] `docs/MCP.md:103` — the internal-retry + don't-stack-a-loop clause.
- [x] `CHANGELOG.md` `[Unreleased]` → Fixed.
- [x] **Regenerate `website/docs/`** — `USAGE.md` and `MCP.md` are in the generated set;
      `generate_website_docs.py --check` goes red otherwise (#626).
- [x] `KNOWN_ISSUES.md` — no entry exists for #895; add none unless the fix is partial.

---

## Task 8 — Gates, e2e, PR

**Steps:**
- [ ] `/gflow:check` green (hygiene, doc-links, website-docs, council-memory, ruff, format,
      pyright, pytest ≥ 80%).
- [ ] **Iron Law:** run the live BDD — `-m e2e_data` with `GFLOW_CLI_E2E_PROFILE`. Zero
      credits. Paste the result in the PR.
- [ ] **Ablate** the `TimeoutError` exclusion and confirm Task 1's test fails; restore.
- [ ] Draft PR off `develop`, branch `bugfix/895-signed-media-download-retry`.
- [ ] `/gflow:sonar <N>` to zero.
- [ ] Post the S3 allowlist decision and the native-vs-wrap justification in the PR body.
- [ ] File Tracks B–E as issues; raise `max_redirects=5` on PR #824.

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green
- [ ] `CHANGELOG.md` `[Unreleased]` updated
- [ ] Docs updated **and `website/docs/` regenerated**
- [ ] BDD covers all 10 must-cover scenarios from `SCENARIO.md`
- [ ] The ablation was run and observed to fail
- [ ] Both MCP envelopes asserted — they are two surfaces, and the Iron Law applies to each
- [ ] No `# TODO` in the diff without a tracked issue link
