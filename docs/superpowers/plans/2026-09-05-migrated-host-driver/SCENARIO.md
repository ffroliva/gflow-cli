# Scenario: migrated-host driver — drive `flow.google.com` (Angular) for t2v / t2i

Predict (2026-09-04): CAUTION 5/10 as opt-in; STOP on "default now" — lifted for the
**flagged-account** case by the 2026-09-05 wire spike (two real clips completed, wire
decoded, unflagged account also served the Angular editor). Recon:
`docs/superpowers/spikes/2026-09-04-migrated-host-handoff-mechanism.md`,
`docs/superpowers/spikes/2026-09-05-migrated-host-wire-protocol.md`.

## Coverage map

Active: **D1** (a second host, a second cookie jar), **D2** (in-page reCAPTCHA on a new
origin), **D3** (a whole new selector map on Angular Material — the core risk), **D5**
(pool pages start on `about:blank`; host is per page), **D6** (recorder rows for a
generation whose ids come from batchexecute), **D7** (exit 36 semantics change; new
failure shapes), **D9** (batchexecute envelope, signed CDN URLs, new download host),
**D11** (settings the new host does not render), **D12** (new events must be
timeline-readable), **D13** (MCP twins of routing + every param).
Skipped: **D4** (no batch/manifest change — `video batch` does not exist; `image batch`
reuses the single-shot path), **D8** (no new paths; output naming unchanged), **D10**
(headed real-Chrome only, as today).

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D3 | Flagged account, `gflow video t2v`: labs bootstrap hops to `flow.google.com` | Critical | Transport detects the migrated host after `_enter_editor` and dispatches to the migrated composer instead of raising exit 36; clip downloaded; exit 0 | Integration + E2E live |
| 2 | D3 | Settings panel: one of the six radiogroups is missing (model-state: no duration row with a Veo model; no resolution row on another cohort) | High | Only the requested axis that has no control fails, with a typed `ConfigurationError` (exit 11) naming the axis — never a silent default (mirrors `_reject_duration_without_control`) | Unit (fake pane) |
| 3 | D3 | Radio click does not flip `aria-checked` (overlay re-rendered, click hit a stale node) | High | Re-query and retry once; then `UiSelectorDriftError` exit 23 with `host=migrated` in the detail | Unit |
| 4 | D3 | Model picker: requested model not among the `menuitem`s (e.g. alias maps to a labs-only name) | High | `ConfigurationError` exit 11 listing the offered names; picker closed; no submit | Unit |
| 5 | D3 | Composer `textarea` not clickable (measured) — prompt must go into the `contenteditable` | High | Type into `[contenteditable='true']`; submit button enabled only after text; assert prompt echoed in the `YhhmEf` body | Unit + Integration |
| 6 | D3 | Non-EN account (`pt`, measured on the unflagged profile): every anchor still resolves | High | No text anchors except numeric tokens and product names; aria-labels never used; probe passes on `pt` | E2E live (pt profile) |
| 7 | D9 | `YhhmEf` 200 but record has no media id / status cell (envelope drift) | Critical | `WireFormatError` with a redacted discovery payload (rpcid + frame head), no credits assumed spent, no recorder STARTED row | Unit (parser) |
| 8 | D9 | Poll status never reaches `3`; unknown status value appears (failure enum not yet observed) | Critical | Unknown status ≥ N surfaces as a failed `VideoStatus` with the raw value; `poll_timeout_s` bounds the wait; exit non-zero with remediation | Unit |
| 9 | D9 | `as29s` never arrives although a poll showed `3` (listener attached late or app skipped it) | High | Status `3` in `jwpduf` alone is terminal: the signed URL is read from whichever record carries it; timeout otherwise | Unit |
| 10 | D9 | Signed CDN URL host is not `flow-content.google` (CDN moved) | High | Download refused by the allowlist with `WireFormatError` naming the host — never an SSRF to an arbitrary host | Unit |
| 11 | D9 | Signed URL expired (`Expires` in the past) or 403 on GET | High | `NetworkError`/`WireFormatError` with the status; the clip remains in the project — remediation says re-run download from the library later | Unit |
| 12 | D1 | Unflagged account with `GFLOW_CLI_FLOW_HOST=flow.google.com`: direct load works (measured) | High | Opt-in routes the unflagged account through the migrated composer; exit 0 | E2E live (denon82) |
| 13 | D1 | Migrated host serves the login page (no `.google.com` SSO session) | High | `AuthExpiredError` (exit 3) from the readiness anchor missing + login-form structural detection, not a 30 s selector timeout | Unit |
| 14 | D2 | reCAPTCHA reload fails / token missing → `YhhmEf` returns an error frame | High | Surface as `RecaptchaError`-class with the frame text redacted; no retry loop | Unit |
| 15 | D5 | Pooled page N is on `about:blank`; host kind unknown until navigation | Medium | Dispatch decision is taken **after** `_enter_editor` on the page in use, never from a cached URL | Unit |
| 16 | D6 | `on_started` receives a `VideoStarted` whose media id is the batchexecute media id | High | Recorder STARTED row inserted with `flow_operation_id=<workflow id>`; download failure later still leaves the row | Unit |
| 17 | D7 | Flagged account, driver dispatch **disabled** (kill switch) | Medium | Exit 36 unchanged, remediation names the env var | Unit |
| 18 | D7 | Labs account with the migrated composer forced but page never reaches the Angular editor | Medium | Readiness timeout → `UiSelectorDriftError` exit 23, `host=migrated` in detail | Unit |
| 19 | D11 | `--duration 10` with a Veo model on the migrated host (only 4/6/8 rendered) | High | Exit 11 pre-submit, no credits — the radio is absent, not "clicked and ignored" | Unit |
| 20 | D11 | `--count 4` when the count row renders `x1..x4` | Low | Selected and echoed as `x4` in the chip read-back | Unit |
| 21 | D12 | Timeline must show dispatch + each stage | Medium | Events `ui_driver.bound host=migrated`, `migrated.settings_applied`, `migrated.submit_observed rpc=YhhmEf`, `migrated.status status=2/3`, `migrated.result_url host=…`; `correlation_id` bound | Unit (capture_logs) |
| 22 | D13 | MCP `gflow_generate_video` queued path on a flagged account | Critical | Same dispatch (shared transport); envelope on failure is the RFC 9457 dict; `retryable` derives from `is_retryable` | Integration (worker) |
| 23 | D13 | MCP docstrings still say "cannot drive the migrated frontend" | High | Docstring + `docs/MCP.md` + `KNOWN_ISSUES.md` #639 + USAGE exit-36 row updated in the same PR | Docs gate |
| 24 | D3 | "High demand" info banner overlays the top of the editor | Low | Never intercepts the composer or the pane; if a click is intercepted, dismiss via the banner's `close` ligature button | Unit |

## Must-cover before merge (Critical + High)

1. Dispatch on the flagged account (1) with the recorder row (16) and the queued MCP twin (22).
2. Parser: envelope drift (7), unknown status (8), status-3-in-poll (9).
3. Download safety: allowlist (10), expired URL (11).
4. Settings: missing radiogroup (2), stale radio (3), model not offered (4), composer contenteditable (5), duration-without-control (19).
5. Auth/recaptcha shapes (13, 14); readiness timeout (18); kill switch (17).
6. Docs truth (23).
7. Live: t2v on the flagged profile (1) and, opt-in, on the unflagged `pt` profile (6, 12) — the e2e matrix the user asked for, credits approved for video; t2i is credit-free.

## Deferred (Medium + Low — log as issues, not blockers)

1. Direct bootstrap (skip the labs boot + 4 s locale probe on flagged accounts) — perf, not correctness.
2. i2v / r2v (uploads, frame slots, ingredients) on the migrated host — separate recon.
3. Character / scene / extend / instructions / tools on the migrated host — REST-backed ones already work.
4. Banner dismissal (24), pooled-page host cache (15).

## Suggested BDD scenarios (for `tests/features/`)

```gherkin
Feature: migrated-host driver
  Scenario: a flagged account generates a video on flow.google.com
    Given the editor hands the session to flow.google.com after entering the project
    When gflow video t2v runs with an 8 s Omni 1.1 Flash request
    Then the migrated composer applies the settings and submits
    And the YhhmEf response yields a workflow id and a media id
    And a poll with status 3 yields a flow-content.google URL
    And the clip is downloaded and the exit code is 0

  Scenario: the requested axis has no control on this host
    Given the settings pane renders no duration radiogroup
    When a 10 s duration is requested
    Then the run aborts pre-submit with exit 11 and names the missing axis

  Scenario: the driver is switched off
    Given GFLOW_CLI_MIGRATED_DRIVER is 0
    When the editor hands the session to flow.google.com
    Then the run fails with exit 36 and the remediation names the switch
```

## Known-issues cross-reference

- **#639** (flow.google.com migration): this plan *resolves* the generation half for t2v/t2i on flagged accounts; the issue stays open until the remaining feature matrix is ported.
- **#650 / duration cohort** (KNOWN_ISSUES duration entry): the migrated host renders duration per model-state exactly like labs — the same "control rendered?" guard applies (scenario 19), not a per-host table.
- **#299 UI-arm cohort flaps**: not applicable on the migrated host (no agentic/classic arm observed); `--ui-mode agentic` must be rejected there with exit 28 until defined.
