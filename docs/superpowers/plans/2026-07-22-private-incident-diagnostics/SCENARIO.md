# Scenario: Private Incident Diagnostics

## Coverage map

All 12 dimensions are relevant because the feature sits across the browser lifecycle,
error funnel, filesystem, and machine-readable error surfaces.

- **D1 Auth & session lifecycle** — profile contention is a capture trigger, while
  expected auth expiry must remain a non-trigger.
- **D2 WAF / reCAPTCHA scoring** — network events can contain the highest-risk secrets
  in the application and must be reduced to allowlisted metadata.
- **D3 Selector cascade drift** — structural DOM and sensitive screenshots are intended
  to diagnose Flow UI drift, app crashes, and one-time overlays.
- **D4 Batch manifest & resume** — repeated systemic failures must be deduplicated and
  capture must never alter submit/resume semantics.
- **D5 Concurrency & Page pool** — context/page listeners, concurrent failure capture,
  and long-running bounded memory are core lifecycle concerns.
- **D6 Data layer** — no schema change is planned, but queue/worker error persistence
  must not leak local paths or raw evidence. SQLite migration scenarios are otherwise
  out of scope.
- **D7 Error propagation & exit codes** — capture is subordinate to the original RFC
  9457 error and must preserve cancellation, traceback, exit code, and retryability.
- **D8 Cross-platform paths** — incident roots, permissions, Unicode, Windows reparse
  points, and atomic writes are platform-sensitive.
- **D9 Transport edge cases** — listener ordering, unknown HTTP 400 responses, route
  reduction, and HAR finalization are direct feature inputs.
- **D10 Headless vs headed environment** — the page can be absent, unresponsive, or
  manually closed while capture/teardown is running.
- **D11 Input validation & boundary values** — boolean configuration, caps, disk-full
  behavior, incomplete bundles, and retention limits need hard boundaries.
- **D12 Observability & structured log contract** — the new stable events and incident
  metadata must remain correlated, bounded, and secret-free.

## Scenario table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| S01 | D2 WAF/reCAPTCHA | Request query, headers, post body, cookies, signed URL, prompt, and reCAPTCHA token all contain a unique canary | Critical | No canary appears in automatic JSON, logs, problem details, filenames, or artifact lists; raw HAR remains the only explicit raw path | Unit + Integration |
| S02 | D2 WAF/reCAPTCHA | Upstream JSON uses a prompt/token as an unexpected top-level key | Critical | Persist only allowlisted known-key booleans plus an unknown-key count; never persist arbitrary key names | Unit |
| S03 | D2 WAF/reCAPTCHA | Console text/title contains a short email, access token, or low-entropy account name | High | Persist category and length only, or a per-incident keyed digest; never claim an unsalted hash is anonymization | Unit |
| S04 | D2 WAF/reCAPTCHA | UI failure screenshot contains account identity, prompt, reference media, and generated media | High | File lives only under `sensitive/`, is marked sensitive in the manifest, and every operator surface says review before sharing | Integration |
| S05 | D2 WAF/reCAPTCHA | `GFLOW_CLI_HAR_PATH` is unset during a failure | High | No raw HAR is enabled or created implicitly and the manifest says `disabled` | Unit + Integration |
| S06 | D1 Auth/session | Expected `AuthExpiredError` occurs before a usable page exists | Medium | Preserve normal auth remediation and create no automatic bundle | Integration |
| S07 | D1 Auth/session | Same-process profile contention occurs against an in-memory registered owner | High | Metadata-only incident reports validated owner evidence without launching Chrome or reading untrusted raw text | Unit |
| S08 | D1 Auth/session | Cross-process Windows contender tries to read metadata whose byte 0 is kernel-locked | High | Lock byte is reserved at offset 0 and versioned bounded metadata begins at offset 1; legacy/unreadable metadata becomes `unavailable`, never a capture failure | Subprocess Integration (Windows) |
| S09 | D1 Auth/session | Lock file contains stale/dead PID metadata while the kernel lock still rejects acquisition | Critical | Kernel lock remains authoritative; no unlink, rename, PID kill, liveness claim, or automatic reclaim occurs | Unit + Subprocess Integration |
| S10 | D3 Selector drift | `FlowAppError` is raised while the page is responsive | High | Structural DOM and sensitive screenshot are staged before page/context close and the original exit code remains 31 | Playwright Integration |
| S11 | D3 Selector drift | One-time unknown banner from issue #369 covers the composer | High | Bundle records bounded visible overlay geometry, role, z-index, pointer-events, and ligatures without raw text; v0.43.0 does not guess a dismiss selector | Playwright Integration + opportunistic live |
| S12 | D3 Selector drift | Hostile DOM places prompt/token text in body, attributes, `aria-label`, and element text | Critical | Structural result validation rejects every non-allowlisted field; no raw DOM/text reaches automatic artifacts | Unit + Playwright Integration |
| S13 | D3 Selector drift | Full-page screenshot hangs or page rejects full-page capture | High | Per-artifact timeout falls back once to viewport, then records partial status without masking the original error | Playwright Integration |
| S14 | D4 Batch/resume | Fifty-row batch hits the same systemic UI failure concurrently/repeatedly | High | One fingerprinted staged bundle is written, suppression count is atomic, and the command never exceeds three distinct bundles | BDD + Integration |
| S15 | D4 Batch/resume | Failure occurs after remote submit but before local completion evidence | Critical | Diagnostics are side-effect-free: no navigation, retry, token mint, resubmit, queue transition, or change to reconciliation semantics | BDD + Integration |
| S16 | D5 Concurrency/Page pool | Recorder is attached repeatedly to a pooled page or a page is checked out twice | High | Every context/page event handler attaches at most once and detaches exactly once; no duplicate records or retained page objects | Playwright Integration |
| S17 | D5 Concurrency/Page pool | Page closes while console/network callbacks are still arriving and finalization begins | High | Detach first, freeze primitive snapshots, ignore late callbacks, and finalize without `Request`/`Response`/JS-handle retention | Playwright Integration |
| S18 | D5 Concurrency/Page pool | Request-start events never receive responses during a long video generation | High | Any timing correlation map is primitive-only and bounded/expired; 10,000 synthetic events remain within declared memory caps | Unit + soak-style Integration |
| S19 | D5 Concurrency/Page pool | Two failure boundaries concurrently capture the same fingerprint | High | A recorder-local async lock/state machine creates one directory and one final manifest; the loser only increments suppression | Integration |
| S20 | D5 Concurrency/Page pool | Cancellation lands during DOM capture, screenshot, context close, or manifest finalization | High | Original cancellation propagates after bounded cleanup; context/driver/lease teardown still runs and HAR is `possibly_incomplete` | Playwright Integration |
| S21 | D6 Data layer | Worker/HTTP/MCP error envelope is returned to a remote caller | High | Remote surfaces receive opaque incident id/status only; absolute local path and local artifact names are CLI-local unless an explicitly local trusted surface requests them | Unit + MCP/HTTP Integration |
| S22 | D6 Data layer | Incident metadata is added to worker failure persistence | Medium | Existing queue schema and prompt-redaction behavior remain unchanged; incident metadata uses the shared problem-details extension only | Unit |
| S23 | D7 Error propagation | DOM, screenshot, directory, JSON, retention, or manifest write raises | High | Capture records a sanitized `incident.capture_failed` event when possible and returns the original exception/traceback/exit code unchanged | Unit + Integration |
| S24 | D7 Error propagation | `FlowAppError` or `FlowAgentUiError` crosses CLI JSON, MCP, HTTP, and queue paths | High | Every surface reports the same `retryable: true`; no separate retryability lists drift | Unit + Integration |
| S25 | D7 Error propagation | Unexpected exception message contains a credential or prompt | Critical | Neither capture-failure logging nor unhandled-error logging persists raw exception text; only approved class/category/hash policy is used | Unit |
| S26 | D8 Cross-platform paths | Home/path contains spaces, non-ASCII, a Windows drive letter, or a long filename | High | Bundle creation/finalization succeeds with `pathlib` paths and UTF-8 JSON; no shell quoting is involved | Unit (Windows/POSIX) |
| S27 | D8 Cross-platform paths | Incidents root or a candidate child is a symlink, Windows junction, or reparse point resolving outside home | Critical | Resolve/validate before every create/delete; write nothing and delete nothing outside the canonical incidents root | Unit + Windows Integration |
| S28 | D8 Cross-platform paths | Another local user can read a new artifact between create and chmod | High | POSIX mode is restrictive from first creation; Windows documentation and tests rely on inherited per-user ACLs without false `chmod` claims | Unit + Integration |
| S29 | D9 Transport | Unknown image/video HTTP 400 includes arbitrary response keys and a raw message | High | Failure-path parser emits numeric/stable allowlisted discovery only and never stores the raw body/message/key names | Unit + Integration |
| S30 | D9 Transport | Response/status event arrives before the recorder or generation listener is attached | High | Recorder attaches before any navigation/submission that can produce relevant traffic; tests pin ordering | Playwright Integration |
| S31 | D9 Transport | Third-party request host/path embeds account IDs or signed tokens outside the query string | High | Persist only an allowlisted host category and canonical route; unknown hosts/routes become `other` with no raw host/path | Unit |
| S32 | D9 Transport | HAR path already exists before launch and context close fails | High | Snapshot pre-launch existence/size/mtime; report `complete` only when the current session demonstrably finalized it, otherwise `possibly_incomplete` | Unit + Playwright Integration |
| S33 | D10 Headed/headless | User manually closes Chrome or the page crashes during capture | High | Available primitive journals still finalize as partial; page-dependent artifacts fail independently; teardown and lease release complete | Playwright Integration |
| S34 | D10 Headed/headless | No display/browser context is available during partial setup | Medium | Metadata-only capture is best-effort and the original headed-environment/configuration error remains authoritative | Integration |
| S35 | D11 Boundaries | `GFLOW_CLI_INCIDENT_CAPTURE` is false or invalid | Medium | False performs no listener/bundle work; invalid values fail through existing typed settings validation before browser launch | Unit + CLI Integration |
| S36 | D11 Boundaries | Disk is full, incidents root is read-only, filename collides, or atomic replace fails | High | Original operational failure wins; capture status is failed/partial without recursive capture attempts | Unit + Integration |
| S37 | D11 Retention | Malicious/invalid manifest, symlink, junction, huge manifest, or unrelated directory is placed under incidents | Critical | Retention reads a bounded manifest, validates exact schema/ownership marker, and never deletes unknown or escaping content | Unit |
| S38 | D11 Retention | Repeated process kills leave incomplete directories without manifests | High | Recorder creates a private versioned pending marker; only stale recorder-owned pending directories are bounded/pruned after a conservative age, while unknown incomplete directories remain untouched | Unit + Integration |
| S39 | D11 Retention | Two commands prune/write under the same home concurrently | High | Active pending directories are never pruned; complete-bundle deletion is race-safe/idempotent and cannot escape the root | Multiprocess Integration |
| S40 | D11 Boundaries | Clock moves backward or two incidents share timestamp/correlation/fingerprint | High | Directory identity includes a collision-resistant component and uses exclusive creation; no overwrite occurs | Unit |
| S41 | D12 Observability | New stable event receives raw path, owner metadata, URL, exception text, or prompt through logging kwargs | Critical | Event constructors expose fixed allowlisted fields; canary tests prove structured logs contain no sensitive values | Unit |
| S42 | D12 Observability | Correlation context is absent in a worker task or changes between capture and finalization | High | Incident id is bound once at command/task boundary and every event/manifest uses that same id; missing context gets a generated id | Unit + Integration |
| S43 | D5 Long-running | One real Veo generation polls for an extended period while journals receive normal traffic | High | Valid MP4/provenance, bounded journals/maps, listener cleanup, no incident on success, and no leaked profile lease | E2E live (explicit paid approval) |

## Must-cover before merge (Critical + High)

1. **Secret boundary:** S01–S05, S12, S25, S29, S31, and S41 must have canary-based
   negative assertions across JSON, logs, filenames, CLI JSON, MCP, HTTP, and worker
   envelopes.
2. **Profile evidence without reclaim:** S07–S09 must cover same-process, POSIX
   subprocess, Windows offset-1 metadata, legacy/unreadable metadata, and malicious
   metadata while proving no destructive action occurs.
3. **UI evidence:** S10–S13 and S33 must prove capture-before-close, structural-only
   DOM, sensitive screenshot classification, timeout fallback, and partial capture.
4. **Batch/concurrency:** S14–S20 must prove fingerprint atomicity, the three-bundle
   cap, listener identity/detach, bounded primitive journals/maps, cancellation-safe
   teardown, and zero generation side effects.
5. **Machine surface privacy:** S21 must prove remote MCP/HTTP envelopes omit absolute
   local paths while local CLI output remains actionable.
6. **Error contract:** S23–S25 must prove the original exception/exit/traceback wins
   and retryability is shared across every output surface.
7. **Filesystem/retention:** S26–S28 and S36–S40 must prove Unicode/space handling,
   root containment including Windows reparse points, restrictive creation,
   recorder-owned pending cleanup, multiprocess pruning, bounded parsing, and
   collision-free exclusive creation.
8. **Network/HAR:** S29–S32 must prove listener ordering, allowlisted route discovery,
   unknown-host reduction, and current-session HAR completion detection.
9. **Correlation and long-running lifecycle:** S42 is an offline merge gate; S43 is a
   release gate requiring explicit paid-live approval. Without S43, release notes must
   state that long-running video lifecycle remains unverified.

## Deferred (Medium + Low — log as issues, not blockers)

1. S06 — expected auth-expiry no-capture behavior remains covered offline; no paid live
   failure needs to be induced.
2. S22 — no queue schema change is planned; broader SQLite resource-warning cleanup is
   separate lifecycle debt unless implementation introduces a new connection/callsite.
3. S34 — metadata-only capture without a display is offline-verifiable; this does not
   claim gflow generation supports headless/serverless execution.
4. S35 — invalid/disabled setting behavior is ordinary configuration coverage.
5. A multi-job paid availability soak is separate from S43. Define a budget, run count,
   and duration before making any unattended availability percentage claim.

## Suggested BDD scenarios (for `tests/features/`)

```gherkin
Feature: Private incident diagnostics

  Scenario: Capture failure preserves the operational error
    Given a Flow UI failure with exit code 31
    And the incident directory is read-only
    When the command handles the failure
    Then the command exits with code 31
    And no raw exception text is emitted
    And incident capture does not retry generation

  Scenario: A systemic batch failure is captured once
    Given a manifest with fifty rows
    And every row hits the same selector failure
    When the manifest runs with continue-on-error
    Then one incident bundle is staged for that fingerprint
    And the manifest records forty-nine suppressed occurrences
    And no more than three distinct bundles exist for the command

  Scenario: Profile contention reports evidence but never reclaims
    Given another process holds the selected profile lease
    When a generation command starts
    Then it exits with ProfileLockedError code 11 before Chrome launches
    And a metadata-only incident contains validated owner evidence
    And no lock file or process is deleted

  Scenario: Remote errors do not expose local incident paths
    Given an incident was captured under a home path containing a username
    When the failure is returned through MCP or HTTP
    Then the response contains an opaque incident id and status
    And it does not contain the absolute path or username

  Scenario: Cancellation leaves no browser or lease
    Given incident capture is staging DOM evidence
    When cancellation arrives during browser context close
    Then HAR state is possibly incomplete
    And the original cancellation propagates
    And the driver stops and the profile lease is released

  Scenario: Successful generation creates no incident
    Given incident capture is enabled
    When a generation completes successfully
    Then the media artifact is valid
    And no incident directory is created for the command
```

## Known-issues cross-reference

- **#369 — one-time top banner/modal:** v0.43.0 mitigates future diagnosis through
  S11 structural overlay evidence and a sensitive screenshot. It does not invent a
  selector or automatically snapshot localStorage/cookies. Raw HAR/storage inspection
  remains an explicit supervised investigation.
- **#370 — reported stale profile lock:** S07–S09 improve validated owner evidence and
  remediation. They explicitly do not accept stale metadata as proof, do not reclaim a
  kernel-rejected lock, and do not close the issue without a reproducible live cause.
- **#174 / FlowAgentUiError — full-page library/agentic cohort:** S10 captures actionable
  structural evidence and preserves retryability. Driving the new cohort remains out of
  scope.
- **#26 — release-notes overlay:** existing targeted dismissal remains. The incident
  recorder observes failures but does not add a generic click-any-overlay policy.
- **Unexplained image HTTP 400 observed 2026-07-22:** S29 turns the next occurrence into
  allowlisted discovery evidence without persisting the raw response. It remains open
  and is not claimed fixed by v0.43.0.

## Design amendments incorporated before implementation planning

The written design now incorporates all eight gaps found by this scenario pass:

1. lock-file byte 0 is reserved for the kernel lock and versioned bounded owner
   metadata begins at offset 1, with legacy/unreadable fallback and no reclaim;
2. incident presentation is split into local and remote views: absolute path/artifact
   names are local-only, while MCP/HTTP/worker surfaces receive opaque id/status;
3. arbitrary top-level response key names and raw unknown hosts/routes are replaced by
   allowlisted classifications and counts;
4. request timing is primitive-only/bounded and capture/finalize uses one recorder
   state lock with frozen post-detach snapshots;
5. HAR completion compares pre-launch state with post-close state rather than mere
   existence;
6. a locked, versioned pending marker makes recorder-owned crash leftovers safely
   distinguishable and bounded without touching unknown content; and
7. incident directories use collision-resistant exclusive creation with explicit
   symlink, Windows junction, and reparse-point containment tests; and
8. profile-lock owner evidence is private/local only, while remote RFC 9457, MCP,
   HTTP, worker/queue, and structured-log outputs omit profile paths, lock paths, raw
   OS errors, and owner values.
