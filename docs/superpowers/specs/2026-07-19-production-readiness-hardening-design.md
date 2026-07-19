# Production-Readiness Hardening Design

**Date:** 2026-07-19  
**Status:** Pending written-spec review  
**Branch:** `chore/production-readiness-hardening`  
**Base:** `origin/develop` at `03a234d`

## Goal

Make the demonstrated local execution, paid-generation, queue, and browser-lifecycle paths
release-ready within gflow-cli's documented constraints: a private Flow API, headed real Chrome,
account-specific UI cohorts, and reCAPTCHA/WAF scoring.

The result must prevent duplicate paid execution, fail safely under profile contention and
cancellation, remove public surfaces that never worked, preserve working image batch generation,
and finish with dated local and live evidence.

## Baseline

The isolated worktree starts clean on the latest `origin/develop`. Before this design was written:

- repository hygiene, documentation links, Ruff lint/format, and strict Pyright passed;
- the full default suite passed: 2,474 tests passed, 7 skipped, 72 deselected;
- total coverage was 90.77%; and
- no live or credit-consuming marker was enabled.

An unrelated temporary DOM-dump edit exists in the original checkout's
`src/gflow_cli/api/transports/mode_control.py`. It is user-owned, absent from this worktree, and
must not be copied, reverted, or committed here.

## Scope decisions

### In scope

1. Remove only the nonfunctional `gflow video batch` command and its orphaned implementation
   surface. Preserve `gflow image batch`.
2. Correct current documentation, agent instructions, help/schema expectations, and MCP parity.
3. Make mention-index failure explicit when a prompt actually contains mentions.
4. Correct recorder/repository resource ownership.
5. Add versioned queue payload decoding with legacy compatibility.
6. Make task claiming atomic across daemon and MCP execution.
7. Prevent blind retry after a possibly paid submission; reconcile by an authoritative Flow handle
   where empirical behavior supports it.
8. Add one cross-process profile lease at each persistent-browser ownership boundary.
9. Make partial setup, cancellation, daemon shutdown, and browser teardown exception-safe.
10. Make the image-driver and client/transport setup contracts honest and typed without a broad
    framework rewrite.
11. Resolve the packaged CDP research lifecycle by evidence: retain only a safe, owned production
    path; otherwise remove it while preserving Chrome discovery helpers.
12. Add focused cross-platform, packaging, offline, authenticated, and minimal-credit verification.

### Explicitly out of scope

- A command bus or a shared CLI/MCP/worker application framework. MCP already reuses the worker;
  forcing interactive CLI execution through the queue would add ceremony without solving the
  demonstrated races.
- A full `FlowSession` rewrite of `FlowApiClient`. Typed setup and explicit lifecycle ownership
  address the current action-at-a-distance without destabilizing all endpoint methods.
- Extracting a standalone `ReferenceAttacher` from the large video mixin. The review found size,
  not a demonstrated behavior defect. Moving shared picker behavior would require additional paid
  I2V/R2V cohort verification beyond the approved budget.
- Fixing Flow-side limitations such as the missing duration control, the full-page media-library
  A/B cohort, WAF heat, quota visibility, or metadata-sensitive JPEG rejection. These remain
  documented external constraints.
- Claiming production SLAs or official Google support. “Release-ready” means the documented matrix
  is green on this commit, not that the private upstream cannot change.

## Alternatives considered

### Full architectural rewrite

This would introduce `FlowSession`, a shared executor, a universal driver transaction, and a
reference-attacher service together. It was rejected because it combines independent failure modes
around paid, cohort-dependent browser paths and demands a much larger live regression matrix.

### Minimal cleanup

This would remove the dead command and documentation, fix mention/recorder ownership, and centralize
only in-process locks. It was rejected because two processes could still claim and execute the same
paid task, and cancellation could still leak browser ownership.

### Staged seam-first hardening

Chosen. Each slice is independently testable and committable. Demonstrated safety and correctness
problems are fixed; speculative abstractions remain rejected decisions rather than unfinished code.

## Design

### 1. Public truth: remove video batch, preserve image batch

`gflow video batch` currently parses as a command but always exits with a stub error. Remove the
Click command, callback, video TSV parser, parser-only tests, and CLI-parity exemption. There is no
MCP video-batch tool to remove.

Update every current operator surface that advertises the command, including README, AGENTS,
USAGE/USER_GUIDE, configuration/authentication/index/status/known-issues documentation, and the
installed `skills/gflow-cli/SKILL.md`. Historical changelog entries remain historical; add a current
`Removed` correction explaining that prior current-facing claims were inaccurate. Document simple
Bash and PowerShell sequential-loop alternatives.

Acceptance rules:

- `gflow video --help` does not list `batch`;
- `gflow video batch` exits with Click's usage error before profile resolution or Chrome launch;
- `gflow image batch --help` remains present;
- CLI/MCP parity is green; and
- existing image-batch unit/integration behavior remains unchanged.

### 2. Mention availability and recorder ownership

Mention-free prompts do not need an index and remain pass-through even if catalog sources are
unavailable. When a prompt contains an `@mention`, build the entity/media indexes from explicit
sources. Catch only expected data/API failures, retain the exception cause, and raise a stable typed
gflow error that identifies which source is unavailable without leaking prompt or catalog content.
An empty source is distinct from an unavailable source.

`OperationRecorder.open()` and the chain repository factory create and own their `DataStore`; their
`close()` methods close it. Instances constructed with an injected repository never close the
caller's store. Implement this with one private owned-store field, not a factory hierarchy.

### 3. Versioned queue codec

Keep the current top-level payload fields for rolling compatibility and add
`schema_version: 1`. A new codec maps `(task_type, payload)` to the existing typed image/video
request DTOs and validated execution options.

- A missing version is legacy V0 and decodes through an explicit compatibility path.
- Version 1 validates task discriminator, required fields, enums, paths, and bounded counts before
  Playwright starts.
- An unknown version becomes a stable RFC 9457 data/configuration failure; it is never interpreted
  optimistically.
- Encoding is centralized at enqueue sites; decoding is centralized at claim/execution.

Do not nest V1 fields under a new `request` object: older workers should ignore the additive version
key and retain their current field lookups during rolling upgrades.

### 4. Atomic claim and paid-execution state

The present `SELECT pending` followed later by `UPDATE processing` permits two processes to execute
one row. Replace it with repository operations that hold a SQLite immediate transaction while they:

1. select the oldest pending row (or a requested task ID);
2. decode and validate its payload;
3. mark an invalid row failed without opening Chrome; or
4. conditionally transition that exact row from `pending` to `processing` with claimant metadata.

Both daemon polling and MCP direct execution use this claim API. `process_task()` accepts only a
claimed task; it cannot mark an arbitrary pending task processing.

Extend the queue state machine:

```text
pending -> processing -> completed
                      -> failed
                      -> indeterminate
```

Add a versioned checkpoint document for claimant identity, execution phase, `may_have_spent`, and
observed Flow handles. Do not put prompt text or credentials in the checkpoint.

Execution phases are monotonic:

1. `claimed` — no browser submit gesture has been attempted;
2. `submit_attempted` — recorded immediately before the UI gesture that can spend credits;
3. `remote_started` — an authoritative operation/workflow/media handle was observed;
4. `terminal` — Flow reported success or failure.

Failure/cancellation policy:

- Before `submit_attempted`: mark failed with `may_have_spent=false`; a caller may enqueue a new
  task explicitly.
- After `submit_attempted` without a terminal result: never resubmit automatically.
- With an authoritative handle: attempt a handle-only reconciliation/poll path that cannot submit a
  new generation.
- Without a usable handle, or when handle-only polling is empirically impossible: mark
  `indeterminate` with `may_have_spent=true` and preserved non-secret identifiers.

The first queue implementation task is an empirical spike against the actual transport callbacks.
It must identify which image and video handles exist at each boundary and whether they support
handle-only reconciliation. The spike decides the smallest real checkpoint schema; it does not
assume a private API contract.

Startup recovery no longer changes every `processing` row to a generic failure. It classifies rows
by checkpoint: pre-submit rows become safe failures; remote-started rows reconcile when supported;
uncertain post-submit rows become indeterminate.

### 5. Profile lease and browser ownership

Create a small profile-lease module keyed by the canonical resolved profile directory. The lease
combines:

- one process-local async lock for tasks in the same interpreter; and
- a stable kernel advisory lock held by an open file handle: `msvcrt.locking` on Windows and
  `fcntl.flock` on POSIX.

The lock file lives under a dedicated gflow lock directory, contains at least one byte for Windows,
uses owner-only permissions where supported, and is never unlinked while held. PID, process start
time, profile name, and an owner token are diagnostic metadata only. The kernel lock is
authoritative; code never kills a process based only on metadata.

Contention is bounded and fail-fast. It preserves `ProfileLockedError` and exit code 11, with safe
wait/use-another-profile guidance. It does not silently wait through MCP or daemon shutdown.

Acquire the lease at every component that actually owns a persistent context:

- `FlowApiClient` when it launches/owns the context;
- standalone `UiAutomationTransport` setup;
- auth strategies and cookie/verification Playwright fallbacks; and
- any remaining experimental transport that launches a persistent context.

Do not acquire a second lease when a transport receives a context owned by its caller. MCP and
worker wrapper lock dictionaries are removed. The daemon does not hold a profile lease while idle;
each browser-owning task acquires it. The daemon's overwriteable lifetime `profile.lock` file is
removed.

### 6. Cancellation-safe lifecycle

Every partial-setup guard catches the cancellation boundary, completes bounded cleanup, and then
re-raises the original cancellation. Cleanup itself is shielded/bounded so one close failure cannot
skip later ownership release.

Required order:

1. stop accepting/claiming work;
2. cancel and await the worker;
3. persist task state/checkpoint;
4. close pages/context/browser and Playwright driver;
5. close stores owned by the component; and
6. release the profile lease.

The ASGI lifespan uses `try/finally`. The daemon E2E test never deletes a pre-existing lock and uses
a dynamically allocated port. Passive real-Chrome auth removes its duplicate Flow URL argument and
terminates/reaps the child process on cancellation.

### 7. Honest driver and transport setup contracts

Keep the two observation mechanisms explicit:

- classic mode attaches the response listener before clicking submit and parses the captured wire
  response in the transport; and
- agentic mode scrapes the UI with its existing attribution defenses.

Remove the classic driver's `await_images()` method that deliberately raises. Replace late
`driver._transport = self` mutation with a typed leaf dependency/callable. Pass the current image
request and expected count directly to agentic submission/await methods instead of storing pending
request state on a long-lived driver.

Replace client writes to transport-private output/storage fields with a typed setup/configuration
object or explicit public parameters. Preserve current lifecycle ownership: a client given a
preinitialized transport does not close resources it does not own.

This closes the demonstrated non-local state and misuse hazards without introducing a universal
driver transaction or moving the shared reference-picker implementation.

### 8. CDP decision gate

Chrome binary discovery and channel-availability helpers are production dependencies and remain.
The external-CDP lifecycle is research code with no production caller, an unauthenticated debug
port, ambiguous process ownership, and prior WAF failure evidence.

Run the cheapest safe decision process:

1. review the recorded PLAN/known-issue evidence and current production call graph;
2. if a dedicated expendable Chrome-strategy profile is available, run a zero-credit probe using a
   dynamic loopback port;
3. require positive executable/PID/start-time/profile identity, authenticated Flow DOM, explicit
   Playwright/browser ownership, and deterministic cleanup; and
4. require a concrete production consumer.

Do not capture cookies, tokens, HAR files, or full DOM dumps. Do not submit a CDP generation merely
to justify unused code. If any ownership/auth/cleanup gate fails, or no consumer exists, remove the
packaged CDP lifecycle and its production-only tests. Preserve useful research notes or a dev spike
outside the shipped package only when they remain truthful and safe.

### 9. Documentation corrections beyond video batch

Runtime requires headed real Chrome for production UI automation. Correct stale configuration and
environment-template claims that headless is the default or a reCAPTCHA remedy. Document:

- fail-fast same-profile contention and different-profile parallelism;
- queue V0/V1 compatibility and indeterminate paid outcomes;
- manual reconciliation guidance;
- the CDP decision and remaining Chrome architecture; and
- the exact live verification boundary.

## Verification design

### TDD and focused offline tests

Write failing tests before each implementation slice. Required coverage includes:

- video batch absent and image batch present;
- current documentation/skill command consistency;
- V0, V1, malformed, and unknown-version queue decoding;
- two-process claim contention and MCP-versus-daemon claiming;
- pre-submit cancellation, post-submit handle reconciliation, and indeterminate recovery;
- same-profile process contention, different-profile parallelism, crash release, and cancellation;
- daemon lifespan cleanup and dynamic-port E2E behavior;
- classic listener-before-click ordering and stateless agentic request handling;
- mention source empty versus unavailable; and
- injected versus factory-owned recorder stores.

Add a focused CI matrix for the real two-subprocess lease test on Windows, macOS, and Linux. Local
verification can execute the Windows leg. Remote macOS/Linux execution requires an explicitly
authorized push/PR and must not be reported as locally executed.

### Repository gates

Before every commit, follow `skills/check/SKILL.md`. Before completion, run the Impeccable Routine
in order, the complete default coverage sweep, focused new subprocess/integration tests, package
build/content checks, and a clean worktree check.

### Live matrix

Run serially and stop on auth expiry, WAF 403, quota exhaustion, or profile contention:

1. all zero-credit auth/health/schema probes;
2. credit-free daemon/MCP lifecycle, queue claim, and lease-release checks;
3. the real-handle/reconciliation observation;
4. one image generation chosen to cover the changed queue/worker boundary;
5. one cheapest stable T2V generation, without explicit duration; and
6. verify terminal state, image magic/dimensions, MP4 `ftyp`, local history/provenance, cookie DB
   health, lease reacquisition, and no gflow-owned Chrome process left behind.

Working image batch receives full offline regression and help/schema coverage. Under the approved
minimal budget, it does not receive a separate multi-credit live batch. If one paid image operation
can cover both a one-row image batch and the changed queue boundary without adding a second submit,
prefer that composition; otherwise prioritize the changed queue boundary.

## Evidence deliverable

Create a dated `docs/LIVE_VERIFICATION_*.md` containing:

- commit and environment versions;
- profile strategy and effective locale, without account secrets;
- exact commands and marker gates;
- timings, terminal statuses, artifact sizes and magic-byte checks;
- queue/checkpoint and lease-release evidence;
- CDP keep/remove decision;
- skipped or externally blocked paths with honest reasons; and
- the remaining known Flow-side limitations.

No “production-ready” claim is made if a required local gate fails, a changed paid path is skipped,
or a post-submit task cannot be classified without being recorded as indeterminate.

## Shipping sequence

1. Public truth cleanup and CDP call-graph/evidence decision.
2. Mention and recorder correctness.
3. Queue codec, empirical handle spike, atomic claim, and recovery states.
4. Profile lease and cancellation-safe lifecycle.
5. Driver/client setup contract cleanup.
6. Documentation, package checks, complete local gates, and serial live verification.

Each slice remains a separate commit so failures can be bisected and the overlapping open avatar PR
can rebase against narrow contracts rather than one monolithic rewrite.

## Appendix A: Empirical handle spike (Task C1)

This appendix records the code-archaeology behind the C1 submit-boundary seam: which Flow handles
exist at each generation boundary, when they first become observable, and whether they support
**handle-only reconciliation** (learning an in-flight generation's fate WITHOUT a new credit-spending
submit). Conclusions are marked **[code-derived]** (proven from the transport source cited) or
**[needs F1]** (a hypothesis requiring live confirmation in the approved F1 matrix — no live runs were
done in C1).

### A.1 The seam (as built)

`GenerationCheckpoint` (`src/gflow_cli/api/dto.py`) is a frozen dataclass carrying only
`phase` + handle identifiers (`operation_id`, `media_ids`, `workflow_ids`) — never prompts, headers,
cookies, or signed URLs. `GenerationCheckpointObserver = Callable[[GenerationCheckpoint], None]` is an
optional, default-`None` sync callback on `FlowApiClient.generate_image/generate_images_batch/generate_video`.
A `None` observer is zero behaviour change. Emission sites:

- **Image** — `client._drive_images_generation` (the single mint/submit funnel for both image entry
  points): `submit_attempted` immediately before `self.transport.generate_images(...)`
  (`src/gflow_cli/api/client.py`), then `remote_started` after the returned images are validated.
- **Video** — `client.generate_video`: `submit_attempted` before `self.transport.generate_video(...)`,
  and `remote_started` by wrapping the existing `on_started` hook so the handle is relayed the moment
  the transport parses it.

Phase granularity is **client-boundary**, not exact-gesture: `submit_attempted` fires just before the
client hands off to the transport call that performs the credit-spending gesture. This is conservative
(strictly earlier than the gesture) and correct for checkpoint semantics — intent is recorded before
any UI is driven. Tightening to the exact submit click would require threading the observer through the
transport ABC + classic/agentic/video drivers; deferred as unjustified for C1. **[code-derived]**

### A.2 Handle inventory

| Boundary | Handle that exists | First observable | Source (file:line) |
|---|---|---|---|
| Video submit (veo-*) | operation name (`operations[0].operation.name`) **and** media_id | In the `batchAsyncGenerateVideo*` generate response, parsed right after submit, before polling | `ui_automation_video.py:3086` (`operation_name_from_generate_response`), `:3283-3288` (fires `on_started`); `video.py:403-411,436` |
| Video submit (omni-flash) | media_id only — operation name is **NULL** (response omits it) | Same point; `flow_operation_id` comes back `None` | `ui_automation_video.py:3085-3087` (`str \| None`); `docs/CONFIGURATION.md:335` (omni-flash caveats) |
| Video status | Flow's own `batchCheckAsyncVideoGenerationStatus` responses (captured passively) | Only while a live page is polling — `page.on('response')` scrape | `ui_automation_video.py:9-10,81,921-940` |
| Image submit (classic) | media UUID + workflow UUID per generated image | Only AFTER submit, when the `batchGenerateImages` response is drained | `drivers/base.py:94-100`; `dto.py` `GeneratedImage.from_response` |
| Image submit (agentic) | media UUID (distinct-UUID DOM scrape; page network capture is dead in that cohort) | Only AFTER submit, from the rendered tiles | `drivers/base.py:96-100` |

### A.3 Can handle-only reconciliation work?

**Video — partial, by page re-entry only.** The status endpoint `batchCheckAsyncVideoGenerationStatus`
returns **HTTP 401 to `page.request.post`** even from the authenticated page
(`ui_automation_video.py:9-10`), so the tool can never turn `operation_id` into a status via a direct
REST call. The only credit-free way to check an operation's fate is to **re-enter the project page** in
an authenticated browser and let Flow's SPA re-poll / render the finished media, then read the rendered
tile (media UUID) or the re-captured status traffic. Therefore:
- With a persisted `operation_id` (or `media_id`) **and** the project, a dead-process recovery can
  reconcile by page re-entry — **no new submit, no credit spend**. **[needs F1]** (must confirm the
  finished tile / status is actually re-readable after the original page is gone).
- With **no** live page and **no** handle (crash before `remote_started`), the operation's fate is
  necessarily **`indeterminate`** — C5 must treat this as "may have spent a credit," not "safe to
  retry." **[code-derived]** from the 401 + passive-capture design.
- omni-flash has no `operation_id`; reconciliation must fall back to `media_id` + project scraping.

**Image — reconciliation is library existence, not status.** There is no async operation handle for
images; the media/workflow UUIDs exist only once the response/DOM is read post-submit. A dead image
process is reconciled by checking whether the media UUID is present in the project library (page
re-entry / library scrape). A crash before the handle is observed leaves **no handle → `indeterminate`**,
but image generation (t2i/i2i) is credit-free, so the stakes are low. **[code-derived]** for the
handle timing; **[needs F1]** for library-existence readback.

### A.4 What the checkpoint schema therefore needs (informs C3/C5)

- Persist `phase` + `operation_id` (nullable) + `media_ids` + `workflow_ids` — already carried by
  `GenerationCheckpoint`.
- **Add `project_id` in C3.** Reconciliation-by-page-re-entry is the ONLY credit-free recovery path for
  video (and image), and it requires the project id to re-open the project. C1 deliberately kept the
  seam to the operation/workflow/media identifiers named in §4; C3's persisted checkpoint row must add
  `project_id`. **[code-derived]** from A.3.
- C5 recovery states map directly: `submit_attempted` with no later `remote_started` on a dead process
  → **`indeterminate`** (may have spent credits); `remote_started` with a handle → reconcile by page
  re-entry before any retry. A retry that re-submits without reconciling risks double-spend on video.
- No new REST status capability is available (401 wall), so C5 must NOT assume a headless
  handle→status call exists. **[code-derived]**
