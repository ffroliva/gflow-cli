# Private Incident Diagnostics — Design

**Date:** 2026-07-22
**Target release:** v0.43.0
**Status:** Architecture amended by scenario analysis; written design awaiting review
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

Human-readable local CLI failures print one additional sentence containing the bundle
path and `Review before sharing; sensitive artifacts may contain account or media
data.` Local CLI JSON carries an optional `incident` object:

```json
{
  "id": "<correlation-id>-<fingerprint>",
  "path": "<absolute local path>",
  "capture_status": "complete|partial|failed",
  "artifacts": ["manifest.json", "ui.json", "network.json", "browser.json"]
}
```

The shared RFC 9457 extension used by MCP, HTTP, and worker/queue surfaces is
remote-safe and contains only `id` and `capture_status`. It never exposes an absolute
local path, local username, artifact filenames, profile path, lock path, or lease-owner
evidence. The local CLI renderer enriches that reference with `path` and `artifacts`
from the in-process capture result. This is error metadata, not a new generation
parameter, so CLI/MCP input-schema symmetry is unchanged.

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
- presence/count fields for profile/project/media inputs and, only where equality
  correlation inside one command is required, a per-command HMAC digest whose random
  key is never persisted;
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
  path segments replaced by stable placeholders or per-command HMAC identities;
- a classified page title (`flow`, `flow_app_crash`, or `other`) plus title length,
  never the raw unknown title or an unsalted digest of it;
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
- booleans for known top-level keys plus an unknown-key count, never arbitrary key
  names;
- message length, never the raw message or an unsalted digest of it; and
- a boolean for known WAF/content-policy signatures.

Allowlisted Flow/Google hosts are reduced to stable host categories and canonical
routes. Unknown hosts/routes become `other`; their raw host/path is not persisted.
No raw message, header, cookie, query string, post data, response body, or signed URL
enters `network.json`. Raw payload inspection remains the explicit HAR escalation
path.

### 5.4 `browser.json`

Bounded journals:

- up to 100 console warning/error records;
- up to 50 page-error records; and
- up to 100 failed-request records shared with the network journal.

Console arguments are never inspected or serialized. Messages/stacks are stored as
class/category and length only, with an allowlisted source category/canonical route
and line/column where available. Known Flow app-crash text is represented by a
boolean classifier, not a raw string. No unsalted digest of low-entropy console,
title, prompt, account, or error text is persisted.

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
copied into the incident directory. Before launch, the recorder snapshots whether
the configured file already exists plus its size and modification identity. After
context close it reports `complete` only when the current session demonstrably
created or changed that file; mere post-close existence is insufficient.

Because Playwright finalizes HAR during `BrowserContext.close()`, cancellation,
process kill, timeout, or force-stop may leave it absent or incomplete. The CLI must
report `possibly_incomplete` rather than imply guaranteed capture. Existing warnings
that HAR contains live secrets remain mandatory.

## 6. Architecture

### 6.1 New module

Create `src/gflow_cli/diagnostics.py` with a session-scoped `IncidentRecorder`.
The module owns:

- bounded immutable event records and `deque(maxlen=...)` journals;
- a bounded primitive-only request-timing map with expiry;
- safe URL normalization and per-command keyed identifier reduction;
- listener attach/detach bookkeeping;
- a recorder-local capture/finalize state lock;
- structural DOM capture;
- exclusive bundle creation, recorder-owned pending markers, atomic manifest
  finalization, and permissions;
- per-command fingerprint suppression;
- retention pruning; and
- best-effort capture timeouts.

It does not own RFC 9457 exception definitions, structured logging configuration,
or transport behavior.

### 6.2 Ownership and lifecycle

`FlowApiClient` owns one recorder because it already owns the persistent browser
context, pooled pages, teardown ordering, settings, and profile lease.

1. Construct the recorder after settings and correlation context are available but
   before profile-lease acquisition, so contention can produce a metadata-only
   incident without launching Chrome. Copy the correlation id once; if absent,
   generate one once and use it for every event, directory, and manifest in the
   command/task.
2. Attach context-level request/response/request-failure listeners immediately after
   context launch and before any navigation/submission that can produce relevant
   traffic.
3. Attach page console/page-error listeners to every pooled page and to any new page
   observed during the session.
4. Pass the recorder to `UiAutomationTransport` through an optional typed field on
   `TransportSetup`; other transports ignore it.
5. Image/video client boundaries and the manifest-batch transport boundary invoke
   `capture_failure` while the page is still alive. This stages artifacts and returns
   the incident path without finalizing the manifest.
6. Partial setup invokes metadata-only capture when no page exists.
7. Stop accepting events, detach listeners, and freeze primitive snapshots before
   context close. Late callbacks become no-ops.
8. Close the context, determine HAR completion state, and atomically finalize all
   staged manifests, including `possibly_incomplete` when close fails.
9. Stop the driver and release the profile lease in the existing cancellation-safe
   order. Manifest finalization is best-effort and never masks a close/driver/lease
   failure.

Listener callbacks perform synchronous metadata extraction and deque/map mutation
only. Request duration correlation uses a primitive key, never a retained Playwright
`Request`/`Response`, and is capped at 256 in-flight entries with a ten-minute expiry;
duration is omitted when safe correlation is unavailable. Callbacks never perform
file I/O, response-body reads, DOM evaluation, navigation, reloads, or additional
network calls. `capture_failure`, suppression updates, and finalization serialize
through one recorder-local async lock so concurrent boundaries cannot create or
overwrite the same incident.

Capture is observation-only. Apart from read-only DOM evaluation and screenshot
capture, it never clicks, types, navigates, reloads, mints a token, submits or retries
generation, changes a queue checkpoint, downloads media, or mutates Flow state.

### 6.3 Existing UI diagnostic consolidation

`capture_ui_diagnostics` becomes a thin compatibility wrapper over
`IncidentRecorder.capture_failure`, or its structural JavaScript is moved into the
new module. There must be one DOM/screenshot engine, not two competing artifact
formats. Existing error messages that name a diagnostic path continue to do so.

### 6.4 Profile lease evidence

`ProfileLease` already writes PID, observed process-start proxy, profile name, and
owner token. The on-disk format changes so byte 0 is a reserved sentinel covered by
the kernel lock, while bytes 1–4095 contain versioned bounded JSON. A contender never
reads the locked byte. On same-process contention it uses the registered owner's
in-memory metadata; on cross-process contention it reads at most 4095 bytes starting
at offset 1 from the already-open descriptor, validates the version/schema and
primitive types, and makes private diagnostic owner evidence available to the
incident recorder and local human formatter:

- PID;
- observed start time;
- per-command HMAC profile identity;
- per-command HMAC owner-token identity, never the raw token.

The evidence is carried on a private typed exception attribute and is never emitted by
`to_problem_details()`, MCP, HTTP, worker/queue, or structured logs. The stable problem
detail classifies contention as same-process or cross-process without embedding the
canonical profile path, lock path, raw OS error, or owner values. The private manifest
and local human output explicitly state that the kernel lock is authoritative and the
metadata can be stale. Existing pre-v1 files whose metadata starts in locked byte 0
may be unreadable on Windows; they report owner evidence as unavailable. No reclaim,
unlink, rename, PID kill, or liveness conclusion is implemented.

### 6.5 Retryable contract correction

`FlowAppError` and `FlowAgentUiError` are documented and presented to humans as
retryable, but `json_output._RETRYABLE` currently omits both and emits
`retryable: false`. v0.43.0 adds them to the shared retryable classification and
locks CLI JSON, MCP, and queue error envelopes to the same result.

## 7. Privacy and filesystem safety

- The incidents root resolves strictly beneath `GFLOW_CLI_HOME`; any escape,
  symlink, Windows junction, or other reparse-point root/target raises a best-effort
  capture warning and writes nothing.
- Incident directories include a collision-resistant random component and are
  created exclusively; a clock rollback or duplicate correlation/fingerprint cannot
  overwrite an existing bundle.
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
- In-flight request timings: 256, ten-minute expiry.
- Bundles per command: 3.
- Failure-capture wall-clock budget: 8 seconds total, with shorter per-artifact
  bounds.
- Complete-bundle retention: at most 50 directories and at most 250 MiB.
- Recorder-owned pending retention: at most 20 directories and at most 100 MiB.

Each incident directory is created with a versioned `.pending` marker whose advisory
lock is held by the recorder until finalization. The marker is bounded to 4 KiB and
contains only fixed ownership/schema fields. `manifest.json` is written last; the
pending lock is then released and the marker removed. If a process dies, the marker
remains but its lock becomes acquirable.

At recorder startup, take a non-blocking incidents-root retention lock; if another
process owns it, skip pruning. Under that lock:

- prune oldest complete bundles until both complete-bundle limits hold, but only for
  direct child directories with a bounded (maximum 64 KiB), valid
  `gflow-incident-v1` manifest and an exact allowlisted artifact set;
- never follow symlinks, junctions, reparse points, or manifest paths while measuring
  or deleting;
- when a pending marker exists, first acquire its lock non-blocking; if acquisition
  fails, treat the directory as active and never inspect, finalize, or prune it;
- after acquiring an inactive marker lock, treat a valid manifest as complete and the
  marker as a crash-left stale marker;
- prune recorder-owned pending directories only after their marker lock can be
  acquired/held (proving no recorder currently owns them) and they are older than 24
  hours, or oldest-first when the pending count/byte cap is exceeded; and
- leave unknown directories, unrecognized markers, locked/active pending bundles,
  oversized/invalid manifests, and escaping content untouched.

Emit `incident.retention_pruned` with counts/bytes only. Retention failure is
best-effort and never recursively creates another incident.

## 9. Error handling and observability

Stable events:

- `incident.capture_started`;
- `incident.capture_completed` (`status`, artifact kinds, duration only);
- `incident.capture_failed`;
- `incident.capture_suppressed`;
- `incident.retention_pruned`; and
- `profile_lease.owner_evidence_read` (valid/invalid only; no owner values in logs).

Each event is emitted through a fixed-field constructor; callers cannot add raw URLs,
paths, owner metadata, exception text, prompts, or browser objects as arbitrary
logging kwargs.

The original exception, problem details, exit code, and traceback chain always win.
Incident capture is shielded/bounded only long enough to write evidence; it cannot
prevent the existing cancellation-safe browser teardown or lease release.

## 10. Testing strategy

### 10.1 Unit tests

- URL query/fragment removal, allowlisted host/route reduction, and per-command HMAC
  identifier equality without persisted keys.
- Structural DOM result validation rejects unexpected/raw fields.
- Hostile token, cookie, signed-URL, prompt, email, ANSI, and Unicode fixtures do
  not appear in automatic JSON, logs, filenames, local CLI JSON, or remote error
  envelopes.
- Arbitrary upstream key names and unknown hosts/routes reduce to known booleans,
  counts, and `other` rather than raw strings.
- Console/page-error/network rings enforce their exact caps.
- The primitive in-flight request map enforces its 256-entry cap/expiry and retains
  no Playwright objects under 10,000 synthetic events.
- Duplicate fingerprints create one staged bundle; finalization records the complete
  suppression count atomically under concurrent capture calls.
- Explicit config allowlist cannot serialize API keys, daemon tokens, storage URIs,
  prompts, profile paths, or HAR paths.
- Bundle paths handle spaces and Unicode on Windows/POSIX.
- Symlinked/junction/reparse-point incident roots and children are refused.
- POSIX modes are `0700`/`0600`; Windows tests assert behavior/documentation without
  making a false DACL claim.
- Exclusive random-suffixed directory creation and atomic manifest-last behavior
  distinguish collision, complete, and pending bundles.
- Retention deletes only valid direct-child complete bundles or unlocked
  recorder-owned pending bundles, respects both count/byte limits, parses bounded
  manifests, and skips when the retention lock is held.
- Capture I/O, screenshot, DOM, and timeout failures preserve the original error.
- HAR state reports disabled/complete/possibly-incomplete honestly when the target
  file was absent, pre-existing, unchanged, changed, or close failed.
- Lease metadata offset-1 parsing is bounded, version/schema-validated, keyed, and
  never drives reclaim behavior; legacy/unreadable files degrade to unavailable.
- Profile-lock owner evidence remains private/local: RFC 9457, MCP, HTTP, queue, and
  structured-log outputs omit profile paths, lock paths, raw OS errors, and owner
  values.
- `FlowAppError` and `FlowAgentUiError` are retryable across CLI JSON, MCP, and
  queue envelopes.
- Local CLI incident output includes the path while remote MCP/HTTP/worker envelopes
  contain only opaque id/status.

### 10.2 Playwright/transport integration tests

- Listeners attach before relevant navigation/submission, once to every pooled/new
  page, and detach/freeze on normal teardown, partial setup, repeated enter/exit,
  and cancellation; late callbacks are ignored.
- Normal successful generation writes no incident bundle.
- Relevant image/video exceptions capture before page/context close.
- Expected usage/content-policy/auth failures do not capture.
- Manifest image batch writes at most three distinct bundles and suppresses a
  repeated systemic failure.
- Context listener callbacks retain no `Request`, `Response`, `ConsoleMessage`, or
  JS-handle objects.
- Cancellation during DOM, screenshot, context close, and finalization preserves the
  original cancellation while releasing driver and profile lease.
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

1. all 9 Critical and 30 High scenarios in
   `docs/superpowers/plans/2026-07-22-private-incident-diagnostics/SCENARIO.md`
   are mapped to implementation tasks and pass their declared tests;
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
