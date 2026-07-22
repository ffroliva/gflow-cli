# Private Incident Diagnostics — Design

**Date:** 2026-07-22
**Target release:** v0.43.0
**Status:** Architecture approved; written design awaiting review
**Predict verdict:** GO — confidence 8.3/10 after the privacy mitigations below

## 1. Problem

gflow-cli already has useful but fragmented diagnostics:

- opt-in raw Playwright HAR capture through `GFLOW_CLI_HAR_PATH`;
- structured logs with a per-command correlation id;
- targeted screenshots for selected UI failures;
- picker-specific bounded DOM dumps; and
- `capture_ui_diagnostics`, which writes a small mode-switch DOM signature and
  a full-page screenshot.

These pieces proved valuable on 2026-07-22: the mode-switch diagnostic identified
a real Flow client-side crash that had previously looked like selector drift. They
do not yet form a robust incident record, however. HAR must be enabled before the
incident, browser console and request failures are not journaled, most failure
classes do not capture DOM state, and profile-lock contention hides owner metadata
that is already present in the lock file.

The current DOM signature is also not sanitized: it includes the raw URL, page
title, and a body-text preview. A screenshot can contain account identity, prompts,
reference media, or generated media. Raw HAR contains live cookies, auth headers,
reCAPTCHA tokens, request bodies, and signed URLs.

## 2. Goal

On a relevant operational failure, automatically write a bounded, private incident
bundle that answers:

1. Which command/version/lifecycle phase failed?
2. What structural UI state was Flow rendering?
3. What recent browser/network failures preceded it?
4. Was sensitive raw HAR enabled, and did it finalize cleanly?
5. For profile contention, which recorded owner appears to hold the lease?

The bundle must never mask or reclassify the original failure, must add negligible
normal-path overhead, and must not claim to be safe to share.

## 3. Non-goals

- Automatically enabling raw HAR.
- Capturing raw HTML, `outerHTML`, form/input values, storage values, console
  arguments, request/response headers, cookies, post bodies, prompts, or signed
  URLs in the automatic tier.
- Uploading telemetry or incident artifacts anywhere.
- Automatically attaching artifacts to GitHub issues.
- Fixing Flow's intermittent HTTP 400, one-time banner, app crash, agentic cohort,
  or the reported profile-lock incident in this release.
- Deleting a lock, killing a recorded PID, or treating lock metadata as authority.
- Adding a new CLI flag, command, exit code, database schema, or MCP tool field.
- Replacing the existing RFC 9457 error taxonomy or structured log stream.

## 4. User contract

### 4.1 Default behavior

`GFLOW_CLI_INCIDENT_CAPTURE` is a boolean setting, default `true`. When enabled,
relevant failures produce an incident bundle beneath:

```text
<GFLOW_CLI_HOME>/incidents/<YYYY-MM-DD>/<UTC timestamp>-<correlation-id>-<fingerprint>/
```

Human-readable failures print one additional sentence containing the bundle path
and `Review before sharing; sensitive artifacts may contain account or media data.`
Machine-readable CLI JSON carries an optional `incident` object:

```json
{
  "id": "<correlation-id>-<fingerprint>",
  "path": "<absolute local path>",
  "capture_status": "complete|partial|failed",
  "artifacts": ["manifest.json", "ui.json", "network.json", "browser.json"]
}
```

The MCP/worker error envelope carries the same optional object through the shared
problem-details mapping. This is error metadata, not a new generation parameter,
so CLI/MCP input-schema symmetry is unchanged.

Setting `GFLOW_CLI_INCIDENT_CAPTURE=false` disables the automatic bundle. Existing
ad-hoc screenshots outside the replaced mode-switch path and explicitly requested
raw HAR retain their current behavior.

### 4.2 Failures that trigger capture

Automatic capture is limited to failures where runtime evidence can change the
diagnosis:

- `FlowAppError`;
- `FlowAgentUiError` and `UiModeUnavailableError`;
- `UiSelectorDriftError`;
- `TransportTimeoutError` and `BrowserSessionClosedError`;
- `WireFormatError`, `WafRejectionError`, and `NetworkError` on a live Flow path;
- unexpected exceptions while a browser page is still available; and
- `ProfileLockedError` as a metadata-only incident.

Usage/config validation, expected `ContentPolicyError`, and ordinary
`AuthExpiredError` do not create a bundle. They already have deterministic operator
remediation and DOM capture adds no value.

At most three bundles are written per command. Repeated failures with the same
exception class, stable problem type, route name, and lifecycle phase share one
fingerprint; later duplicates increment a suppressed count in the manifest.

## 5. Bundle format

The bundle uses schema id `gflow-incident-v1`. Evidence files are staged while the
page is alive. `manifest.json` is finalized after browser-context close establishes
the HAR state, written atomically, and is the marker that a directory is a complete
gflow-created bundle. If context close raises, teardown still finalizes the manifest
with `possibly_incomplete`; abrupt process termination may leave an incomplete
directory without a manifest.

### 5.1 `manifest.json`

Allowlisted fields only:

- schema id and incident id;
- UTC timestamps;
- CLI version, Python version, OS family, and browser engine/channel;
- command path, transport name, headed/headless state, locale, requested UI mode,
  model alias, aspect, and count;
- hashed profile/project/media identifiers where present;
- exception class, RFC 9457 problem type, exit code, corrected retryable flag,
  sanitized route name, and lifecycle phase;
- artifact paths and sensitivity classifications;
- capture status per artifact;
- HAR state: `disabled`, `pending_flush`, `complete`, or `possibly_incomplete`;
- duplicate-suppression count; and
- a no-upload/no-auto-share notice.

The implementation must build this object from an explicit allowlist. It must not
serialize `Settings.model_dump()` because settings include API keys, daemon tokens,
storage URIs, profile paths, and HAR paths.

### 5.2 `ui.json`

Structural evidence only:

- query/fragment-free `scheme + host + canonical route`, with identifier-bearing
  path segments replaced by stable placeholders or hashes;
- a classified page title (`flow`, `flow_app_crash`, or `other`) plus title length
  and SHA-256 hash, never the raw unknown title;
- unique Material-Symbol ligatures and total count;
- known composer signal booleans (`crop_*`, Slate textbox, Agent toggle, media
  library markers);
- tag/role counts;
- bounded visible modal/overlay records containing tag, role, `aria-modal`,
  visibility, bounding rectangle, computed `z-index`, `pointer-events`, and
  Material-Symbol ligatures inside the element; and
- viewport dimensions and current scroll position.

It must not contain raw body text, element text, `aria-label`, HTML, input values,
storage values, or element attributes outside the allowlist. The existing
`bodyTextPreview` field is removed from the generalized capture.

### 5.3 `network.json`

A fixed-size ring journal containing at most 100 records:

- monotonic/UTC timestamp;
- request method;
- query-free host/canonical route, with identifier-bearing path segments replaced;
- resource type;
- response status or request-failure category;
- duration when derivable; and
- a stable route classifier for known Flow endpoints.

Listeners never read response bodies on the hot path. At a captured unexpected
non-2xx Flow response, failure-path code may parse the response already retained by
the generation listener and include only allowlisted error fields:

- numeric error code;
- stable status/reason enum;
- sorted top-level key names;
- message length and SHA-256 hash; and
- a boolean for known WAF/content-policy signatures.

No raw message, header, cookie, query string, post data, response body, or signed
URL enters `network.json`. Raw payload inspection remains the explicit HAR
escalation path.

### 5.4 `browser.json`

Bounded journals:

- up to 100 console warning/error records;
- up to 50 page-error records; and
- up to 100 failed-request records shared with the network journal.

Console arguments are never inspected or serialized. Messages/stacks are stored as
class/category, length, and SHA-256 hash, with a query-free, identifier-scrubbed
source route and line/column where available. Known Flow app-crash text is
represented by a boolean classifier, not a raw string.

### 5.5 `sensitive/screenshot.png`

A screenshot is inherently sensitive and is never called sanitized. It is captured
only for UI-state failures (`FlowAppError`, agent/UI-mode errors, selector drift,
and UI timeouts), not for WAF, network, wire-format, auth, or profile-lock failures.

The screenshot is full-page only where the page is responsive; capture is bounded
and falls back to the viewport. The manifest marks it `sensitive`, and operator
output instructs the user to review it before sharing. No export or upload behavior
is added in v0.43.0.

### 5.6 Raw HAR

`GFLOW_CLI_HAR_PATH` remains unchanged and explicitly opt-in. Raw HAR is never
copied into the incident directory. The incident manifest records only whether HAR
was requested and whether the expected file appeared after graceful context close.

Because Playwright finalizes HAR during `BrowserContext.close()`, cancellation,
process kill, timeout, or force-stop may leave it absent or incomplete. The CLI must
report `possibly_incomplete` rather than imply guaranteed capture. Existing warnings
that HAR contains live secrets remain mandatory.

## 6. Architecture

### 6.1 New module

Create `src/gflow_cli/diagnostics.py` with a session-scoped `IncidentRecorder`.
The module owns:

- bounded immutable event records and `deque(maxlen=...)` journals;
- safe URL/identifier normalization;
- listener attach/detach bookkeeping;
- structural DOM capture;
- bundle writing, atomic manifest finalization, and permissions;
- per-command fingerprint suppression;
- retention pruning; and
- best-effort capture timeouts.

It does not own RFC 9457 exception definitions, structured logging configuration,
or transport behavior.

### 6.2 Ownership and lifecycle

`FlowApiClient` owns one recorder because it already owns the persistent browser
context, pooled pages, teardown ordering, settings, and profile lease.

1. Construct the recorder after settings and correlation context are available.
2. Attach context-level request/response/request-failure listeners after context
   launch.
3. Attach page console/page-error listeners to every pooled page and to any new page
   observed during the session.
4. Pass the recorder to `UiAutomationTransport` through an optional typed field on
   `TransportSetup`; other transports ignore it.
5. Image/video client boundaries and the manifest-batch transport boundary invoke
   `capture_failure` while the page is still alive. This stages artifacts and returns
   the incident path without finalizing the manifest.
6. Partial setup invokes metadata-only capture when no page exists.
7. Detach listeners and flush the bounded incident journal before context close.
8. Close the context, determine HAR completion state, and atomically finalize all
   staged manifests, including `possibly_incomplete` when close fails.
9. Stop the driver and release the profile lease in the existing cancellation-safe
   order. Manifest finalization is best-effort and never masks a close/driver/lease
   failure.

Listener callbacks perform synchronous metadata extraction and deque append only.
They never perform file I/O, response-body reads, DOM evaluation, navigation,
reloads, or additional network calls.

### 6.3 Existing UI diagnostic consolidation

`capture_ui_diagnostics` becomes a thin compatibility wrapper over
`IncidentRecorder.capture_failure`, or its structural JavaScript is moved into the
new module. There must be one DOM/screenshot engine, not two competing artifact
formats. Existing error messages that name a diagnostic path continue to do so.

### 6.4 Profile lease evidence

`ProfileLease` already writes PID, observed process-start proxy, profile name, and
owner token. On kernel-lock contention it reads at most 4 KiB of metadata from the
already-open descriptor, validates the JSON schema and primitive types, and adds
diagnostic owner evidence to `ProfileLockedError`:

- PID;
- observed start time;
- hashed profile name;
- lock path beneath `GFLOW_CLI_HOME`; and
- owner-token hash prefix, never the raw token.

The error and manifest explicitly state that the kernel lock is authoritative and
the metadata can be stale. No reclaim, unlink, PID kill, or liveness conclusion is
implemented.

### 6.5 Retryable contract correction

`FlowAppError` and `FlowAgentUiError` are documented and presented to humans as
retryable, but `json_output._RETRYABLE` currently omits both and emits
`retryable: false`. v0.43.0 adds them to the shared retryable classification and
locks CLI JSON, MCP, and queue error envelopes to the same result.

## 7. Privacy and filesystem safety

- The incidents root resolves strictly beneath `GFLOW_CLI_HOME`; any escape or
  symlinked root/target raises a best-effort capture warning and writes nothing.
- POSIX directories are created with mode `0700` and files with `0600` from first
  creation, using exclusive creation where practical. Post-write chmod remains
  defense in depth.
- Windows documentation states that protection relies on inherited per-user ACLs;
  it does not claim that `chmod(0600)` creates a restrictive DACL.
- Artifact JSON is written as data only. Any future viewer must escape it and use a
  restrictive CSP because Flow DOM strings are untrusted.
- Capture never uploads, opens a network connection, or invokes another process.
- A failure to capture emits `incident.capture_failed` with only exception class,
  artifact kind, and correlation id. It never logs the raw capture exception text.

## 8. Bounds and retention

- Network records: 100.
- Console records: 100.
- Page errors: 50.
- Bundles per command: 3.
- Failure-capture wall-clock budget: 8 seconds total, with shorter per-artifact
  bounds.
- Global retention: at most 50 complete incident directories and at most 250 MiB.

At recorder startup, prune oldest complete bundles until both global limits hold.
Pruning is restricted to direct child directories beneath the resolved incidents
root whose atomically written manifest has schema `gflow-incident-v1`. Unknown
directories, symlinks, incomplete directories, and invalid manifests are never
deleted. Emit `incident.retention_pruned` with counts/bytes only.

## 9. Error handling and observability

Stable events:

- `incident.capture_started`;
- `incident.capture_completed` (`status`, artifact kinds, duration only);
- `incident.capture_failed`;
- `incident.capture_suppressed`;
- `incident.retention_pruned`; and
- `profile_lease.owner_evidence_read` (valid/invalid only; no owner values in logs).

The original exception, problem details, exit code, and traceback chain always win.
Incident capture is shielded/bounded only long enough to write evidence; it cannot
prevent the existing cancellation-safe browser teardown or lease release.

## 10. Testing strategy

### 10.1 Unit tests

- URL query/fragment removal and identifier hashing.
- Structural DOM result validation rejects unexpected/raw fields.
- Hostile token, cookie, signed-URL, prompt, email, ANSI, and Unicode fixtures do
  not appear in sanitized JSON.
- Console/page-error/network rings enforce their exact caps.
- Duplicate fingerprints create one staged bundle; finalization records the complete
  suppression count atomically.
- Explicit config allowlist cannot serialize API keys, daemon tokens, storage URIs,
  prompts, profile paths, or HAR paths.
- Bundle paths handle spaces and Unicode on Windows/POSIX.
- Symlinked incident roots/children are refused.
- POSIX modes are `0700`/`0600`; Windows tests assert behavior/documentation without
  making a false DACL claim.
- Atomic manifest-last behavior distinguishes complete/incomplete bundles.
- Retention deletes only valid direct-child bundles and respects count/byte limits.
- Capture I/O, screenshot, DOM, and timeout failures preserve the original error.
- HAR state reports disabled/complete/possibly-incomplete honestly.
- Lease metadata parsing is bounded, schema-validated, hashed, and never drives
  reclaim behavior.
- `FlowAppError` and `FlowAgentUiError` are retryable across CLI JSON, MCP, and
  queue envelopes.

### 10.2 Playwright/transport integration tests

- Listeners attach once to every pooled/new page and detach on normal teardown,
  partial setup, repeated enter/exit, and cancellation.
- Normal successful generation writes no incident bundle.
- Relevant image/video exceptions capture before page/context close.
- Expected usage/content-policy/auth failures do not capture.
- Manifest image batch writes at most three distinct bundles and suppresses a
  repeated systemic failure.
- Context listener callbacks retain no `Request`, `Response`, `ConsoleMessage`, or
  JS-handle objects.
- Full-page screenshot failure falls back to viewport without masking the error.
- Existing mode-switch diagnostics use the new bundle rather than duplicating a
  screenshot/JSON pair.

### 10.3 Live verification

Before release:

1. Open a real authenticated Flow page and deliberately invoke the recorder without
   submitting generation. Confirm a real structural DOM/network bundle, permissions,
   no raw HAR, no raw prompt/account text in JSON, and a locally reviewable sensitive
   screenshot.
2. Run one real T2I generation with incident capture enabled. Confirm its current
   credit behavior in Flow before submission and obtain operator approval if it will
   consume credits. Confirm success, valid image artifact, expected dimensions/magic
   bytes, and no incident directory for the successful command.
3. Hold a real profile lease in one process and attempt the same profile from a
   second process. Confirm exit 11, metadata-only incident, recorded owner PID, no
   Chrome launch, and no reclaim.
4. With explicit operator approval, run one paid `veo-lite` T2V. Confirm submission,
   polling, valid MP4, provenance, listener cleanup, and no incident on success.

This live matrix validates one long-running video lifecycle; it does not establish a
multi-job soak or an unattended availability percentage. Any broader stability claim
requires a separately approved, budgeted soak with a declared run count and duration.

Record the five-layer evidence in `docs/LIVE_VERIFICATION_v0.43.0.md` and update
`docs/INDEX.md`. If the paid video gate is not approved, record it explicitly as an
unverified release risk; do not silently omit it.

## 11. Documentation

Update:

- `.env.template` and `docs/CONFIGURATION.md` for
  `GFLOW_CLI_INCIDENT_CAPTURE`;
- `docs/DEBUGGING.md` with bundle layout, capture triggers, HAR escalation, and
  review-before-sharing instructions;
- `docs/SECURITY.md` with automatic-vs-sensitive artifact boundaries, POSIX modes,
  Windows ACL truth, and no-upload guarantee;
- `docs/ARCHITECTURE.md` with recorder ownership and teardown ordering;
- `KNOWN_ISSUES.md` with issues #369 and #370 plus the unexplained HTTP 400;
- `CHANGELOG.md` `[Unreleased]` with the private incident bundle, retryable-contract
  correction, and lease-owner evidence; and
- `docs/INDEX.md` plus `docs/LIVE_VERIFICATION_v0.43.0.md` at release time.

Also correct existing truth-source drift discovered during assessment:

- the active root plan still labels MCP as unshipped;
- image-batch documentation says generations run in parallel while the transport is
  strictly serial;
- image-batch documentation contradicts the CLI's default continue-on-error mode;
  and
- the 2026-07-22 live-attempt ledger count must be reconciled before it is cited as
  a reliability metric.

## 12. Alternatives considered

### Automatic raw HAR for every generation — rejected

Highest forensic fidelity, but it persistently writes live credentials and payloads,
adds substantial disk usage, depends on graceful context close, and is unsafe as a
default.

### Fully opt-in diagnostics — rejected

Lowest default risk, but repeats today's failure: the evidence must be enabled before
an unpredictable incident. It is inadequate for one-time banners, cohort flaps, and
rare app crashes.

### Playwright tracing/full HTML snapshot — rejected

Trace archives and raw HTML are larger, contain the same or greater sensitive data as
HAR, and add unnecessary implementation/viewer complexity. Bounded structural data is
sufficient for first-pass diagnosis; raw HAR remains the explicit escalation path.

## 13. Release gate and timing

This work and the already-unreleased UI-drift engine, clean cohort classification,
`FlowAppError`, and standalone-transport guard form a coherent **v0.43.0** operator
reliability release. It is a MINOR bump because it adds an operator-visible incident
capability, not merely internal fixes.

Do not cut the release until:

1. all Critical/High scenarios from the follow-on scenario analysis are covered;
2. the Impeccable Routine is green; an aggregate packaging failure is treated as a
   lifecycle/resource regression and is not waived merely because the packaging test
   passes in isolation;
3. code review, simplification/over-engineering review, and SonarCloud report zero new
   issues;
4. the live matrix in §10.3 is recorded honestly;
5. documentation review passes;
6. `docs/LIVE_VERIFICATION_v0.43.0.md` and the INDEX entry are committed; and
7. the user explicitly approves the signed-tag push that publishes to PyPI and GitHub.

The unexplained HTTP 400, one-time banner, and profile-lock root cause do not block
v0.43.0 if this design is fully implemented and their open status is documented. The
release exists specifically to make the next occurrence actionable. Root-cause fixes
remain separate, evidence-gated follow-ups.

## 14. Baseline evidence

The isolated `feature/incident-diagnostics` worktree was created from current
`origin/develop` (`10365b3`). The full non-live baseline produced:

```text
2564 passed, 13 skipped, 64 deselected, 1 failed
```

The sole failure was
`tests/data/test_packaging.py::test_built_distributions_contain_sql_migrations`.
Although the exact test passed in isolation, systematic bisection proved this was not
a random packaging flake. Standalone `UiAutomationTransport` and
`EvaluateFetchTransport` setup tests acquired real `ProfileLease` handles and omitted
their required teardown. On Windows, the later Hatchling sdist traversal then failed
while reading a file beneath the repo-local test tree.

The test-only lifecycle correction is commit `5a75043`: every standalone setup test
now closes its fake context/driver and releases its lease in `finally`. Evidence:

- the individual setup-plus-packaging red reproducers became `3 passed`;
- all targeted setup/teardown tests followed by packaging became `19 passed`;
- the complete transport block followed by packaging became
  `577 passed, 1 skipped`; and
- the exact post-fix coverage gate became
  `2564 passed, 4 skipped, 74 deselected` at `91.39%` coverage.

An earlier baseline attempt overlapped a pytest child that survived its parent shell's
timeout and temporarily held a test `ProfileLease`. The recorded owner PID later exited;
no Chrome or pytest process remained. This is not evidence that a stale lock file alone
blocks acquisition; it is direct support for surfacing owner metadata and never
auto-deleting the lock.
